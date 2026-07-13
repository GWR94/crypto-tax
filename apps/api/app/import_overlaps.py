"""Detect overlapping imports and duplicate transactions across sources."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from .export_coverage import as_coverage_date
from .csv_export_kind import export_kind_label
from .schemas import ImportSourceView, Transaction
from .transaction_dedup import Fingerprint, dedup_keys_for_transaction, transaction_fingerprint

MIN_OVERLAP_DAYS = 1

# On-chain wallet labels — overlapping CSV exports are usually complementary slices.
CHAIN_PARSER_LABELS = frozenset(
    {"Solana", "Ethereum", "Bitcoin", "Cardano", "Celestia"}
)


def _coverage_window(
    source: ImportSourceView,
) -> Optional[Tuple[datetime, datetime]]:
    if source.kind == "demo":
        return None
    start = source.coverage_start or source.date_start
    end = source.coverage_end or source.date_end
    if not start or not end:
        return None
    return start, end


def _overlap_window(
    left: Tuple[datetime, datetime],
    right: Tuple[datetime, datetime],
) -> Optional[Tuple[datetime, datetime]]:
    start = max(left[0], right[0])
    end = min(left[1], right[1])
    if as_coverage_date(start) > as_coverage_date(end):
        return None
    return start, end


def _overlap_days(start: datetime, end: datetime) -> int:
    return (
        as_coverage_date(end) - as_coverage_date(start)
    ).days + 1


def _normalize_address(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _same_wallet(left: ImportSourceView, right: ImportSourceView) -> bool:
    left_addr = _normalize_address(left.address)
    right_addr = _normalize_address(right.address)
    if not left_addr or not right_addr:
        return False
    return left_addr == right_addr



def _csv_export_kind(source: ImportSourceView) -> Optional[str]:
    return getattr(source, "export_kind", None)


def _csv_pair_label(source: ImportSourceView) -> str:
    kind = export_kind_label(_csv_export_kind(source))
    if kind and source.parser_label:
        return f"{source.parser_label} {kind}"
    return source.label


def _likely_duplicate_coverage(
    left: ImportSourceView, right: ImportSourceView
) -> bool:
    """Whether overlapping dates between two imports suggest a re-import.

    Different wallets, exchanges, or chains active in the same period is normal.
    Only flag when the imports plausibly describe the same underlying source.
    """
    if left.kind == "wallet" and right.kind == "wallet":
        return _same_wallet(left, right)

    if left.kind == "csv" and right.kind == "csv":
        if left.label.strip().lower() == right.label.strip().lower():
            return True
        if not left.parser_label or left.parser_label != right.parser_label:
            return False
        if left.parser_label in CHAIN_PARSER_LABELS:
            return False
        left_kind = _csv_export_kind(left)
        right_kind = _csv_export_kind(right)
        if left_kind and right_kind:
            return left_kind == right_kind
        # Same exchange but unknown export slice — still warn on overlap.
        return True

    return False


def _describe_pair(labels: List[str]) -> str:
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels)


def _fingerprints_by_import(
    transactions: List[Transaction],
) -> Dict[str, Set[Fingerprint]]:
    grouped: Dict[str, Set[Fingerprint]] = {}
    for tx in transactions:
        if not tx.import_id:
            continue
        grouped.setdefault(tx.import_id, set()).update(
            dedup_keys_for_transaction(tx)
        )
    return grouped


def ledger_dedup_keys(
    transactions: List[Transaction],
) -> Tuple[Set[str], Set[Fingerprint]]:
    """Transaction ids and content fingerprints already present in the ledger."""
    fingerprints: Set[Fingerprint] = set()
    for tx in transactions:
        fingerprints.update(dedup_keys_for_transaction(tx))
    return (
        {tx.id for tx in transactions},
        fingerprints,
    )


def is_known_transaction(
    tx: Transaction,
    *,
    known_ids: Set[str],
    known_fingerprints: Set[Fingerprint],
) -> bool:
    """True when ``tx`` matches an id or fingerprint already in the ledger."""
    if tx.id in known_ids:
        return True
    return any(fp in known_fingerprints for fp in dedup_keys_for_transaction(tx))


def partition_novel_transactions(
    batch: List[Transaction],
    *,
    known_ids: Set[str],
    known_fingerprints: Set[Fingerprint],
) -> Tuple[List[Transaction], int]:
    """Split ``batch`` into novel rows and a duplicate count."""
    novel: List[Transaction] = []
    duplicate_count = 0
    for tx in batch:
        if is_known_transaction(
            tx, known_ids=known_ids, known_fingerprints=known_fingerprints
        ):
            duplicate_count += 1
            continue
        novel.append(tx)
        known_ids.add(tx.id)
        known_fingerprints.update(dedup_keys_for_transaction(tx))
    return novel, duplicate_count


def format_fully_duplicate_rejection(
    filename: str,
    transaction_count: int,
    *,
    ledger_labels: List[str],
    same_upload: bool = False,
) -> str:
    """Human-readable error when an import would add no new transactions."""
    if same_upload:
        return (
            f"{filename}: all {transaction_count} transaction(s) duplicate another "
            "file in this upload — only import it once"
        )
    if ledger_labels:
        quoted = ", ".join(f'"{label}"' for label in ledger_labels)
        return (
            f"{filename}: all {transaction_count} transaction(s) already in your "
            f"ledger (from {quoted})"
        )
    return (
        f"{filename}: all {transaction_count} transaction(s) already in your ledger"
    )


def count_ledger_duplicates(
    batch: List[Transaction],
    existing: List[Transaction],
) -> Tuple[int, Dict[str, int]]:
    """Count rows in ``batch`` whose fingerprint already exists in ``existing``."""
    existing_by_fp: Dict[Fingerprint, str] = {}
    for tx in existing:
        if not tx.import_id:
            continue
        for fp in dedup_keys_for_transaction(tx):
            existing_by_fp[fp] = tx.import_id

    duplicate_count = 0
    matching_imports: Dict[str, int] = {}
    for tx in batch:
        import_id = None
        for fp in dedup_keys_for_transaction(tx):
            import_id = existing_by_fp.get(fp)
            if import_id:
                break
        if not import_id:
            continue
        duplicate_count += 1
        matching_imports[import_id] = matching_imports.get(import_id, 0) + 1
    return duplicate_count, matching_imports


def find_import_overlaps(
    sources: List[ImportSourceView],
    transactions: List[Transaction],
    *,
    min_overlap_days: int = MIN_OVERLAP_DAYS,
) -> List[Dict[str, object]]:
    """Overlapping coverage windows and redundant re-imports across sources."""
    eligible = [source for source in sources if source.kind not in ("demo",)]
    fps_by_import = _fingerprints_by_import(transactions)
    overlaps: List[Dict[str, object]] = []

    redundant_by_label: Dict[str, List[ImportSourceView]] = {}
    for redundant in eligible:
        if redundant.transaction_count > 0:
            continue
        if redundant.kind == "legacy":
            continue
        redundant_by_label.setdefault(redundant.label, []).append(redundant)

    for label, group in sorted(redundant_by_label.items()):
        duplicate_count = len(group)
        parser_label = group[0].parser_label
        import_ids = [source.id for source in group]
        if duplicate_count == 1:
            message = (
                f'"{label}" added no new transactions — likely a duplicate of '
                "data already in your ledger. You can disconnect it."
            )
        else:
            message = (
                f'"{label}" was imported {duplicate_count} times with no new '
                "transactions — likely repeat uploads of data already in your "
                "ledger. You can disconnect the redundant copies."
            )
        overlaps.append(
            {
                "kind": "redundant_import",
                "import_ids": import_ids,
                "import_labels": [label],
                "parser_label": parser_label,
                "overlap_start": group[0].coverage_start or group[0].date_start,
                "overlap_end": group[0].coverage_end or group[0].date_end,
                "overlap_days": 0,
                "shared_transactions": 0,
                "same_platform": False,
                "duplicate_count": duplicate_count,
                "message": message,
            }
        )

    active = [source for source in eligible if source.transaction_count > 0]
    seen_pairs: Set[Tuple[str, str]] = set()
    for index, left in enumerate(active):
        left_window = _coverage_window(left)
        if not left_window:
            continue
        for right in active[index + 1 :]:
            pair_key = tuple(sorted((left.id, right.id)))
            if pair_key in seen_pairs:
                continue
            right_window = _coverage_window(right)
            if not right_window:
                continue
            overlap = _overlap_window(left_window, right_window)
            if not overlap:
                continue
            overlap_days = _overlap_days(overlap[0], overlap[1])
            if overlap_days < min_overlap_days:
                continue
            seen_pairs.add(pair_key)  # type: ignore[arg-type]

            left_fps = fps_by_import.get(left.id, set())
            right_fps = fps_by_import.get(right.id, set())
            shared = len(left_fps & right_fps) if left_fps and right_fps else 0
            if shared == 0 and not _likely_duplicate_coverage(left, right):
                continue

            same_platform = _likely_duplicate_coverage(left, right) or shared > 0

            labels = [left.label, right.label]
            parser_label = left.parser_label if same_platform else None
            range_text = (
                f"{as_coverage_date(overlap[0]).isoformat()} → "
                f"{as_coverage_date(overlap[1]).isoformat()}"
            )

            if shared:
                detail = f"{shared} transaction(s) appear in both imports"
                message = (
                    f"{_describe_pair(labels)} overlap for {overlap_days} day(s) "
                    f"({range_text}); {detail}."
                )
            elif left.kind == "wallet" and right.kind == "wallet":
                message = (
                    f"{_describe_pair(labels)} cover the same wallet address for "
                    f"{overlap_days} day(s) ({range_text}) — you may have imported "
                    "it twice."
                )
            elif same_platform and left.parser_label:
                left_desc = _csv_pair_label(left)
                right_desc = _csv_pair_label(right)
                message = (
                    f"{left_desc} and {right_desc} overlap for {overlap_days} "
                    f"day(s) ({range_text}) — you may have imported the same "
                    "export twice."
                )
            else:
                message = (
                    f"{_describe_pair(labels)} overlap for {overlap_days} day(s) "
                    f"({range_text})."
                )

            overlaps.append(
                {
                    "kind": "coverage",
                    "import_ids": [left.id, right.id],
                    "import_labels": labels,
                    "parser_label": parser_label,
                    "overlap_start": overlap[0],
                    "overlap_end": overlap[1],
                    "overlap_days": overlap_days,
                    "shared_transactions": shared,
                    "same_platform": same_platform,
                    "duplicate_count": 1,
                    "message": message,
                }
            )

    overlaps.sort(
        key=lambda row: (
            0 if row["kind"] == "coverage" and row["same_platform"] else 1,
            -int(row["duplicate_count"]),  # type: ignore[arg-type]
            -int(row["overlap_days"]),  # type: ignore[arg-type]
            str(row["import_labels"][0]),
        )
    )
    return overlaps
