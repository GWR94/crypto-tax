"""Hard-fork acquisition normalization."""

from datetime import datetime, timezone

from app.fork_normalize import EVENT_HARD_FORK, normalize_hard_forks
from app.ledger_normalize import normalize_tax_ledger
from app.schemas import Transaction, TransactionType


def _eth_buy(amount: float = 2.0, when: str = "2021-06-01T00:00:00") -> Transaction:
    return Transaction(
        id=f"eth-buy-{when[:10]}",
        timestamp=datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
        asset="ETH",
        transaction_type=TransactionType.BUY,
        amount=amount,
        fiat_value_at_trigger=amount * 2000.0,
        fee_fiat=0.0,
        fiat_currency="GBP",
        source="coinbase",
    )


def test_eth_ethw_fork_zero_basis():
    txs, n = normalize_hard_forks(
        [_eth_buy()],
        basis_policy="zero",
        fork_events={"ETHW": {"parent": "ETH", "date": "2022-09-15", "ratio": 1.0}},
    )
    assert n == 1
    fork = next(t for t in txs if t.asset == "ETHW")
    assert fork.transaction_type == TransactionType.BUY
    assert fork.amount == 2.0
    assert fork.fiat_value_at_trigger == 0.0
    assert fork.event_subtype == EVENT_HARD_FORK
    assert fork.parent_asset == "ETH"
    assert fork.id == "hard-fork-ethw-2022-09-15"


def test_fork_idempotent_when_already_present():
    first, _ = normalize_hard_forks(
        [_eth_buy()],
        basis_policy="zero",
        fork_events={"ETHW": {"parent": "ETH", "date": "2022-09-15", "ratio": 1.0}},
    )
    second, n = normalize_hard_forks(
        first,
        basis_policy="zero",
        fork_events={"ETHW": {"parent": "ETH", "date": "2022-09-15", "ratio": 1.0}},
    )
    assert n == 0
    assert sum(1 for t in second if t.asset == "ETHW") == 1


def test_no_fork_when_parent_held_only_after_date():
    txs, n = normalize_hard_forks(
        [_eth_buy(when="2023-01-01T00:00:00")],
        basis_policy="zero",
        fork_events={"ETHW": {"parent": "ETH", "date": "2022-09-15", "ratio": 1.0}},
    )
    assert n == 0
    assert all(t.asset != "ETHW" for t in txs)


def test_ledger_normalize_applies_configured_ethw_fork():
    normalized, changed = normalize_tax_ledger([_eth_buy()])
    assert changed
    fork = next(t for t in normalized if t.asset == "ETHW")
    assert fork.event_subtype == EVENT_HARD_FORK
    assert fork.parent_asset == "ETH"
    assert fork.amount == 2.0
