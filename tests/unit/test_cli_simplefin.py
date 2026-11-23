import sys
from unittest.mock import patch

import pytest

from budget.cli import main
from budget.config import ConfigError


def test_help_lists_simplefin_commands(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["budget", "--help"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "simplefin-claim" in output
    assert "simplefin" in output
    assert "snapshot" in output


def test_simplefin_claim_prints_access_url_and_private_storage_instruction(
    monkeypatch, capsys
):
    monkeypatch.setattr(sys, "argv", ["budget", "simplefin-claim", "token"])
    with patch(
        "budget.cli.claim_setup_token", return_value="https://bridge.example/u/p"
    ) as claim:
        main()

    claim.assert_called_once_with("token")
    output = capsys.readouterr().out
    assert "https://bridge.example/u/p" in output
    assert "SIMPLEFIN_ACCESS_URL" in output


def test_simplefin_requires_configured_access_url(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["budget", "simplefin"])
    with (
        patch(
            "budget.cli.get_accounts",
            side_effect=ConfigError("missing SIMPLEFIN_ACCESS_URL"),
        ),
        pytest.raises(ConfigError, match="SIMPLEFIN_ACCESS_URL"),
    ):
        main()


def test_snapshot_saves_fetched_accounts(monkeypatch, capsys):
    accounts = [{"name": "Savings", "balance": "12.50"}]
    monkeypatch.setattr(sys, "argv", ["budget", "snapshot"])
    with (
        patch("budget.cli.get_accounts", return_value=accounts),
        patch("budget.cli.insert_balance_snapshots") as insert,
    ):
        main()

    insert.assert_called_once_with(accounts, source="simplefin")
    assert "Saved balance snapshot for 1 account(s)." in capsys.readouterr().out


def test_simplefin_displays_balances_and_raw_ids(monkeypatch, capsys):
    accounts = [{"id": "a1", "name": "Savings", "balance": "12.5"}]
    monkeypatch.setattr(sys, "argv", ["budget", "simplefin"])
    with patch("budget.cli.get_accounts", return_value=accounts) as get:
        main()
    get.assert_called_once_with(include_unmapped=False)
    assert "12.50" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["budget", "simplefin", "--list-raw-ids"])
    with patch("budget.cli.get_accounts", return_value=accounts):
        main()
    assert "a1\tSavings" in capsys.readouterr().out


def test_transactions_command_saves_rows(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["budget", "transactions", "--start-date", "2024-01-01"]
    )
    rows = [{"id": "a1"}]
    with (
        patch("budget.cli.get_transactions", return_value=rows) as fetch,
        patch("budget.cli.insert_simplefin_transactions", return_value=4) as insert,
    ):
        main()
    fetch.assert_called_once_with(start_date="2024-01-01", end_date=None)
    insert.assert_called_once_with(rows)
    assert "Saved 4 transaction(s)" in capsys.readouterr().out


def test_import_balances_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["budget", "import-balances"])
    with (
        patch("budget.cli.get_env", side_effect=["key", "sheet"]),
        patch("budget.cli.build_service", return_value=object()),
        patch("budget.cli.get_balances_df", return_value="balances"),
        patch("budget.cli.import_balance_history", return_value=(2, 1)) as importer,
    ):
        main()
    importer.assert_called_once_with("balances")
