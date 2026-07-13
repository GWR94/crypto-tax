"""Parse EVM wallet activity into unified transactions."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from .evm_chains import EvmChain, native_asset_for
from .schemas import Transaction, TransactionType
from .token_spam import is_scam_token_label

SWAP_NET_EPS = 1e-12


def _normalize_address(address: str) -> str:
    return address.strip().lower()


def _row_id(chain: EvmChain, tx_hash: str, kind: str, asset: str) -> str:
    return f"evm-{chain}-{tx_hash[:18]}-{kind}-{asset}"


def _counterparty_for_row(row: dict, direction: str) -> Optional[str]:
    if direction == "IN":
        return row.get("from") or None
    return row.get("to") or None


def _transfer_tx(
    *,
    chain: EvmChain,
    tx_hash: str,
    timestamp: datetime,
    asset: str,
    amount: float,
    direction: str,
    contract: Optional[str] = None,
    counterparty_address: Optional[str] = None,
    trade_group_id: Optional[str] = None,
) -> Transaction:
    return Transaction(
        id=_row_id(chain, tx_hash, f"transfer-{direction}", asset),
        timestamp=timestamp,
        asset=asset,
        transaction_type=TransactionType.TRANSFER,
        amount=amount,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source=chain,
        transfer_direction=direction,  # type: ignore[arg-type]
        token_mint=contract,
        counterparty_address=counterparty_address,
        trade_group_id=trade_group_id,
        on_chain_tx_id=tx_hash,
    )


def _fee_tx(
    *,
    chain: EvmChain,
    tx_hash: str,
    timestamp: datetime,
    amount: float,
    asset: str,
    trade_group_id: Optional[str] = None,
) -> Transaction:
    return Transaction(
        id=_row_id(chain, tx_hash, "fee", asset),
        timestamp=timestamp,
        asset=asset,
        transaction_type=TransactionType.FEE,
        amount=amount,
        fiat_value_at_trigger=0.0,
        fee_fiat=0.0,
        source=chain,
        trade_group_id=trade_group_id,
        on_chain_tx_id=tx_hash,
    )


def _parse_swap_group(
    rows: List[dict],
    *,
    chain: EvmChain,
    wallet: str,
) -> List[Transaction]:
    """Net same-hash token legs into taxable BUY/SELL rows."""
    timestamp = rows[0]["timestamp"]
    tx_hash = str(rows[0]["hash"])
    trade_group_id = tx_hash

    nets: Dict[str, float] = defaultdict(float)
    mints: Dict[str, Optional[str]] = {}

    for row in rows:
        asset = str(row["asset"])
        mints[asset] = row.get("contract")
        amount = float(row["amount"])
        if row["flow"] == "in":
            nets[asset] += amount
        else:
            nets[asset] -= amount

    net_ins = {a: q for a, q in nets.items() if q > SWAP_NET_EPS}
    net_outs = {a: q for a, q in nets.items() if q < -SWAP_NET_EPS}

    # Multiple inbound tokens with no wallet outflow — staking withdraw / rewards, not a swap.
    if net_ins and not net_outs:
        transactions: List[Transaction] = []
        for asset, qty in net_ins.items():
            if is_scam_token_label(asset):
                continue
            source_row = next(
                (r for r in rows if str(r["asset"]) == asset and r["flow"] == "in"),
                rows[0],
            )
            transactions.append(
                _transfer_tx(
                    chain=chain,
                    tx_hash=tx_hash,
                    timestamp=timestamp,
                    asset=asset,
                    amount=qty,
                    direction="IN",
                    contract=mints.get(asset),
                    counterparty_address=_counterparty_for_row(source_row, "IN"),
                    trade_group_id=trade_group_id,
                )
            )
        return transactions

    counter_for_sell = next(iter(net_ins), None) if len(net_ins) == 1 else None
    counter_for_buy = next(iter(net_outs), None) if len(net_outs) == 1 else None

    transactions: List[Transaction] = []
    for asset, qty in net_outs.items():
        if is_scam_token_label(asset):
            continue
        transactions.append(
            Transaction(
                id=_row_id(chain, tx_hash, f"sell-{asset}", asset),
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.SELL,
                amount=abs(qty),
                fiat_value_at_trigger=0.0,
                fee_fiat=0.0,
                counter_asset=counter_for_sell,
                trade_group_id=trade_group_id,
                source=chain,
                token_mint=mints.get(asset),
                on_chain_tx_id=tx_hash,
            )
        )
    for asset, qty in net_ins.items():
        if is_scam_token_label(asset):
            continue
        transactions.append(
            Transaction(
                id=_row_id(chain, tx_hash, f"buy-{asset}", asset),
                timestamp=timestamp,
                asset=asset,
                transaction_type=TransactionType.BUY,
                amount=qty,
                fiat_value_at_trigger=0.0,
                fee_fiat=0.0,
                counter_asset=counter_for_buy,
                trade_group_id=trade_group_id,
                source=chain,
                token_mint=mints.get(asset),
                on_chain_tx_id=tx_hash,
            )
        )
    return transactions


def _parse_single_transfer(row: dict, *, chain: EvmChain, wallet: str) -> Optional[Transaction]:
    asset = str(row["asset"])
    if is_scam_token_label(asset):
        return None
    direction = "IN" if row["flow"] == "in" else "OUT"
    tx_hash = str(row["hash"])
    tx = _transfer_tx(
        chain=chain,
        tx_hash=tx_hash,
        timestamp=row["timestamp"],
        asset=str(row["asset"]),
        amount=float(row["amount"]),
        direction=direction,
        contract=row.get("contract"),
        counterparty_address=_counterparty_for_row(row, direction),
        trade_group_id=tx_hash,
    )
    return tx


def parse_evm_wallet(
    rows: List[dict],
    *,
    wallet: str,
    chain: EvmChain,
) -> List[Transaction]:
    """Convert normalized EVM transfer rows into ledger transactions."""
    wallet = _normalize_address(wallet)
    native = native_asset_for(chain)
    by_hash: Dict[str, List[dict]] = defaultdict(list)
    fee_rows: Dict[str, dict] = {}

    for row in rows:
        tx_hash = str(row.get("hash") or "")
        if not tx_hash:
            continue
        if row.get("kind") == "fee":
            fee_rows[tx_hash] = row
            continue
        by_hash[tx_hash].append(row)

    transactions: List[Transaction] = []
    for tx_hash, group in by_hash.items():
        trade_group_id = tx_hash
        if len(group) == 1:
            tx = _parse_single_transfer(group[0], chain=chain, wallet=wallet)
            if tx:
                transactions.append(tx)
        else:
            transactions.extend(_parse_swap_group(group, chain=chain, wallet=wallet))

        fee = fee_rows.get(tx_hash)
        if fee and float(fee.get("amount") or 0) > 0:
            fee_asset = str(fee.get("asset") or native)
            transactions.append(
                _fee_tx(
                    chain=chain,
                    tx_hash=tx_hash,
                    timestamp=fee["timestamp"],
                    amount=float(fee["amount"]),
                    asset=fee_asset,
                    trade_group_id=trade_group_id if len(group) >= 1 else None,
                )
            )

    transactions.sort(key=lambda tx: tx.timestamp)
    return transactions
