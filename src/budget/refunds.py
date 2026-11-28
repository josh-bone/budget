"""Review suggestions for same-account posted refunds."""

import sqlite3
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

REFUND_MATCH_WINDOW_DAYS = 180


def suggest_refund_pairs(
    connection: sqlite3.Connection, *, window_days: int = REFUND_MATCH_WINDOW_DAYS
) -> list[dict]:
    """Return all exact amount, earlier purchase candidates without mutation."""
    if window_days < 0:
        raise ValueError("window_days must be non-negative")
    rows = connection.execute(
        """SELECT t.account_id, a.name, t.transaction_id,
                  COALESCE(t.posted_date,t.transacted_at), t.amount,t.description,
                  t.pending, c.is_transfer, p.is_transfer
           FROM transactions t JOIN accounts a ON a.id=t.account_id
           LEFT JOIN categories c ON c.id=t.category_id
           LEFT JOIN categories p ON p.id=c.parent_id
           WHERE COALESCE(t.posted_date,t.transacted_at) IS NOT NULL
           ORDER BY COALESCE(t.posted_date,t.transacted_at),t.transaction_id"""
    ).fetchall()
    linked = {
        tuple(r)
        for r in connection.execute(
            "SELECT refund_account_id,refund_transaction_id FROM refunds UNION "
            "SELECT purchase_account_id,purchase_transaction_id FROM refunds"
        )
    }
    linked_transfers = set()
    for row in connection.execute(
        """SELECT account_id,transaction_id FROM transactions t JOIN categories c ON c.id=t.category_id
           LEFT JOIN categories p ON p.id=c.parent_id WHERE c.is_transfer=1 OR p.is_transfer=1"""
    ):
        linked_transfers.add(tuple(row))
    result = []
    for credit in rows:
        if credit[6] or credit[4] <= 0:
            continue
        credit_day = date.fromisoformat(credit[3])
        cents = int(
            (Decimal(str(credit[4])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            * 100
        )
        if (credit[0], credit[2]) in linked or (
            credit[0],
            credit[2],
        ) in linked_transfers:
            continue
        for purchase in rows:
            if purchase[0] != credit[0] or purchase[6] or purchase[4] >= 0:
                continue
            if (purchase[0], purchase[2]) in linked or (
                purchase[0],
                purchase[2],
            ) in linked_transfers:
                continue
            purchase_day = date.fromisoformat(purchase[3])
            days = (credit_day - purchase_day).days
            if days < 0 or days > window_days:
                continue
            purchase_cents = int(
                Decimal(str(abs(purchase[4]))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                * 100
            )
            if cents != purchase_cents:
                continue
            result.append(
                {
                    "purchase": {
                        "account_id": purchase[0],
                        "account": purchase[1],
                        "transaction_id": purchase[2],
                        "date": purchase[3],
                        "amount": purchase[4],
                        "description": purchase[5],
                    },
                    "refund": {
                        "account_id": credit[0],
                        "account": credit[1],
                        "transaction_id": credit[2],
                        "date": credit[3],
                        "amount": credit[4],
                        "description": credit[5],
                    },
                    "days_apart": days,
                }
            )
    return result
