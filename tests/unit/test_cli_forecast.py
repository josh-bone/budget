import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from budget.cli import main


@pytest.mark.parametrize(
    ("sheet_names", "latest"),
    [
        (["12/2026", "9/2026"], "12/2026"),
        (["9/2026", "1/2027", "12/2026"], "1/2027"),
    ],
)
def test_forecast_fetches_latest_month(monkeypatch, sheet_names, latest):
    service = object()
    fetch = Mock(return_value={})
    result = SimpleNamespace(
        initial_balance=0,
        monthly_contribution=0,
        config=SimpleNamespace(target_real_balance=0),
        probability_of_reaching_target=0,
        summary=Mock(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "budget",
            "retirement-forecast",
            "--initial-balance",
            "0",
            "--monthly-contribution",
            "0",
        ],
    )
    monkeypatch.setattr("budget.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("budget.cli.get_env", lambda name: "sheet-or-key")
    monkeypatch.setattr(
        "budget.cli.load_toml", lambda: {"cells": {"income": {"pay": "A1"}}}
    )
    monkeypatch.setattr("budget.cli.build_service", lambda _: service)
    monkeypatch.setattr("budget.cli.list_sheet_names", lambda *a, **k: sheet_names)
    monkeypatch.setattr("budget.cli.fetch_cells", fetch)
    monkeypatch.setattr("budget.cli.build_budget", lambda *a: {})
    monkeypatch.setattr("budget.cli.run_retirement_forecast", lambda **k: result)

    main()

    fetch.assert_called_once_with(service, "sheet-or-key", latest, ["A1"])
