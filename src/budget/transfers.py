"""On-demand suggestions for possible transfers between mapped accounts."""

import sqlite3
from bisect import bisect_left, bisect_right
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

TRANSFER_MATCH_WINDOW_DAYS = 10


def transfer_pair_key(source: dict, candidate: dict) -> tuple[str, str, str, str]:
    """Return a stable, account/transaction ordered key for a pair."""
    first = (str(source["account_id"]), str(source["transaction_id"]))
    second = (str(candidate["account_id"]), str(candidate["transaction_id"]))
    first, second = sorted((first, second))
    return first[0], first[1], second[0], second[1]


def suggest_transfer_pairs(
    connection: sqlite3.Connection, *, window_days: int | None = None
) -> list[dict]:
    """Return every plausible posted transaction pair without changing categories.

    Pair candidates must have opposite signs, equal cent amounts, different
    accounts, and dates within ``window_days``. Transactions that are pending,
    manually categorized, or undated are excluded. Ambiguous transactions keep
    all matching partners in the returned list.
    """
    if window_days is None:
        window_days = TRANSFER_MATCH_WINDOW_DAYS
    if window_days < 0:
        raise ValueError("window_days must be non-negative")

    rows = connection.execute(
        """SELECT t.account_id, a.name, t.transaction_id,
                  COALESCE(t.posted_date, t.transacted_at) AS transaction_date,
                  t.amount, t.description
           FROM transactions t JOIN accounts a ON a.id=t.account_id
           WHERE t.pending=0 AND t.is_manual_category=0
             AND COALESCE(t.posted_date, t.transacted_at) IS NOT NULL
             AND t.amount != 0
           ORDER BY transaction_date, t.account_id, t.transaction_id"""
    ).fetchall()
    rejection_table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transfer_rejections'"
    ).fetchone()
    rejected_pairs = set()
    if rejection_table_exists:
        rejected_pairs = {
            (row[0], row[1], row[2], row[3])
            for row in connection.execute(
                "SELECT account_id_a, transaction_id_a, account_id_b, transaction_id_b "
                "FROM transfer_rejections"
            )
        }

    records = []
    by_amount_and_sign: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for row in rows:
        transaction_date = date.fromisoformat(row[3])
        amount_cents = int(
            abs(Decimal(str(row[4]))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            * 100
        )
        if amount_cents == 0:
            continue
        sign = 1 if row[4] > 0 else -1
        record_index = len(records)
        records.append(
            {
                "account_id": row[0],
                "account": row[1],
                "transaction_id": row[2],
                "date": transaction_date.isoformat(),
                "amount": float(row[4]),
                "description": row[5],
                "_day": transaction_date.toordinal(),
                "_amount_cents": amount_cents,
                "_sign": sign,
            }
        )
        by_amount_and_sign.setdefault((amount_cents, sign), []).append(
            (transaction_date.toordinal(), record_index)
        )

    pair_buckets = {
        key: [day for day, _ in bucket] for key, bucket in by_amount_and_sign.items()
    }
    pairs = []
    for source_index, source in enumerate(records):
        opposite_key = (source["_amount_cents"], -source["_sign"])
        opposite = by_amount_and_sign.get(opposite_key, [])
        candidate_days = pair_buckets.get(opposite_key, [])
        first = bisect_left(candidate_days, source["_day"] - window_days)
        last = bisect_right(candidate_days, source["_day"] + window_days)
        for _, candidate_index in opposite[first:last]:
            if candidate_index <= source_index:
                continue
            candidate = records[candidate_index]
            if source["account_id"] == candidate["account_id"]:
                continue
            debit, credit = (
                (source, candidate) if source["_sign"] < 0 else (candidate, source)
            )
            debit_public = {
                key: value for key, value in debit.items() if not key.startswith("_")
            }
            credit_public = {
                key: value for key, value in credit.items() if not key.startswith("_")
            }
            if transfer_pair_key(debit_public, credit_public) in rejected_pairs:
                continue
            pairs.append(
                {
                    "source": debit_public,
                    "candidate": credit_public,
                    "days_apart": abs(source["_day"] - candidate["_day"]),
                }
            )

    return pairs
