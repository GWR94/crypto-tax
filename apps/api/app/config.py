"""Reporting currency configuration."""

REPORTING_CURRENCY = "GBP"
SUPPORTED_DISPLAY_CURRENCIES = frozenset({"GBP", "USD"})

# UK: CGT has no short/long-term rate split. US: IRS holding-period rules apply.
TAX_JURISDICTION = "UK"
SUPPORTED_TAX_JURISDICTIONS = frozenset({"UK", "US"})

# How perpetual-futures PnL is treated for tax:
#   "exclude"       — perps shown for reference only, kept out of every report
#   "income"        — net realized PnL reported as trading/ordinary income by year
#   "capital_gains" — perp fills routed through the spot CGT / Form 8949 engine
SUPPORTED_PERP_TREATMENTS = frozenset({"exclude", "income", "capital_gains"})
DEFAULT_UK_PERP_TREATMENT = "income"
DEFAULT_US_PERP_TREATMENT = "income"

# HMRC annual exempt amount (CGT allowance) in GBP, keyed by UK tax-year label.
# Source: HMRC published allowances. Update as new tax years are confirmed.
UK_CGT_ANNUAL_EXEMPT_AMOUNT = {
    "2019/20": 12000.0,
    "2020/21": 12300.0,
    "2021/22": 12300.0,
    "2022/23": 12300.0,
    "2023/24": 6000.0,
    "2024/25": 3000.0,
    "2025/26": 3000.0,
    "2026/27": 3000.0,
}

# Fallback when a tax year is not in the table above (most recent known value).
UK_CGT_DEFAULT_ALLOWANCE = 3000.0

# Liquid-staking unstake yield booked as STAKING income:
#   "sol" — excess SOL received over SOL deposited when staking (native yield)
#   "reporting" — same trigger, also reduces LST disposal proceeds for CGT
#   "off" — entire unstake PnL stays on the LST disposal (capital gains only)
LIQUID_STAKING_YIELD_AS_INCOME = "sol"

# Pegged USD stablecoins — excluded from per-coin PnL (treated as cash).
STABLECOIN_ASSETS = frozenset(
    {
        "USDT",
        "USDT0",
        "USDC",
        "DAI",
        "TUSD",
        "USDP",
        "PYUSD",
        "BUSD",
    }
)

# Major native tickers — never resolve via the Solana memecoin registry.
RESERVED_SYMBOLS = frozenset(
    {
        "BTC",
        "ETH",
        "ETHW",
        "SOL",
        "ADA",
        "DOT",
        "AVAX",
        "MATIC",
        "POL",
        "LINK",
        "UNI",
        "ATOM",
        "XRP",
        "LTC",
        "BCH",
        "XLM",
        "DOGE",
        "BNB",
        "CRO",
        "CHZ",
        "ENA",
        "VET",
        "LUNA",
        "LUNC",
        "LUNA2",
        "REZ",
        "WEN",
        "MOODENG",
        "MUBI",
        "KDA",
        "1000FLOKI",
        "ARB",
        "OP",
        "TIA",
        "HYPE",
        "INJ",
        "NEAR",
        "APT",
        "SUI",
        "SEI",
        "FTM",
        "ALGO",
        "HBAR",
        "FIL",
        "ICP",
        "AAVE",
        "MKR",
        "CRV",
        "SNX",
        "COMP",
        "GRT",
        "SAND",
        "MANA",
        "APE",
        "SHIB",
        "PEPE",
        "USDT",
        "USDC",
        "DAI",
        "TUSD",
        "USDP",
        "PYUSD",
        "BUSD",
    }
)

# Human-readable names for common exchange-native tickers.
NATIVE_ASSET_NAMES: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "ETHW": "Ethereum PoW (fork)",
    "SOL": "Solana",
    "ADA": "Cardano",
    "DOT": "Polkadot",
    "AVAX": "Avalanche",
    "MATIC": "Polygon",
    "POL": "Polygon",
    "LINK": "Chainlink",
    "UNI": "Uniswap",
    "ATOM": "Cosmos",
    "XRP": "Ripple",
    "LTC": "Litecoin",
    "DOGE": "Dogecoin",
    "BNB": "BNB Chain",
    "CRO": "Cronos",
    "CHZ": "Chiliz",
    "ENA": "Ethena",
    "VET": "VeChain",
    "LUNA": "Terra",
    "LUNC": "Terra Classic",
    "LUNA2": "Terra 2.0",
    "REZ": "Renzo",
    "WEN": "Wen (Solana)",
    "MOODENG": "Moo Deng",
    "MUBI": "Multibit",
    "KDA": "Kadena",
    "1000FLOKI": "1000FLOKI (perp unit)",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "TIA": "Celestia",
    "HYPE": "Hyperliquid",
    "INJ": "Injective",
    "NEAR": "NEAR",
    "APT": "Aptos",
    "SUI": "Sui",
    "SEI": "Sei",
    "MSOL": "Marinade Staked SOL",
    "BSOL": "BlazeStake Staked SOL",
    "JITOSOL": "Jito Staked SOL",
    "JTO": "Jito",
    "USDC": "USD Coin",
    "USDT": "Tether USD",
    "USDT0": "USDT0",
    "DAI": "Dai",
    "BUSD": "Binance USD",
    "TUSD": "TrueUSD",
    "USDP": "Pax Dollar",
    "PYUSD": "PayPal USD",
    "BABY": "Babylon",
}

# Exchange-specific tickers that differ from on-chain / wallet symbols.
EXCHANGE_ASSET_ALIASES: dict[str, str] = {
    "CROWN2": "CROWN",  # MEXC lists Third Time Games CROWN as CROWN2
}


def normalize_asset_ticker(asset: str) -> str:
    """Canonical ticker for comparisons (handles stylised on-chain symbols)."""
    return asset.strip().upper().replace("₮", "T")


def is_stablecoin(asset: str) -> bool:
    return normalize_asset_ticker(asset) in STABLECOIN_ASSETS


def is_reserved_symbol(asset: str) -> bool:
    return asset.strip().upper() in RESERVED_SYMBOLS


def native_asset_name(asset: str) -> str:
    return NATIVE_ASSET_NAMES.get(asset.strip().upper(), asset.strip().upper())
