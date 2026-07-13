"""Tracks CSV and wallet import batches for per-source disconnect."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import List, Literal, Optional

from pydantic import BaseModel

from .import_file_storage import clear_import_files, remove_import_file
from .import_reconcile import infer_orphan_import_metadata, orphan_imported_at
from .schemas import Transaction
from .ingestion import transaction_date_range

CoverageFrom = Literal["export_filter", "transactions"]


def _default_state_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "data"


STATE_DIR = Path(os.environ.get("CRYPTO_TAX_STATE_DIR", _default_state_dir()))
IMPORTS_FILE = STATE_DIR / "imports.json"


class ImportSource(BaseModel):
    """Metadata for one import batch (one CSV file or one wallet fetch)."""

    id: str
    kind: Literal["csv", "wallet"]
    label: str
    chain: Optional[str] = None
    address: Optional[str] = None
    imported_at: datetime
    coverage_start: Optional[datetime] = None
    coverage_end: Optional[datetime] = None
    data_start: Optional[datetime] = None
    data_end: Optional[datetime] = None
    coverage_from: CoverageFrom = "transactions"


class ImportRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sources: List[ImportSource] = []
        self._load()

    def _load(self) -> None:
        if not IMPORTS_FILE.exists():
            self._sources = []
            return
        try:
            raw = json.loads(IMPORTS_FILE.read_text(encoding="utf-8"))
            self._sources = [ImportSource(**row) for row in raw]
        except (json.JSONDecodeError, ValueError, TypeError):
            self._sources = []

    def _persist(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = [json.loads(s.model_dump_json()) for s in self._sources]
        IMPORTS_FILE.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )

    def all(self) -> List[ImportSource]:
        with self._lock:
            return list(self._sources)

    def clear(self) -> None:
        with self._lock:
            self._sources = []
            self._persist()
        clear_import_files()

    def register(
        self,
        kind: Literal["csv", "wallet"],
        label: str,
        *,
        chain: Optional[str] = None,
        address: Optional[str] = None,
        coverage_start: Optional[datetime] = None,
        coverage_end: Optional[datetime] = None,
        data_start: Optional[datetime] = None,
        data_end: Optional[datetime] = None,
        coverage_from: CoverageFrom = "transactions",
    ) -> str:
        source = ImportSource(
            id=uuid.uuid4().hex,
            kind=kind,
            label=label,
            chain=chain,
            address=address,
            imported_at=datetime.now(timezone.utc),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            data_start=data_start,
            data_end=data_end,
            coverage_from=coverage_from,
        )
        with self._lock:
            self._sources.append(source)
            self._persist()
        return source.id

    def remove(self, import_id: str) -> bool:
        with self._lock:
            before = len(self._sources)
            self._sources = [s for s in self._sources if s.id != import_id]
            changed = len(self._sources) != before
            if changed:
                self._persist()
        if changed:
            remove_import_file(import_id)
        return changed

    def update_label(self, import_id: str, label: str) -> bool:
        with self._lock:
            for index, source in enumerate(self._sources):
                if source.id != import_id:
                    continue
                self._sources[index] = source.model_copy(update={"label": label})
                self._persist()
                return True
            return False

    def reconcile_orphans(self, transactions: List[Transaction]) -> int:
        """Re-register import batches that still have ledger rows but no registry entry."""
        registry_ids = {source.id for source in self._sources}
        orphan_groups: dict[str, list[Transaction]] = {}
        for tx in transactions:
            if tx.import_id and tx.import_id not in registry_ids:
                orphan_groups.setdefault(tx.import_id, []).append(tx)

        if not orphan_groups:
            return 0

        recovered: list[ImportSource] = []
        for import_id, import_txs in sorted(orphan_groups.items()):
            kind, label, chain, address = infer_orphan_import_metadata(import_txs)
            data_start, data_end = transaction_date_range(import_txs)
            recovered.append(
                ImportSource(
                    id=import_id,
                    kind=kind,
                    label=label,
                    chain=chain,
                    address=address,
                    imported_at=orphan_imported_at(import_txs),
                    coverage_start=data_start,
                    coverage_end=data_end,
                    data_start=data_start,
                    data_end=data_end,
                    coverage_from="transactions",
                )
            )

        with self._lock:
            self._sources.extend(recovered)
            self._persist()
        return len(recovered)


registry = ImportRegistry()
