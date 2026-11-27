"""Account closing workflows and balance-history behavior."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from budget.balances_db import _connect

_APP_PATH = Path(__file__).resolve().parents[2] / "app.py"
_APP_SPEC = importlib.util.spec_from_file_location("budget_closure_test_app", _APP_PATH)
assert _APP_SPEC is not None and _APP_SPEC.loader is not None
web_app = importlib.util.module_from_spec(_APP_SPEC)
_APP_SPEC.loader.exec_module(web_app)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGET_DB_PATH", str(tmp_path / "closure.sqlite3"))
    monkeypatch.setitem(web_app.app.config, "TESTING", True)
    return web_app.app.test_client()


def _account(db_path, *, name="Checking", account_id="checking", closed_date=None):
    connection = _connect(db_path)
    connection.execute(
        "INSERT INTO accounts(id, name, closed_date) VALUES (?, ?, ?)",
        (account_id, name, closed_date),
    )
    connection.commit()
    connection.close()


def _snapshot(db_path, account, snapshot_date, amount, source="manual"):
    connection = _connect(db_path)
    connection.execute(
        "INSERT INTO balance_snapshots(account, date, amount, source) VALUES (?, ?, ?, ?)",
        (account, snapshot_date, amount, source),
    )
    connection.commit()
    connection.close()


def _stored_account(db_path, account_id="checking"):
    connection = _connect(db_path)
    row = connection.execute(
        "SELECT id, name, closed_date FROM accounts WHERE id=?", (account_id,)
    ).fetchone()
    connection.close()
    return row


def _stored_snapshot(db_path, account, snapshot_date):
    connection = _connect(db_path)
    row = connection.execute(
        "SELECT amount, source FROM balance_snapshots WHERE account=? AND date=?",
        (account, snapshot_date),
    ).fetchone()
    connection.close()
    return row


def test_database_migrates_existing_accounts_table(tmp_path):
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE accounts(id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    connection.execute("INSERT INTO accounts VALUES ('old', 'Old account')")
    connection.commit()
    connection.close()

    connection = _connect(path)

    columns = {row[1] for row in connection.execute("PRAGMA table_info(accounts)")}
    assert "closed_date" in columns
    assert connection.execute(
        "SELECT closed_date FROM accounts WHERE id='old'"
    ).fetchone() == (None,)
    connection.close()


def test_account_row_has_close_action_and_form_requests_closing_date(client, tmp_path):
    _account(tmp_path / "closure.sqlite3")

    accounts = client.get("/accounts")
    form = client.get("/accounts/checking/close")

    assert accounts.status_code == 200
    assert b"Close" in accounts.data
    assert form.status_code == 200
    assert b'name="closed_date"' in form.data
    assert b'type="date"' in form.data


def test_zero_balance_on_closing_date_closes_without_confirmation(client, tmp_path):
    path = tmp_path / "closure.sqlite3"
    _account(path)
    _snapshot(path, "Checking", "2026-11-20", 0.0)

    response = client.post(
        "/accounts/checking/close", data={"closed_date": "2026-11-20"}
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/accounts")
    assert _stored_account(path)[2] == "2026-11-20"
    assert _stored_snapshot(path, "Checking", "2026-11-20") == (0.0, "manual")
    assert b"Confirm" not in client.get("/accounts/checking/close").data


def test_nonzero_balance_prompts_and_yes_zeroes_alias_snapshot(client, tmp_path):
    path = tmp_path / "closure.sqlite3"
    _account(path)
    connection = _connect(path)
    connection.execute(
        "INSERT INTO account_aliases(account_key, alias) VALUES ('checking', 'Old Checking')"
    )
    connection.commit()
    connection.close()
    _snapshot(path, "Old Checking", "2026-11-20", 124.5, "simplefin")

    prompt = client.post("/accounts/checking/close", data={"closed_date": "2026-11-20"})
    assert prompt.status_code == 200
    assert b"$124.50" in prompt.data
    assert b"Did you zero the account's balance" in prompt.data
    assert _stored_account(path)[2] is None

    response = client.post(
        "/accounts/checking/close",
        data={"closed_date": "2026-11-20", "zeroed": "yes"},
    )

    assert response.status_code == 302
    assert _stored_account(path)[2] == "2026-11-20"
    assert _stored_snapshot(path, "Old Checking", "2026-11-20") == (0.0, "simplefin")


def test_nonzero_balance_can_be_preserved_when_user_did_not_zero_it(client, tmp_path):
    path = tmp_path / "closure.sqlite3"
    _account(path)
    _snapshot(path, "Checking", "2026-11-20", 20.0)

    response = client.post(
        "/accounts/checking/close",
        data={"closed_date": "2026-11-20", "zeroed": "no"},
    )

    assert response.status_code == 302
    assert _stored_account(path)[2] == "2026-11-20"
    assert _stored_snapshot(path, "Checking", "2026-11-20") == (20.0, "manual")


def test_missing_closing_day_balance_prompts_then_inserts_zero(client, tmp_path):
    path = tmp_path / "closure.sqlite3"
    _account(path)

    prompt = client.post("/accounts/checking/close", data={"closed_date": "2026-11-20"})
    assert prompt.status_code == 200
    assert b"There is no balance snapshot" in prompt.data

    response = client.post(
        "/accounts/checking/close",
        data={"closed_date": "2026-11-20", "zeroed": "yes"},
    )

    assert response.status_code == 302
    assert _stored_account(path)[2] == "2026-11-20"
    assert _stored_snapshot(path, "Checking", "2026-11-20") == (0.0, "manual")


@pytest.mark.parametrize("close_date", ["", "not-a-date", "2026-02-30"])
def test_invalid_closing_date_does_not_close_account(client, tmp_path, close_date):
    path = tmp_path / "closure.sqlite3"
    _account(path)

    response = client.post("/accounts/checking/close", data={"closed_date": close_date})

    assert response.status_code == 400
    assert b"valid closing date" in response.data
    assert _stored_account(path)[2] is None


def test_closed_and_unknown_accounts_cannot_be_closed_again(client, tmp_path):
    path = tmp_path / "closure.sqlite3"
    _account(path, closed_date="2026-11-20")

    assert client.get("/accounts/checking/close").status_code == 302
    assert (
        client.post(
            "/accounts/missing/close", data={"closed_date": "2026-11-20"}
        ).status_code
        == 404
    )


def test_closed_account_is_hidden_but_history_is_preserved(client, tmp_path):
    path = tmp_path / "closure.sqlite3"
    _account(path)
    _snapshot(path, "Checking", "2026-11-20", 0.0)
    client.post("/accounts/checking/close", data={"closed_date": "2026-11-20"})

    response = client.get("/accounts")

    assert response.status_code == 200
    assert b"<strong>Checking</strong>" not in response.data
    assert _stored_snapshot(path, "Checking", "2026-11-20") == (0.0, "manual")


def test_closed_account_is_not_marked_stale_on_balances(client, tmp_path):
    path = tmp_path / "closure.sqlite3"
    _account(path, closed_date="2026-11-20")
    _account(path, name="Savings", account_id="savings")
    _snapshot(path, "Checking", "2026-11-20", 0.0)
    _snapshot(path, "Savings", "2026-11-27", 10.0)

    response = client.get("/balances")

    assert response.status_code == 200
    assert b"Checking" in response.data
    assert b"Stale" not in response.data
