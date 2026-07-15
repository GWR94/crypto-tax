"""Ledger backup download and pre-reset local backup."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.schemas import Transaction, TransactionType
from app import state as state_mod
from app.state import state


def _tx(tx_id: str) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
        asset="SOL",
        transaction_type=TransactionType.BUY,
        amount=1.0,
        fiat_value_at_trigger=100.0,
        fee_fiat=0.0,
        fiat_currency="GBP",
        source="test",
    )


def test_build_backup_payload_includes_transactions():
    prior = state.transactions()
    try:
        state.replace_all([_tx("backup-sol")])
        payload = state.build_backup_payload()
        assert payload["format"] == "crypto-tax-ledger-backup"
        assert payload["version"] == 1
        assert payload["transaction_count"] == 1
        assert payload["transactions"][0]["id"] == "backup-sol"
        assert isinstance(payload["cost_basis_overrides"], list)
    finally:
        state.replace_all(prior)


def test_reset_writes_local_bak(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state_mod, "STATE_FILE", tmp_path / "ledger.json")
    monkeypatch.setattr(
        state_mod, "OVERRIDES_FILE", tmp_path / "cost_basis_overrides.json"
    )

    prior = state.transactions()
    try:
        state.replace_all([_tx("live-before-reset")])
        bak = state.reset_to_sample(backup=True)
        assert bak is not None
        assert bak.exists()
        raw = json.loads(bak.read_text(encoding="utf-8"))
        assert any(row.get("id") == "live-before-reset" for row in raw)
        assert not any(t.id == "live-before-reset" for t in state.transactions())
    finally:
        state.replace_all(prior)


def test_backup_json_is_reimportable():
    from app.ingestion import parse_json

    prior = state.transactions()
    try:
        state.replace_all([_tx("reimport-me")])
        payload = state.build_backup_payload()
        restored = parse_json(json.dumps(payload))
        assert len(restored) == 1
        assert restored[0].id == "reimport-me"
        assert restored[0].asset == "SOL"
    finally:
        state.replace_all(prior)
