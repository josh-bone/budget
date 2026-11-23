from datetime import date, timedelta

import pytest

from budget.balances_db import _connect
from budget.transfers import TRANSFER_MATCH_WINDOW_DAYS, suggest_transfer_pairs


def _database(tmp_path):
    connection = _connect(tmp_path / "transfers.sqlite3")
    connection.executemany(
        "INSERT INTO accounts(id, name) VALUES (?, ?)",
        [("a", "Checking"), ("b", "Savings"), ("c", "Credit card")],
    )
    category_id = connection.execute(
        "INSERT INTO categories(name) VALUES ('Food')"
    ).lastrowid
    connection.commit()
    return connection, category_id


def _transaction(
    connection,
    account,
    transaction_id,
    amount,
    day,
    *,
    pending=0,
    manual=0,
    category_id=None,
):
    connection.execute(
        """INSERT INTO transactions
           (account_id, transaction_id, posted_date, amount, description,
            pending, category_id, is_manual_category)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            account,
            transaction_id,
            day,
            amount,
            f"Transaction {transaction_id}",
            pending,
            category_id,
            manual,
        ),
    )


def _day(offset):
    return (date(2026, 1, 1) + timedelta(days=offset)).isoformat()


def test_suggests_exact_opposite_pair_without_mutating_categories(tmp_path):
    connection, category_id = _database(tmp_path)
    _transaction(connection, "a", "out", -12.34, _day(2), category_id=category_id)
    _transaction(connection, "b", "in", 12.34, _day(5))

    pairs = suggest_transfer_pairs(connection)

    assert len(pairs) == 1
    assert pairs[0]["source"]["transaction_id"] == "out"
    assert pairs[0]["candidate"]["transaction_id"] == "in"
    assert pairs[0]["days_apart"] == 3
    assert connection.execute(
        "SELECT category_id, is_manual_category FROM transactions WHERE transaction_id='out'"
    ).fetchone() == (category_id, 0)
    connection.close()


def test_returns_no_pair_for_same_account_sign_or_different_amount(tmp_path):
    connection, _ = _database(tmp_path)
    _transaction(connection, "a", "a1", -10, _day(1))
    _transaction(connection, "a", "a2", 10, _day(1))
    _transaction(connection, "b", "b1", -12, _day(1))
    _transaction(connection, "c", "c1", 11, _day(1))

    assert suggest_transfer_pairs(connection) == []
    connection.close()


def test_surfaces_all_ambiguous_candidates(tmp_path):
    connection, _ = _database(tmp_path)
    _transaction(connection, "a", "out", -25, _day(5))
    _transaction(connection, "b", "in-1", 25, _day(4))
    _transaction(connection, "c", "in-2", 25, _day(6))

    pairs = suggest_transfer_pairs(connection)

    assert {
        pair["candidate"]["transaction_id"]
        for pair in pairs
        if pair["source"]["transaction_id"] == "out"
    } == {"in-1", "in-2"}
    connection.close()


def test_includes_window_boundary_but_excludes_outside_it(tmp_path):
    connection, _ = _database(tmp_path)
    _transaction(connection, "a", "out", -8, _day(0))
    _transaction(connection, "b", "in-boundary", 8, _day(10))
    _transaction(connection, "c", "in-outside", 8, _day(11))

    pairs = suggest_transfer_pairs(connection)

    assert TRANSFER_MATCH_WINDOW_DAYS == 10
    assert [pair["candidate"]["transaction_id"] for pair in pairs] == ["in-boundary"]
    connection.close()


def test_excludes_manually_categorized_and_pending_transactions(tmp_path):
    connection, category_id = _database(tmp_path)
    _transaction(connection, "a", "manual-out", -5, _day(0), manual=1)
    _transaction(connection, "b", "manual-in", 5, _day(0))
    _transaction(connection, "a", "pending-out", -7, _day(1), pending=1)
    _transaction(connection, "b", "pending-in", 7, _day(1))
    _transaction(connection, "a", "eligible-out", -9, _day(2), category_id=category_id)
    _transaction(connection, "c", "eligible-in", 9, _day(2))

    pairs = suggest_transfer_pairs(connection)

    assert [
        (p["source"]["transaction_id"], p["candidate"]["transaction_id"]) for p in pairs
    ] == [("eligible-out", "eligible-in")]
    connection.close()


def test_window_must_not_be_negative(tmp_path):
    connection, _ = _database(tmp_path)

    with pytest.raises(ValueError, match="non-negative"):
        suggest_transfer_pairs(connection, window_days=-1)

    connection.close()
