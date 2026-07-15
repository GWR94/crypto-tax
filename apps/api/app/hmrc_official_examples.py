"""Official HMRC Cryptoassets Manual worked examples as engine goldens.

Primary source (Crown copyright, cite GOV.UK — do not imply HMRC endorsement)::

    https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22250

``20XX`` years in the manual are instantiated as **2024** calendar dates.

HMRC sometimes rounds mid-computation (e.g. £937.50 → £938). This suite asserts
**exact Decimal-derived totals** from our engine and records the published
rounded figures for reference.

Gap fixtures under :data:`NARRATIVE_CASES` instantiate HMRC *narrative* guidance
(CRYPTO21200 / 21250 / 22300 / 22350) with explicit invented numbers — not
official tables — so provenance is always ``narrative_instantiated`` or
``engine_policy``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, List, Optional

from .schemas import Transaction, TransactionType


class Provenance(str, Enum):
    OFFICIAL = "official"  # numbered CRYPTO2225x table
    NARRATIVE = "narrative_instantiated"  # HMRC text + our numbers
    ENGINE_POLICY = "engine_policy"  # documented product policy


@dataclass(frozen=True)
class OfficialExample:
    case_id: str
    title: str
    hmrc_ref: str
    url: str
    provenance: Provenance
    description: str
    # Published HMRC headline figures (may be rounded); for documentation.
    hmrc_published_net_gain: Optional[float] = None
    notes: str = ""


def _ts(when: str) -> datetime:
    dt = datetime.fromisoformat(when)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _tx(
    tx_id: str,
    when: str,
    asset: str,
    ttype: TransactionType,
    amount: float,
    value: float,
) -> Transaction:
    return Transaction(
        id=tx_id,
        timestamp=_ts(when),
        asset=asset,
        transaction_type=ttype,
        amount=amount,
        fiat_value_at_trigger=value,
        fee_fiat=0.0,
        fiat_currency="GBP",
        source="hmrc_official",
    )


# --- CRYPTO22251–22256 (official pooling examples) -------------------------


def fixture_crypto22251() -> List[Transaction]:
    """Example 1 — basic Section 104 pool disposal (Victoria / token A)."""
    return [
        _tx("v-open", "2024-01-01", "TOKA", TransactionType.BUY, 100, 1000),
        _tx("v-buy", "2024-09-18", "TOKA", TransactionType.BUY, 50, 125_000),
        _tx("v-sell", "2024-12-01", "TOKA", TransactionType.SELL, 50, 300_000),
    ]


def fixture_crypto22252() -> List[Transaction]:
    """Example 2 — same-day rule (Martyn / token B)."""
    return [
        _tx("m-open", "2024-01-01", "TOKB", TransactionType.BUY, 5000, 500),
        _tx("m-sell-am", "2024-06-23T09:00:00", "TOKB", TransactionType.SELL, 1000, 800),
        _tx("m-buy", "2024-06-23T14:00:00", "TOKB", TransactionType.BUY, 1600, 1000),
        _tx("m-sell-pm", "2024-06-23T20:00:00", "TOKB", TransactionType.SELL, 500, 600),
    ]


def fixture_crypto22253() -> List[Transaction]:
    """Example 3 — 30-day rule (Rachel / token C)."""
    return [
        _tx("r-open", "2024-01-01", "TOKC", TransactionType.BUY, 2000, 1000),
        _tx("r-sell-1", "2024-03-31", "TOKC", TransactionType.SELL, 1000, 400),
        _tx("r-sell-2", "2024-04-20", "TOKC", TransactionType.SELL, 500, 150),
        _tx("r-buy-1", "2024-04-21", "TOKC", TransactionType.BUY, 700, 175),
        _tx("r-buy-2", "2024-04-28", "TOKC", TransactionType.BUY, 500, 100),
        _tx("r-buy-3", "2024-05-01", "TOKC", TransactionType.BUY, 500, 150),
    ]


def fixture_crypto22254() -> List[Transaction]:
    """Example 4 — same-day + Section 104 (Daniel / token D)."""
    return [
        _tx("d-open", "2024-01-01", "TOKD", TransactionType.BUY, 8000, 1000),
        _tx("d-sell-1", "2024-01-31T10:00:00", "TOKD", TransactionType.SELL, 5000, 500),
        _tx("d-buy-1", "2024-01-31T11:00:00", "TOKD", TransactionType.BUY, 4000, 320),
        _tx("d-buy-2", "2024-01-31T12:00:00", "TOKD", TransactionType.BUY, 1000, 75),
        _tx("d-buy-3", "2024-01-31T13:00:00", "TOKD", TransactionType.BUY, 1000, 70),
        _tx("d-sell-2", "2024-01-31T14:00:00", "TOKD", TransactionType.SELL, 2000, 142),
        _tx("d-buy-4", "2024-01-31T15:00:00", "TOKD", TransactionType.BUY, 500, 35),
    ]


def fixture_crypto22255() -> List[Transaction]:
    """Example 5 — 30-day + Section 104 (Melanie / token E)."""
    return [
        _tx("e-open", "2024-01-01", "TOKE", TransactionType.BUY, 14_000, 200_000),
        _tx("e-sell", "2024-08-30", "TOKE", TransactionType.SELL, 4000, 160_000),
        _tx("e-buy", "2024-09-11", "TOKE", TransactionType.BUY, 500, 17_500),
    ]


def fixture_crypto22256() -> List[Transaction]:
    """Example 6 — same-day + 30-day + Section 104 (Gulferaz / token F)."""
    return [
        _tx("f-open", "2024-01-01", "TOKF", TransactionType.BUY, 100_000, 300_000),
        _tx("f-buy-1", "2024-07-31T10:00:00", "TOKF", TransactionType.BUY, 10_000, 45_000),
        _tx("f-sell-1", "2024-07-31T12:00:00", "TOKF", TransactionType.SELL, 30_000, 150_000),
        _tx("f-sell-2", "2024-08-05", "TOKF", TransactionType.SELL, 20_000, 100_000),
        _tx("f-buy-2", "2024-08-06", "TOKF", TransactionType.BUY, 50_000, 225_000),
        _tx("f-sell-3", "2024-08-07", "TOKF", TransactionType.SELL, 100_000, 150_000),
    ]


def fixture_crypto22257() -> List[Transaction]:
    """Example 7 — crypto-to-crypto (Elina / tokens G & H).

    Each swap is two legs. HMRC values them asymmetrically when the manual
    quotes different token values on each side:

    * disposal proceeds = value of tokens *received*
    * acquisition cost = value of tokens *given up*

    Same-day multi-leg buys/sells of one token are aggregated in the engine
    before matching (CRYPTO22250).
    """
    return [
        _tx("g-open", "2024-01-01", "TOKG", TransactionType.BUY, 100_000, 300_000),
        # 31 Aug: 1,000 G (£3,200) → 10,000 H (£3,200)
        _tx("g-sell-1", "2024-08-31T10:00:00", "TOKG", TransactionType.SELL, 1000, 3200),
        _tx("h-buy-1", "2024-08-31T10:00:00", "TOKH", TransactionType.BUY, 10_000, 3200),
        # 31 Aug: 5,000 H (£1,700) → 600 G (£1,920)
        _tx("h-sell-1", "2024-08-31T12:00:00", "TOKH", TransactionType.SELL, 5000, 1920),
        _tx("g-buy-1", "2024-08-31T12:00:00", "TOKG", TransactionType.BUY, 600, 1700),
        # 31 Aug: 550 G (£1,760) → 5,000 H (£1,650)
        _tx("g-sell-2", "2024-08-31T14:00:00", "TOKG", TransactionType.SELL, 550, 1650),
        _tx("h-buy-2", "2024-08-31T14:00:00", "TOKH", TransactionType.BUY, 5000, 1760),
        # 4 Sep: 2,000 H (£560) → 180 G (£558)
        _tx("h-sell-2", "2024-09-04", "TOKH", TransactionType.SELL, 2000, 558),
        _tx("g-buy-2", "2024-09-04", "TOKG", TransactionType.BUY, 180, 560),
        # 16 Sep: 400 G (£1,080) → 4,000 H (£1,080)
        _tx("g-sell-3", "2024-09-16", "TOKG", TransactionType.SELL, 400, 1080),
        _tx("h-buy-3", "2024-09-16", "TOKH", TransactionType.BUY, 4000, 1080),
        # 27 Oct: 12,000 H (£2,400) → 900 G (£2,430)
        _tx("h-sell-3", "2024-10-27", "TOKH", TransactionType.SELL, 12_000, 2430),
        _tx("g-buy-3", "2024-10-27", "TOKG", TransactionType.BUY, 900, 2400),
    ]


# --- Narrative / policy gap coverage ---------------------------------------


def fixture_narrative_staking_income() -> List[Transaction]:
    """CRYPTO21200 — staking rewards as misc income at GBP FMV; basis for later CGT."""
    return [
        _tx("s-buy", "2024-01-01", "ETH", TransactionType.BUY, 1.0, 2000),
        _tx("s-rew", "2024-06-01", "ETH", TransactionType.STAKING, 0.1, 200),
        _tx("s-sell", "2024-09-01", "ETH", TransactionType.SELL, 0.1, 250),
    ]


def fixture_narrative_airdrop_income() -> List[Transaction]:
    """CRYPTO21250 + CRYPTO22350 — taxable airdrop (service) → income + own S.104 pool."""
    return [
        _tx("a-drop", "2024-05-01", "AIR", TransactionType.AIRDROP, 100, 500),
        _tx("a-sell", "2024-08-01", "AIR", TransactionType.SELL, 50, 400),
    ]


def fixture_narrative_fee_disposal() -> List[Transaction]:
    """Gas/FEE as disposal of the crypto spent (engine policy; aligns with CGT asset disposal)."""
    return [
        _tx("fee-buy", "2024-01-01", "ETH", TransactionType.BUY, 1.0, 2000),
        Transaction(
            id="fee-gas",
            timestamp=_ts("2024-07-01"),
            asset="ETH",
            transaction_type=TransactionType.FEE,
            amount=0.01,
            fiat_value_at_trigger=20.0,
            fee_fiat=0.0,
            fiat_currency="GBP",
            source="hmrc_official",
        ),
    ]


def fixture_policy_hard_fork_fmv() -> List[Transaction]:
    """CRYPTO22300 requires just-and-reasonable *cost split*; engine uses FMV fork BUY.

    This fixture documents our ``HARD_FORK_BASIS_POLICY=fmv`` behaviour, which is
    **not** a literal HMRC numerical example.
    """
    return [
        _tx("fork-eth", "2021-06-01", "ETH", TransactionType.BUY, 2.0, 4000),
    ]


OFFICIAL_CASES: tuple[OfficialExample, ...] = (
    OfficialExample(
        case_id="crypto22251",
        title="Basic Section 104 pool disposal",
        hmrc_ref="CRYPTO22251",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22251",
        provenance=Provenance.OFFICIAL,
        description="Victoria: pool then sell 50/150 → gain £258,000; pool left 100 @ £84,000",
        hmrc_published_net_gain=258_000.0,
    ),
    OfficialExample(
        case_id="crypto22252",
        title="Same-day rule",
        hmrc_ref="CRYPTO22252",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22252",
        provenance=Provenance.OFFICIAL,
        description="Martyn: same-day net disposal; HMRC rounds costs to £938 / gain £462",
        hmrc_published_net_gain=462.0,
        notes="Engine keeps half-pennies: net gain £462.50, pool cost £562.50",
    ),
    OfficialExample(
        case_id="crypto22253",
        title="30-day rule",
        hmrc_ref="CRYPTO22253",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22253",
        provenance=Provenance.OFFICIAL,
        description="Rachel: two disposals matched to later buys; gains £165 + £20",
        hmrc_published_net_gain=185.0,
    ),
    OfficialExample(
        case_id="crypto22254",
        title="Same-day + Section 104",
        hmrc_ref="CRYPTO22254",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22254",
        provenance=Provenance.OFFICIAL,
        description="Daniel: same-day match then S.104 slice; HMRC gain £79 (rounds £62.5→£63)",
        hmrc_published_net_gain=79.0,
        notes="Engine net gain £79.50, S.104 cost £62.50, pool 7,500 @ £937.50",
    ),
    OfficialExample(
        case_id="crypto22255",
        title="30-day + Section 104",
        hmrc_ref="CRYPTO22255",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22255",
        provenance=Provenance.OFFICIAL,
        description="Melanie: 500 BnB + 3,500 S.104 → gain £92,500",
        hmrc_published_net_gain=92_500.0,
    ),
    OfficialExample(
        case_id="crypto22256",
        title="Same-day + 30-day + Section 104",
        hmrc_ref="CRYPTO22256",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22256",
        provenance=Provenance.OFFICIAL,
        description="Gulferaz: three disposals; HMRC net ≈ −£138,637 (rounded S.104 leg)",
        hmrc_published_net_gain=-138_637.0,
        notes="Engine net −£138,636.36 with unrounded S.104 average",
    ),
    OfficialExample(
        case_id="crypto22257",
        title="Crypto-to-crypto exchange",
        hmrc_ref="CRYPTO22257",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22257",
        provenance=Provenance.OFFICIAL,
        description="Elina: same-day aggregation across G↔H swaps; HMRC net −£972",
        hmrc_published_net_gain=-972.0,
        notes="Per-leg gains differ from HMRC's combined disposal rows; net −£972 and pools match",
    ),
)

NARRATIVE_CASES: tuple[OfficialExample, ...] = (
    OfficialExample(
        case_id="narrative-staking-income",
        title="Staking reward as miscellaneous income",
        hmrc_ref="CRYPTO21200",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto21200",
        provenance=Provenance.NARRATIVE,
        description="FMV at receipt is income; same FMV is CGT allowable cost on later disposal",
    ),
    OfficialExample(
        case_id="narrative-airdrop-income",
        title="Taxable airdrop → income + Section 104",
        hmrc_ref="CRYPTO21250 / CRYPTO22350",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto21250",
        provenance=Provenance.NARRATIVE,
        description="Service airdrop: misc income at FMV; tokens enter their own S.104 pool",
    ),
    OfficialExample(
        case_id="narrative-fee-disposal",
        title="Native-token fee / gas as CGT disposal",
        hmrc_ref="CGT asset disposal (engine FEE policy)",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22200",
        provenance=Provenance.ENGINE_POLICY,
        description="Crypto spent as fee disposed at FMV against the Section 104 pool",
    ),
    OfficialExample(
        case_id="policy-hard-fork-fmv",
        title="Hard fork acquisition (FMV policy)",
        hmrc_ref="CRYPTO22300",
        url="https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22300",
        provenance=Provenance.ENGINE_POLICY,
        description=(
            "HMRC: split parent pool cost just-and-reasonably. "
            "Engine: synthetic ETHW BUY at FMV (HARD_FORK_BASIS_POLICY)."
        ),
        notes="Documents divergence from strict CRYPTO22300 cost-split",
    ),
)

FIXTURE_BUILDERS: dict[str, Callable[[], List[Transaction]]] = {
    "crypto22251": fixture_crypto22251,
    "crypto22252": fixture_crypto22252,
    "crypto22253": fixture_crypto22253,
    "crypto22254": fixture_crypto22254,
    "crypto22255": fixture_crypto22255,
    "crypto22256": fixture_crypto22256,
    "crypto22257": fixture_crypto22257,
    "narrative-staking-income": fixture_narrative_staking_income,
    "narrative-airdrop-income": fixture_narrative_airdrop_income,
    "narrative-fee-disposal": fixture_narrative_fee_disposal,
    "policy-hard-fork-fmv": fixture_policy_hard_fork_fmv,
}
