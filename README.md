# Crypto Tax & PnL Dashboard

Local, self-hosted crypto portfolio PnL and capital-gains tax dashboard for **UK (HMRC)** and **US (IRS)** taxpayers.

The backend is a deterministic Python/FastAPI tax engine. The frontend is a React (Vite + TypeScript) SPA with Tailwind CSS, shadcn-style components, and Recharts.

> **Disclaimer:** This tool performs deterministic accounting from your ledger for informational purposes. It is **not tax advice**. Verify results with a qualified professional before filing.

---

## Quick start

```bash
npm run setup   # once — creates .venv, installs Python + Node deps
cp .env.example .env   # optional API keys for wallets / prices
npm run dev     # API :8000 + web UI :5173
```

| Surface | URL |
| ------- | --- |
| Dashboard | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/api/health |

---

## What it does

- **Dual jurisdiction** — switch UK / US in settings; each uses its own matching engine
- **Portfolio** — holdings, unrealized & realized PnL, allocation charts, tax-loss harvesting hints
- **Tax reports** — UK CGT (with annual exempt amount) + income schedule, or US Form 8949 CSV
- **Imports** — exchange CSVs, wallet address fetch, MEXC email paste, generic CSV/JSON
- **Ledger hygiene** — internal transfer matching, dedup, orphaned-inflow / missing-basis alerts, manual cost-basis overrides
- **Local persistence** — ledger and settings under `data/` (not committed)

Default jurisdiction is **UK**; reporting currency is **GBP** (historical FX applied where needed).

---

## Project structure

```
crypto-tax/
├── apps/
│   ├── api/                 # FastAPI tax engine + ingestion
│   │   ├── app/
│   │   │   ├── tax_engine.py        # US FIFO / HIFO lots, Form 8949
│   │   │   ├── hmrc_cgt_engine.py   # UK same-day → 30-day → S.104
│   │   │   ├── ledger_normalize.py  # read-time ledger fixes
│   │   │   ├── main.py              # REST API
│   │   │   └── …
│   │   ├── tests/
│   │   └── requirements.txt
│   └── web/                 # React dashboard (Vite + TypeScript)
├── data/                    # local ledger / caches (gitignored)
├── scripts/                 # setup, api runner, pytest wrapper
├── .env.example
└── package.json
```

---

## Tax engines

### UK (HMRC) — `hmrc_cgt_engine.py`

Matching order for each asset (CRYPTO22000-style share matching):

1. **Same-day rule** — disposal matched to acquisitions on the same UK calendar day (Europe/London)
2. **30-day (“bed and breakfast”) rule** — matched to acquisitions in the following 30 days
3. **Section 104 pool** — remaining quantity at average allowable cost

Also:

- UK tax years (`2024/25` = 6 Apr–5 Apr) and published annual exempt amounts
- Airdrops / staking as **miscellaneous income** at FMV, with FMV becoming cost basis
- Native crypto **fees (e.g. gas)** treated as disposals of that asset at FMV
- Liquid-staking unstake yield booked as SOL `STAKING` income; companion SOL `BUY` reduced to principal (no double-count)

### US (IRS) — `tax_engine.py`

- **FIFO** or **HIFO** lot matching (no LIFO yet)
- Short-term vs long-term from holding period (**more than** one year → long-term)
- Form 8949-style disposal rows and CSV download
- Same acquisition / disposal / income type model as UK for shared ledger rows

### Shared rules

| Event | Treatment |
| ----- | --------- |
| `BUY` / `AIRDROP` / `STAKING` | Acquisition (cost basis; income types also count as ordinary income at FMV) |
| `SELL` / `FEE` | Disposal (fees on sells reduce proceeds; FEE rows dispose the fee asset at FMV) |
| Paired `TRANSFER` | Non-taxable; basis carries across wallets/exchanges |
| Unpaired `TRANSFER OUT` | Treated as disposal (third-party send / unmatched move) |
| Stablecoins | Treated as cash — excluded from CGT pools |
| Perps | Excluded from spot CGT; configurable as income / capital gains / exclude |

---

## Supported imports

### Exchange / CSV parsers

