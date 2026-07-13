"""Detect gaps between import coverage windows across the whole ledger."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Literal, Optional, Tuple

from .export_coverage import as_coverage_date
from .schemas import ImportSourceView

GapKind = Literal["ledger", "preview"]

# Gap between one import ending and another starting (calendar days).
MIN_GAP_DAYS = 7


def _coverage_window(source: ImportSourceView) -> Optional[Tuple[datetime, datetime]]:
    if source.kind == "demo":
        return None
    start = source.coverage_start or source.date_start
    end = source.coverage_end or source.date_end
    if not start or not end:
        return None
    return start, end


def _interval_dict(
    source: ImportSourceView,
    start: datetime,
    end: datetime,
) -> Dict[str, object]:
    return {
        "start": start,
        "end": end,
        "import_id": source.id,
        "label": source.label,
        "parser_label": source.parser_label or source.label,
    }


def _describe_imports(imports: List[Dict[str, object]]) -> str:
    labels: List[str] = []
    for item in imports:
        parser = str(item.get("parser_label") or "")
        name = str(item.get("label") or "")
        text = f"{name} ({parser})" if parser and parser != name else name
        if text not in labels:
            labels.append(text)
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def _merge_coverage_intervals(
    intervals: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    if not intervals:
        return []

    ordered = sorted(intervals, key=lambda row: row["start"])  # type: ignore[arg-type, return-value]
    merged: List[Dict[str, object]] = [
        {
            "start": ordered[0]["start"],
            "end": ordered[0]["end"],
            "imports": [ordered[0]],
        }
    ]

    for interval in ordered[1:]:
        last = merged[-1]
        start_gap = (
            as_coverage_date(interval["start"])  # type: ignore[arg-type]
            - as_coverage_date(last["end"])  # type: ignore[arg-type]
        ).days
        if start_gap <= 1:
            if interval["end"] > last["end"]:  # type: ignore[operator]
                last["end"] = interval["end"]
            last["imports"].append(interval)  # type: ignore[attr-defined]
        else:
            merged.append(
                {
                    "start": interval["start"],
                    "end": interval["end"],
                    "imports": [interval],
                }
            )
    return merged


def _gap_days_between(end: datetime, start: datetime) -> int:
    return max(0, (as_coverage_date(start) - as_coverage_date(end)).days)


def _format_gap_range(start: datetime, end: datetime) -> str:
    start_text = as_coverage_date(start).isoformat()
    end_text = as_coverage_date(end).isoformat()
    if start_text == end_text:
        return start_text
    return f"{start_text} → {end_text}"


def find_ledger_coverage_gaps(
    sources: List[ImportSourceView],
    *,
    min_gap_days: int = MIN_GAP_DAYS,
) -> List[Dict[str, object]]:
    """Gaps where no import's coverage window overlaps the period."""
    intervals: List[Dict[str, object]] = []
    for source in sources:
        window = _coverage_window(source)
        if not window:
            continue
        start, end = window
        intervals.append(_interval_dict(source, start, end))

    merged = _merge_coverage_intervals(intervals)
    if len(merged) < 2:
        return []

    gaps: List[Dict[str, object]] = []
    for index in range(len(merged) - 1):
        left, right = merged[index], merged[index + 1]
        days = _gap_days_between(left["end"], right["start"])  # type: ignore[arg-type]
        if days < min_gap_days:
            continue
        before = _describe_imports(left["imports"])  # type: ignore[arg-type]
        after = _describe_imports(right["imports"])  # type: ignore[arg-type]
        import_ids: List[str] = []
        import_labels: List[str] = []
        for block in (left["imports"], right["imports"]):  # type: ignore[assignment]
            for item in block:  # type: ignore[union-attr]
                import_ids.append(str(item["import_id"]))
                import_labels.append(str(item["label"]))

        gaps.append(
            {
                "kind": "ledger",
                "source_label": "Ledger",
                "source_slug": None,
                "gap_start": left["end"],
                "gap_end": right["start"],
                "gap_days": days,
                "import_ids": import_ids,
                "import_labels": import_labels,
                "message": (
                    f"No import covers {days} days between "
                    f"{before} and {after} "
                    f"({_format_gap_range(left['end'], right['start'])})"  # type: ignore[index]
                ),
            }
        )
    return gaps


def find_preview_ledger_gaps(
    *,
    coverage_start: Optional[datetime],
    coverage_end: Optional[datetime],
    label: str,
    parser_label: Optional[str],
    sources: List[ImportSourceView],
    min_gap_days: int = MIN_GAP_DAYS,
) -> List[Dict[str, object]]:
    """Gaps that would remain (or appear) when adding a previewed file."""
    if not coverage_start or not coverage_end:
        return []

    preview_source = ImportSourceView(
        id="preview",
        kind="csv",
        label=label,
        parser_label=parser_label or label,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        date_start=coverage_start,
        date_end=coverage_end,
        transaction_count=0,
    )
    return find_ledger_coverage_gaps([*sources, preview_source], min_gap_days=min_gap_days)


def find_coverage_gaps(
    sources: List[ImportSourceView],
    *,
    min_gap_days: int = MIN_GAP_DAYS,
) -> List[Dict[str, object]]:
    return find_ledger_coverage_gaps(sources, min_gap_days=min_gap_days)
