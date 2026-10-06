"""Reseller wallets: a running credit the reseller spends on accounts.

The wallet is a statement, not a column: every movement -- the operator's
top-up, the debit for a batch of accounts -- is one ``WalletTx`` row whose
``BalanceAfter`` snapshots the running balance. Reading the balance is
reading the newest row; there is no number anywhere else to drift out of
step with the history.
"""
from __future__ import annotations

from typing import Any

from ..audit import record
from ..db import get_db, utc_now

Wire = dict[str, Any]


def balance(user_id: int) -> float:
    row = get_db().query_one(
        'SELECT "BalanceAfter" FROM "WalletTx" WHERE "UserID" = :u '
        'ORDER BY "WalletTxID" DESC LIMIT 1',
        {"u": user_id},
    )
    return float(row["BalanceAfter"]) if row else 0.0


def apply(user_id: int, amount: float, note: str = "", by: str = "",
          actor: dict | None = None, action: str = "wallet.changed") -> float:
    """One wallet movement. A balance may never go below zero -- a debit
    that would overdraft raises ``ValueError`` and writes nothing."""
    amount = round(float(amount), 2)
    new = round(balance(user_id) + amount, 2)
    if new < -0.005:
        raise ValueError("Not enough credit in the wallet for this purchase.")
    get_db().execute(
        'INSERT INTO "WalletTx"("UserID", "Amount", "BalanceAfter", "Note", '
        '"CreatedBy", "CreatedDate") VALUES (:u, :a, :b, :n, :by, :now)',
        {"u": user_id, "a": amount, "b": new, "n": note, "by": by, "now": utc_now()},
    )
    record(actor, action, "panel_user", str(user_id), f"{amount:+g} -> {new:g} ({note})")
    return new


def transactions(user_id: int, limit: int = 50) -> list[dict]:
    rows = get_db().query_all(
        'SELECT * FROM "WalletTx" WHERE "UserID" = :u '
        'ORDER BY "WalletTxID" DESC LIMIT :n',
        {"u": user_id, "n": limit},
    )
    return [dict(r) for r in rows]