| Source | Notes |
| ------ | ----- |
| Kraken | Ledger CSV + movement normalisations |
| Binance / Crypto.com | Transaction-history layouts |
| Crypto.com app | Dedicated export parser |
| WOO X / Variational | Perp CSVs |
| Solana / EVM / Celestia | Explorer / wallet CSV shapes |
| Generic | Tolerant column aliases (CSV/JSON) |

### Wallet address import

| Chain | Key | Provider |
| ----- | --- | -------- |
| Solana | `HELIUS_API_KEY` (**required**) | [Helius](https://helius.dev) |
| Ethereum + multi-EVM | `ETHERSCAN_API_KEY` | [Etherscan](https://etherscan.io/myapikey) (one key covers many chains) |
| Bitcoin | — | Blockstream |
| Cardano | optional `BLOCKFROST_API_KEY` | Koios or Blockfrost |
| Celestia | — | PublicNode |

### Other

- **MEXC** — paste deposit / withdrawal / futures emails
- **CoinGecko** — `COINGECKO_API_KEY` strongly recommended for live + historical USD prices

Copy `.env.example` → `.env` at the repo root, then restart the API. Check `/api/health` for `wallet_import.*` flags.

---

## Commands

| Command | Description |
| ------- | ----------- |
| `npm run setup` | First-time install (Python venv + deps) |
| `npm run dev` | API + web together |
| `npm run dev:api` / `dev:web` | Run one side only |
| `npm run build` | Production web build |
| `npm run typecheck` | TypeScript check |
| `npm run test:api` | All API pytest suites |
| `npm run test:hmrc-matrix` | HMRC compliance matrix only |

```bash
# From apps/api with the project venv active:
python -m pytest -v
python -m pytest tests/test_hmrc_compliance_matrix.py -v
```

---

## Key API endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/portfolio?method=FIFO` | Dashboard payload (UK forces Section 104) |
| GET | `/api/pnl-breakdown` | Per-asset lots / disposals drill-down |
| GET | `/api/tax-report?year=…` | UK CGT summary or US realized gains |
| GET | `/api/tax-report/income?year=…` | UK airdrop / staking income |
| GET | `/api/tax-report/perps?year=…` | Perp PnL schedule |
| GET | `/api/tax-report/download?year=…` | CSV (UK CGT / income / Form 8949 / perps) |
| POST | `/api/transactions/import` | Upload CSV/JSON |
| POST | `/api/transactions/import-wallet` | Fetch wallet history |
| GET | `/api/data-health` | Orphaned inflows / missing basis |
| PUT | `/api/settings` | Jurisdiction, perp treatment, data mode |

Full OpenAPI schema: http://localhost:8000/docs

---

## Dashboard UI (high level)

- KPI ribbon, per-coin holdings & realized tables, allocation charts
- Tax reporter (UK CGT / income or US Form 8949) with CSV download
- Tax-loss harvesting matrix
- Import panel, import sources, coverage / overlap alerts
- Missing cost basis + Data Health Ledger (manual overrides)
- Perps and staking sections
- Demo mode with a golden sample ledger for verification

---

## Known limitations

These are intentional gaps or work still in progress — do not treat the engine as complete for every DeFi edge case:

- **LIFO** not implemented (US: FIFO / HIFO only)
- **Hard forks** — no dedicated acquisition / basis-split logic
- **LP / lending deposits** — some DeFi deposits may still normalize as transfers rather than CGT disposals (see HMRC matrix `known_gap` cases)
- **Precision** — quantities and pool costs use Python `float` today (not `Decimal`)
- **US Form 8949** figures currently flow through the GBP reporting path — use with care until USD reporting is jurisdiction-aware
- Perp tax treatment is **configurable policy**, not a fixed legal classification

The HMRC compliance matrix (`apps/api/app/hmrc_matrix.py` + `npm run test:hmrc-matrix`) documents pass / known-gap cases.

---

## Tech stack

| Layer | Stack |
| ----- | ----- |
| API | Python 3, FastAPI, Uvicorn, Pydantic, pandas |
| Web | React 18, TypeScript, Vite, Tailwind, Recharts |
| Tooling | Node.js, concurrently, pytest |

---

## License / use

Private local tool. Keep `.env` and `data/ledger.json` out of version control (already covered by `.gitignore`).
