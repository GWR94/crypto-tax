# Crypto Tax & PnL Dashboard

A local, self-hosted crypto portfolio PnL and capital-gains tax calculation
dashboard. The backend is a deterministic Python/FastAPI tax engine; the
frontend is a React (Vite + TypeScript) single-page app styled with Tailwind
CSS and shadcn-style components, with Recharts for visualization.

> **Disclaimer:** This tool performs deterministic accounting math for
> informational purposes. It is not tax advice. Verify results with a qualified
> professional before filing.

## Features

- **Unified transaction schema** with a tolerant CSV/JSON ingestion engine.
- **Deterministic FIFO & HIFO** capital-gains engine (no estimation — strict
  lot matching in pure Python).
- **Internal transfer matching**: a SELL on one ledger and a BUY on another for
  identical amounts within 15 minutes are reclassified as non-taxable
  `TRANSFER` events, preserving cost-basis continuity.
- **Dust filtering**: positions worth less than $0.50 are ignored.
- **Executive KPI ribbon**: portfolio value, realized gains, unrealized gains,
  and a crypto income (airdrop/staking) summary.
- **Tax-loss-harvesting matrix**: only red positions, with potential tax savings
  at a flat 20% rate.
- **Missing cost-basis alert**: flags sells with no matching purchase history.
- **Per-coin PnL table** and **portfolio allocation chart**.
- **Tax Reporter**: pick a year + method and export an IRS Form 8949-structured
  CSV of short-term vs. long-term gains.

## Project structure

```
crypto-tax/
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py          # FastAPI app + REST endpoints
│       ├── tax_engine.py    # FIFO/HIFO, transfer matching, dust filtering
│       ├── schemas.py       # Pydantic v2 models
│       ├── ingestion.py     # CSV/JSON parsing
│       ├── pricing.py       # in-memory price store
│       ├── state.py         # JSON-backed ledger
│       └── sample_data.py   # seed dataset
└── frontend/
    └── src/
        ├── App.tsx
        ├── components/
        │   ├── Dashboard.tsx
        │   ├── KpiRibbon.tsx
        │   ├── TaxHarvestTable.tsx
        │   ├── PerCoinTable.tsx
        │   ├── AllocationChart.tsx
        │   ├── MissingCostBasisAlert.tsx
        │   ├── TaxReporter.tsx
        │   └── ui/           # shadcn-style primitives
        └── lib/              # api client, types, utils
```

## Running the backend

```bash
cd backend
python -m venv .venv
# Windows (bash): source .venv/Scripts/activate
# macOS/Linux:    source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs: <http://localhost:8000/docs>

## Running the frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The Vite dev server proxies `/api` to the backend
on port 8000.

To build for production:

```bash
npm run build && npm run preview
```

## Accounting rules

- **Acquisitions** (`BUY`, `AIRDROP`, `STAKING`) create cost-basis lots. Airdrop
  and staking lots use fair-market value at receipt and also count as ordinary
  crypto income.
- **Disposals** (`SELL`, `FEE`) consume lots: FIFO consumes the oldest lot
  first; HIFO consumes the highest unit-cost lot first.
- **Holding period**: more than 365 days is **long-term**, otherwise
  **short-term** (IRS rule).
- **Transfers** are non-taxable and never alter lots.

## Key API endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/portfolio?method=FIFO` | Full dashboard payload |
| GET | `/api/tax-report?year=2024&method=HIFO` | Realized gains for a year |
| GET | `/api/tax-report/download?year=2024&method=FIFO` | Form 8949 CSV |
| POST | `/api/transactions/import` | Upload a CSV/JSON ledger |
| POST | `/api/transactions/match-transfers` | Reclassify internal transfers |
| PUT | `/api/prices` | Override current market prices |
```
