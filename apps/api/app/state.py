"""Application state: a JSON-backed transaction ledger plus price store.

State is persisted to a local JSON file so the dashboard survives restarts with
no external database. Access is guarded by a lock for thread safety under the
ASGI server's threadpool.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import List

from .config import (
    DEFAULT_UK_PERP_TREATMENT,
    DEFAULT_US_PERP_TREATMENT,
    SUPPORTED_PERP_TREATMENTS,
    SUPPORTED_TAX_JURISDICTIONS,
    TAX_JURISDICTION,
)
from .pricing import PriceStore
from .sample_data import default_transactions, without_sample
from .schemas import ManualCostBasisOverride, Transaction
from .ledger_filters import strip_dust_transactions
from .transaction_dedup import dedupe_transactions

def _default_state_dir() -> Path:
    # apps/api/app/state.py -> repo root is three levels up from app/
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "data"


STATE_DIR = Path(os.environ.get("CRYPTO_TAX_STATE_DIR", _default_state_dir()))
STATE_FILE = STATE_DIR / "ledger.json"
SETTINGS_FILE = STATE_DIR / "settings.json"
OVERRIDES_FILE = STATE_DIR / "cost_basis_overrides.json"


class AppState:
    """Holds the in-memory ledger and persists it to disk."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._transactions: List[Transaction] = []
        self._cost_basis_overrides: List[ManualCostBasisOverride] = []
        self._tax_jurisdiction = TAX_JURISDICTION.upper()
        self._data_mode: str | None = None
        self._perp_treatment = {
            "UK": DEFAULT_UK_PERP_TREATMENT,
            "US": DEFAULT_US_PERP_TREATMENT,
        }
        self.prices = PriceStore()
        self._load_settings()
        self._load_overrides()
        self._load()

    # --- settings ----------------------------------------------------------

    def _load_settings(self) -> None:
        if SETTINGS_FILE.exists():
            try:
                raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                jurisdiction = str(raw.get("tax_jurisdiction", TAX_JURISDICTION)).upper()
                if jurisdiction in SUPPORTED_TAX_JURISDICTIONS:
                    self._tax_jurisdiction = jurisdiction
                for code in ("UK", "US"):
                    value = str(raw.get(f"{code.lower()}_perp_treatment", "")).lower()
                    if value in SUPPORTED_PERP_TREATMENTS:
                        self._perp_treatment[code] = value
                mode = str(raw.get("data_mode", "")).lower()
                if mode in ("live", "demo"):
                    self._data_mode = mode
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

    def _persist_settings(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        mode = self._data_mode if self._data_mode else self._infer_data_mode()
        SETTINGS_FILE.write_text(
            json.dumps(
                {
                    "tax_jurisdiction": self._tax_jurisdiction,
                    "uk_perp_treatment": self._perp_treatment["UK"],
                    "us_perp_treatment": self._perp_treatment["US"],
                    "data_mode": mode,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _infer_data_mode(self) -> str:
        live_count = len(without_sample(self._transactions))
        return "live" if live_count > 0 else "demo"

    def data_mode(self) -> str:
        with self._lock:
            if self._data_mode:
                return self._data_mode
            return self._infer_data_mode()

    def set_data_mode(self, mode: str) -> str:
        value = mode.strip().lower()
        if value not in ("live", "demo"):
            raise ValueError(f"Unsupported data mode: {mode}")
        with self._lock:
            self._data_mode = value
            self._persist_settings()
            return self._data_mode

    def tax_jurisdiction(self) -> str:
        with self._lock:
            return self._tax_jurisdiction

    def set_tax_jurisdiction(self, jurisdiction: str) -> str:
        code = jurisdiction.strip().upper()
        if code not in SUPPORTED_TAX_JURISDICTIONS:
            raise ValueError(f"Unsupported tax jurisdiction: {jurisdiction}")
        with self._lock:
            self._tax_jurisdiction = code
            self._persist_settings()
            return self._tax_jurisdiction

    def perp_treatment(self, jurisdiction: str | None = None) -> str:
        with self._lock:
            code = (jurisdiction or self._tax_jurisdiction).strip().upper()
            return self._perp_treatment.get(code, "income")

    def set_perp_treatment(self, jurisdiction: str, treatment: str) -> str:
        code = jurisdiction.strip().upper()
        if code not in SUPPORTED_TAX_JURISDICTIONS:
            raise ValueError(f"Unsupported tax jurisdiction: {jurisdiction}")
        value = treatment.strip().lower()
        if value not in SUPPORTED_PERP_TREATMENTS:
            raise ValueError(f"Unsupported perp treatment: {treatment}")
        with self._lock:
            self._perp_treatment[code] = value
            self._persist_settings()
            return value

    def _load_overrides(self) -> None:
        if not OVERRIDES_FILE.exists():
            self._cost_basis_overrides = []
            return
        try:
            raw = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
            self._cost_basis_overrides = [
                ManualCostBasisOverride(**row) for row in raw
            ]
        except (json.JSONDecodeError, ValueError, TypeError):
            self._cost_basis_overrides = []

    def _persist_overrides(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = [
            json.loads(o.model_dump_json()) for o in self._cost_basis_overrides
        ]
        OVERRIDES_FILE.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )

    def cost_basis_overrides(self) -> List[ManualCostBasisOverride]:
        with self._lock:
            return list(self._cost_basis_overrides)

    def upsert_cost_basis_override(
        self, override: ManualCostBasisOverride
    ) -> ManualCostBasisOverride:
        with self._lock:
            self._cost_basis_overrides = [
                o
                for o in self._cost_basis_overrides
                if o.anchor_transaction_id != override.anchor_transaction_id
            ]
            self._cost_basis_overrides.append(override)
            self._persist_overrides()
            return override

    def delete_cost_basis_override(self, anchor_transaction_id: str) -> bool:
        with self._lock:
            before = len(self._cost_basis_overrides)
            self._cost_basis_overrides = [
                o
                for o in self._cost_basis_overrides
                if o.anchor_transaction_id != anchor_transaction_id
            ]
            changed = len(self._cost_basis_overrides) != before
            if changed:
                self._persist_overrides()
            return changed

    # --- persistence -------------------------------------------------------

    def _load(self) -> None:
        if STATE_FILE.exists():
            try:
                raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                txs = [Transaction(**row) for row in raw]
                deduped, stats = dedupe_transactions(txs)
                cleaned, dust_removed = strip_dust_transactions(deduped)
                self._transactions = cleaned
                if (
                    stats["skipped_id"]
                    or stats["skipped_fingerprint"]
                    or stats["skipped_on_chain"]
                    or dust_removed
                ):
                    self._persist()
                return
            except (json.JSONDecodeError, ValueError, TypeError):
                # Corrupt state file: fall back to seed data.
                pass
        self._transactions = default_transactions()
        self._persist()

    def _persist(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = [json.loads(t.model_dump_json()) for t in self._transactions]
        STATE_FILE.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )

    # --- ledger access -----------------------------------------------------

    def transactions(self) -> List[Transaction]:
        with self._lock:
            return list(self._transactions)

    def replace_all(self, transactions: List[Transaction]) -> None:
        with self._lock:
            self._transactions = list(transactions)
            self._persist()

    def add_many(self, transactions: List[Transaction]) -> int:
        with self._lock:
            self._transactions.extend(transactions)
            self._persist()
            return len(transactions)

    def add_one(self, transaction: Transaction) -> None:
        with self._lock:
            self._transactions.append(transaction)
            self._persist()

    def delete(self, transaction_id: str) -> bool:
        with self._lock:
            before = len(self._transactions)
            self._transactions = [
                t for t in self._transactions if t.id != transaction_id
            ]
            changed = len(self._transactions) != before
            if changed:
                self._persist()
            return changed

    def reset_to_sample(self) -> None:
        with self._lock:
            self._transactions = default_transactions()
            self._persist()


# Module-level singleton used by the API layer.
state = AppState()
