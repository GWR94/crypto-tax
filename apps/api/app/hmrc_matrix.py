"""HMRC compliance test matrix — case catalogue and verification helpers.

Each :class:`HmrcMatrixCase` documents what UK law expects versus what the
engine currently does.  Tests in ``tests/test_hmrc_compliance_matrix.py`` run
fixtures through :func:`normalize_tax_ledger` and assert outcomes.

Status values:
  * ``pass`` — engine matches HMRC expectation (regression guard)
  * ``known_gap`` — documents a deliberate/policy gap; asserts current behaviour
  * ``fail_target`` — HMRC expectation not yet met; marked xfail until fixed
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, List, Literal, Optional

from .cost_basis_overrides import build_override_from_request, prepare_tax_ledger
from .data_health import find_orphaned_inflows
from .hmrc_cgt_engine import (
    calculate_uk_cgt,
    calculate_uk_income,
    compute_uk_missing_cost_basis,
)
from .ledger_normalize import normalize_tax_ledger
from .schemas import CgtMatchType, Transaction, TransactionType

MatrixCategory = Literal[
    "matching",
    "defi",
    "liquid_staking",
    "income",
    "missing_basis",
    "normalization",
]


class MatrixStatus(str, Enum):
    PASS = "pass"
    KNOWN_GAP = "known_gap"
    FAIL_TARGET = "fail_target"


@dataclass(frozen=True)
class HmrcMatrixCase:
    case_id: str
    category: MatrixCategory
    status: MatrixStatus
    description: str
    hmrc_expectation: str
    risk_if_wrong: str
    reference: str = ""


# --- Fixture builders -------------------------------------------------------


def _tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    value: float,
    *,
    fee: float = 0.0,
    currency: str = "GBP",
    direction: str | None = None,
    source: str | None = None,
    gid: str | None = None,
    on_chain: str | None = None,
    counterparty: str | None = None,
) -> Transaction:
    ts = datetime.fromisoformat(when)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return Transaction(
        id=tx_id,
        timestamp=ts,
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fee_fiat=fee,
        fiat_currency=currency,
        source=source,
        transfer_direction=direction,
        trade_group_id=gid,
        on_chain_tx_id=on_chain or gid,
        counterparty_address=counterparty,
    )


def fixture_match_sameday_xrp() -> List[Transaction]:
    from .sample_data import default_transactions

    ids = {"demo-xrp-sameday-buy", "demo-xrp-sameday-sell"}
    return [t for t in default_transactions() if t.id in ids]


def fixture_match_30day_ada() -> List[Transaction]:
    from .sample_data import default_transactions

    ids = {"demo-ada-bb-buy-1", "demo-ada-bb-sell", "demo-ada-bb-buy-2"}
    return [t for t in default_transactions() if t.id in ids]


def fixture_match_s104_avax() -> List[Transaction]:
    from .sample_data import default_transactions

    ids = {"demo-avax-pool-buy", "demo-avax-pool-sell"}
    return [t for t in default_transactions() if t.id in ids]


def fixture_match_bst_boundary() -> List[Transaction]:
    """Sell and repurchase at the same UK instant but different UTC calendar dates."""
    return [
        _tx("bst-pool", "2024-01-01T00:00:00Z", "BTC", TransactionType.BUY, 1, 10000),
        # 2024-06-01 23:30 UTC = 2024-06-02 00:30 BST
        _tx("bst-sell", "2024-06-01T23:30:00Z", "BTC", TransactionType.SELL, 1, 12000),
        _tx(
            "bst-rebuy",
            "2024-06-02T00:30:00+01:00",
            "BTC",
            TransactionType.BUY,
            1,
            11000,
        ),
    ]


JUPITER_SWAP_SIG = (
    "5XbMAkuWHFXiNb1srxxPgQKBcwk52HvdYFMbUf3zVw6a6HS8E4VkBbpCXNB5jNEzSKCJrh9b9qEcwnQ5SAVHCinL"
)
KAMINO_WITHDRAW_SIG = (
    "ScGyXB81DtPLC8zTFoeB28ebDpjUrvTN6JqvjGm8LKcXRLyzRVwxfRvRwoR9ys8k9Ffomxv41Ldoxj8KLWM5ngP"
)
KAMINO_LEND = "9DrvZvyWh1HuAoZxvYWMvkf2XCzryCpGgHqrMjyDWpmo"
KAMINO_DEPOSIT_SIG = (
    "2CsYiLomHzs3H6BS5mjUYw3CPa8cHQL2vR62PDptXYH9Cy8wwoCCXAqcwFvLRRhUz5aEsYYjyAf7bxhwjjH9H8Ra"
)
MSOL_AMOUNT = 1.399453074


def fixture_match_jupiter_swap() -> List[Transaction]:
    return [
        _tx(
            f"sol-{JUPITER_SWAP_SIG}-sell-MSOL",
            "2025-07-31T08:39:20",
            "MSOL",
            TransactionType.SELL,
            MSOL_AMOUNT,
            250.0,
            currency="USD",
            source="solana",
            gid=JUPITER_SWAP_SIG,
        ),
        _tx(
            f"sol-{JUPITER_SWAP_SIG}-buy-SOL",
            "2025-07-31T08:39:20",
            "SOL",
            TransactionType.BUY,
            3.672093348,
            250.0,
            currency="USD",
            source="solana",
            gid=JUPITER_SWAP_SIG,
        ),
    ]


def fixture_match_polluted_trade_group() -> List[Transaction]:
    return [
        _tx(
            f"sol-{KAMINO_WITHDRAW_SIG}-transfer-in-MSOL",
            "2025-07-31T08:36:46",
            "MSOL",
            TransactionType.TRANSFER,
            MSOL_AMOUNT,
            0.0,
            currency="USD",
            source="solana",
            direction="IN",
            gid=JUPITER_SWAP_SIG,
            on_chain=KAMINO_WITHDRAW_SIG,
            counterparty=KAMINO_LEND,
        ),
    ]


def fixture_defi_kamino_lend_deposit() -> List[Transaction]:
    return [
        _tx(
            "kamino-msol-buy",
            "2024-02-01T00:00:00Z",
            "MSOL",
            TransactionType.BUY,
            2.021458952,
            250.0,
            currency="USD",
            source="solana",
        ),
        _tx(
            "kamino-deposit",
            "2024-02-12T11:46:10",
            "MSOL",
            TransactionType.SELL,
            2.021458952,
            260.0,
            currency="USD",
            source="solana",
            gid=KAMINO_DEPOSIT_SIG,
            on_chain=KAMINO_DEPOSIT_SIG,
            counterparty=KAMINO_LEND,
        ),
    ]


def fixture_lst_unstake_yield() -> List[Transaction]:
    return [
        _tx(
            "lst-buy",
            "2024-01-01T00:00:00",
            "MSOL",
            TransactionType.BUY,
            1.0,
            100.0,
            currency="USD",
            source="solana",
            gid="stake-1",
        ),
        _tx(
            "lst-sol-out",
            "2024-01-01T00:00:00",
            "SOL",
            TransactionType.SELL,
            1.0,
            100.0,
            currency="USD",
            source="solana",
            gid="stake-1",
        ),
        _tx(
            "lst-sell",
            "2024-06-01T00:00:00",
            "MSOL",
            TransactionType.SELL,
            1.0,
            150.0,
            currency="USD",
            source="solana",
            gid="unstake-1",
        ),
        _tx(
            "lst-sol-in",
            "2024-06-01T00:00:00",
            "SOL",
            TransactionType.BUY,
            1.1,
            150.0,
            currency="USD",
            source="solana",
            gid="unstake-1",
        ),
    ]


def fixture_lst_false_unmatched() -> List[Transaction]:
    return [
        _tx(
            "lst-buy",
            "2024-02-12T10:13:52",
            "MSOL",
            TransactionType.BUY,
            4.242918,
            532.34,
            currency="USD",
            source="solana",
            gid="stake",
        ),
        _tx(
            "lst-sol-out",
            "2024-02-12T10:13:52",
            "SOL",
            TransactionType.SELL,
            4.9538,
            532.06,
            currency="USD",
            source="solana",
            gid="stake",
        ),
        _tx(
            "lst-xfer-out",
            "2024-02-12T11:46:10",
            "MSOL",
            TransactionType.TRANSFER,
            2.021459,
            253.62,
            currency="USD",
            source="solana",
            direction="OUT",
        ),
        _tx(
            "lst-sell",
            "2024-02-12T11:46:10",
            "MSOL",
            TransactionType.SELL,
            2.021459,
            195.15,
            currency="USD",
            source="solana",
            gid="unstake",
        ),
        _tx(
            "lst-dust",
            "2024-02-12T11:46:10",
            "SOL",
            TransactionType.SELL,
            0.035559,
            5.16,
            currency="USD",
            source="solana",
            gid="unstake",
        ),
    ]


def fixture_income_arb() -> List[Transaction]:
    from .sample_data import default_transactions

    return [t for t in default_transactions() if t.id == "demo-arb-airdrop"]


def fixture_income_zero_fiat_airdrop() -> List[Transaction]:
    return [
        _tx(
            "zero-airdrop",
            "2024-05-15T00:00:00Z",
            "ARB",
            TransactionType.AIRDROP,
            100.0,
            0.0,
        ),
    ]


def fixture_gap_doge_unmatched() -> List[Transaction]:
    from .sample_data import default_transactions

    return [t for t in default_transactions() if t.id == "demo-doge-sell"]


def fixture_gap_mexc_orphan() -> List[Transaction]:
    from .sample_data import default_transactions

    return [t for t in default_transactions() if t.id == "demo-mexc-deposit"]


def fixture_gap_override() -> List[Transaction]:
    return [
        _tx(
            "dep",
            "2024-06-01T12:00:00",
            "ETH",
            TransactionType.TRANSFER,
            2.0,
            0.0,
            direction="IN",
            source="kraken",
        ),
        _tx(
            "sell",
            "2024-09-01T12:00:00",
            "ETH",
            TransactionType.SELL,
            2.0,
            5000.0,
            source="kraken",
        ),
    ]


FIXTURE_BUILDERS: dict[str, Callable[[], List[Transaction]]] = {
    "match-sameday-xrp": fixture_match_sameday_xrp,
    "match-30day-ada": fixture_match_30day_ada,
    "match-s104-avax": fixture_match_s104_avax,
    "match-bst-boundary": fixture_match_bst_boundary,
    "match-jupiter-swap": fixture_match_jupiter_swap,
    "match-polluted-trade-group": fixture_match_polluted_trade_group,
    "defi-kamino-lend-deposit": fixture_defi_kamino_lend_deposit,
    "lst-unstake-yield": fixture_lst_unstake_yield,
    "lst-no-false-unmatched": fixture_lst_false_unmatched,
    "income-arb-airdrop": fixture_income_arb,
    "income-zero-fiat-airdrop": fixture_income_zero_fiat_airdrop,
    "gap-doge-unmatched": fixture_gap_doge_unmatched,
    "gap-mexc-orphan": fixture_gap_mexc_orphan,
    "gap-override-fixes-sell": fixture_gap_override,
}


HMRC_MATRIX_CASES: tuple[HmrcMatrixCase, ...] = (
    HmrcMatrixCase(
        case_id="match-sameday-xrp",
        category="matching",
        status=MatrixStatus.PASS,
        description="Same-calendar-day buy and sell of XRP",
        hmrc_expectation="Same-day rule; gain = proceeds − same-day acquisition cost",
        risk_if_wrong="Incorrect match type and gain on intraday trades",
        reference="demo-xrp-sameday-buy / demo-xrp-sameday-sell",
    ),
    HmrcMatrixCase(
        case_id="match-30day-ada",
        category="matching",
        status=MatrixStatus.PASS,
        description="ADA sold then repurchased within 30 days",
        hmrc_expectation="30-day bed-and-breakfast against repurchase, not Section 104 pool",
        risk_if_wrong="Under- or over-stated gain on wash sales",
        reference="demo-ada-bb-sell / demo-ada-bb-buy-2",
    ),
    HmrcMatrixCase(
        case_id="match-s104-avax",
        category="matching",
        status=MatrixStatus.PASS,
        description="Single pooled disposal of AVAX",
        hmrc_expectation="Section 104 average cost",
        risk_if_wrong="Wrong allowable cost on pooled assets",
        reference="demo-avax-pool-sell",
    ),
    HmrcMatrixCase(
        case_id="match-bst-boundary",
        category="matching",
        status=MatrixStatus.PASS,
        description="Sell 23:30 UTC and repurchase 00:30 BST same UK instant",
        hmrc_expectation="Same-day rule using UK (Europe/London) calendar dates",
        risk_if_wrong="30-day or pool match instead of same-day around DST boundaries",
        reference="Synthetic bst-sell / bst-rebuy",
    ),
    HmrcMatrixCase(
        case_id="match-jupiter-swap",
        category="normalization",
        status=MatrixStatus.PASS,
        description="Jupiter MSOL→SOL swap retains taxable legs",
        hmrc_expectation="MSOL SELL disposal; SOL BUY acquisition",
        risk_if_wrong="Swap treated as transfer — CGT event missed",
        reference=JUPITER_SWAP_SIG,
    ),
    HmrcMatrixCase(
        case_id="match-polluted-trade-group",
        category="normalization",
        status=MatrixStatus.PASS,
        description="Kamino receipt must not keep Jupiter trade_group_id",
        hmrc_expectation="on_chain_tx_id drives trade_group_id after repair",
        risk_if_wrong="Cross-signature grouping breaks swap and matching",
        reference=KAMINO_WITHDRAW_SIG,
    ),
    HmrcMatrixCase(
        case_id="defi-kamino-lend-deposit",
        category="defi",
        status=MatrixStatus.KNOWN_GAP,
        description="Kamino Lend MSOL deposit after prior acquisition",
        hmrc_expectation="CGT disposal of MSOL at FMV on deposit",
        risk_if_wrong="Under-reported CGT — deposit treated as internal transfer",
        reference=KAMINO_DEPOSIT_SIG,
    ),
    HmrcMatrixCase(
        case_id="lst-unstake-yield",
        category="liquid_staking",
        status=MatrixStatus.PASS,
        description="mSOL unstake with SOL yield above deposited principal",
        hmrc_expectation="Miscellaneous income on excess SOL; LST disposal on unstake",
        risk_if_wrong="Missing income or false unmatched disposal",
    ),
    HmrcMatrixCase(
        case_id="lst-no-false-unmatched",
        category="liquid_staking",
        status=MatrixStatus.PASS,
        description="Duplicate mSOL TRANSFER OUT must not drain pool before SELL",
        hmrc_expectation="LST sell matched to stake acquisition, not UNMATCHED",
        risk_if_wrong="£0 basis disposal — overstated CGT",
    ),
    HmrcMatrixCase(
        case_id="income-arb-airdrop",
        category="income",
        status=MatrixStatus.PASS,
        description="ARB airdrop at known GBP FMV",
        hmrc_expectation="£500 miscellaneous income",
        risk_if_wrong="Under-reported income tax",
        reference="demo-arb-airdrop",
    ),
    HmrcMatrixCase(
        case_id="income-zero-fiat-airdrop",
        category="income",
        status=MatrixStatus.PASS,
        description="Airdrop row with zero fiat_value_at_trigger",
        hmrc_expectation="Income at historical GBP FMV on receipt date",
        risk_if_wrong="£0 income until enrichment — under-reported income tax",
    ),
    HmrcMatrixCase(
        case_id="gap-doge-unmatched",
        category="missing_basis",
        status=MatrixStatus.PASS,
        description="Sell with no acquisition history",
        hmrc_expectation="UNMATCHED flag; £0 allowable cost; full proceeds as gain",
        risk_if_wrong="Silent wrong basis or crash",
        reference="demo-doge-sell",
    ),
    HmrcMatrixCase(
        case_id="gap-mexc-orphan",
        category="missing_basis",
        status=MatrixStatus.PASS,
        description="Zero-fiat exchange deposit after data purge",
        hmrc_expectation="Orphaned inflow flagged for manual override",
        risk_if_wrong="Undetected missing history",
        reference="demo-mexc-deposit",
    ),
    HmrcMatrixCase(
        case_id="gap-override-fixes-sell",
        category="missing_basis",
        status=MatrixStatus.PASS,
        description="Manual override on orphaned deposit before sell",
        hmrc_expectation="Allowable cost from synthetic BUY; gain = proceeds − override",
        risk_if_wrong="User cannot recover from exchange data gaps",
    ),
)


def case_by_id(case_id: str) -> HmrcMatrixCase:
    for case in HMRC_MATRIX_CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)


def load_fixture(case_id: str) -> List[Transaction]:
    return FIXTURE_BUILDERS[case_id]()


def run_matrix_case(case_id: str) -> None:
    """Assert engine behaviour for one matrix row (raises AssertionError on failure)."""
    case = case_by_id(case_id)
    raw = load_fixture(case_id)
    normalized, _ = normalize_tax_ledger(list(raw))

    if case_id == "match-sameday-xrp":
        report = calculate_uk_cgt(normalized, tax_year_label="2024/25")
        row = report.rows[0]
        assert row.match_type == CgtMatchType.SAME_DAY
        assert row.gain == 500.0

    elif case_id == "match-30day-ada":
        report = calculate_uk_cgt(normalized)
        bnb = [r for r in report.rows if r.match_type == CgtMatchType.THIRTY_DAY]
        assert len(bnb) == 1
        assert bnb[0].allowable_cost == 1100.0
        assert bnb[0].gain == 100.0

    elif case_id == "match-s104-avax":
        report = calculate_uk_cgt(normalized)
        assert report.rows[0].match_type == CgtMatchType.SECTION_104
        assert report.rows[0].gain == 250.0

    elif case_id == "match-bst-boundary":
        report = calculate_uk_cgt(normalized, tax_year_label="2024/25")
        row = report.rows[0]
        assert row.match_type == CgtMatchType.SAME_DAY
        assert row.gain == 1000.0

    elif case_id == "match-jupiter-swap":
        sells = [t for t in normalized if t.transaction_type == TransactionType.SELL]
        buys = [t for t in normalized if t.transaction_type == TransactionType.BUY]
        assert any(t.asset == "MSOL" for t in sells)
        assert any(t.asset == "SOL" for t in buys)

    elif case_id == "match-polluted-trade-group":
        tx = normalized[0]
        assert tx.trade_group_id == KAMINO_WITHDRAW_SIG
        assert tx.on_chain_tx_id == KAMINO_WITHDRAW_SIG

    elif case_id == "defi-kamino-lend-deposit":
        # Current engine policy: lending deposit is not a CGT disposal.
        msol_legs = [t for t in normalized if t.asset == "MSOL"]
        assert any(
            t.transaction_type == TransactionType.TRANSFER
            and t.transfer_direction == "OUT"
            for t in msol_legs
        )
        report = calculate_uk_cgt(normalized, tax_year_label="2024/25")
        deposit_disposals = [
            r for r in report.rows if r.disposal_id == "kamino-deposit"
        ]
        assert deposit_disposals == []

    elif case_id == "lst-unstake-yield":
        staking = [t for t in normalized if t.transaction_type == TransactionType.STAKING]
        assert len(staking) == 1
        assert staking[0].asset == "SOL"
        assert staking[0].amount == 0.1
        sol_buy = next(
            t
            for t in normalized
            if t.transaction_type == TransactionType.BUY and t.asset == "SOL"
        )
        assert sol_buy.amount == 1.0
        assert abs(sol_buy.amount + staking[0].amount - 1.1) < 1e-9

    elif case_id == "lst-no-false-unmatched":
        flags = compute_uk_missing_cost_basis(normalized)
        assert not any(f.asset == "MSOL" for f in flags)

    elif case_id == "income-arb-airdrop":
        income = calculate_uk_income(normalized, tax_year_label="2024/25")
        assert income.airdrop_income == 500.0

    elif case_id == "income-zero-fiat-airdrop":
        income = calculate_uk_income(normalized, tax_year_label="2024/25")
        assert income.airdrop_income > 0.0

    elif case_id == "gap-doge-unmatched":
        report = calculate_uk_cgt(normalized, tax_year_label="2024/25")
        row = report.rows[0]
        assert row.match_type == CgtMatchType.UNMATCHED
        assert row.missing_cost_basis is True
        assert row.allowable_cost == 0.0
        assert row.gain == 158.0

    elif case_id == "gap-mexc-orphan":
        flags = find_orphaned_inflows(normalized)
        assert len(flags) == 1
        assert flags[0].transaction_id == "demo-mexc-deposit"

    elif case_id == "gap-override-fixes-sell":
        anchor = raw[0]
        override = build_override_from_request(
            anchor=anchor,
            acquisition_date=datetime(2023, 1, 15, tzinfo=timezone.utc),
            total_fiat_spent=3000.0,
        )
        tax_txs = prepare_tax_ledger(raw, [override])
        report = calculate_uk_cgt(tax_txs, tax_year_label="2024/25")
        assert report.disposal_count == 1
        row = report.rows[0]
        assert not row.missing_cost_basis
        assert row.allowable_cost == 3000.0
        assert row.gain == 2000.0

    else:
        raise AssertionError(f"No assertions registered for {case_id}")


def matrix_summary() -> List[dict]:
    """Return catalogue rows for documentation or API export."""
    return [
        {
            "case_id": c.case_id,
            "category": c.category,
            "status": c.status.value,
            "description": c.description,
            "hmrc_expectation": c.hmrc_expectation,
            "risk_if_wrong": c.risk_if_wrong,
            "reference": c.reference,
        }
        for c in HMRC_MATRIX_CASES
    ]
