from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from budget.sheets import build_service, fetch_cells, get_balances_df, list_sheet_names


def test_build_service_reads_timeout_at_call_time(monkeypatch):
    monkeypatch.setenv("BUDGET_FETCH_TIMEOUT", "37")
    with (
        patch("budget.sheets.service_account.Credentials.from_service_account_file"),
        patch("budget.sheets.httplib2.Http") as http,
        patch("budget.sheets.AuthorizedHttp"),
        patch("budget.sheets.build"),
    ):
        build_service("unused.json")
        http.assert_called_once_with(timeout=37)
        http.reset_mock()
        build_service("unused.json", timeout=8)
        http.assert_called_once_with(timeout=8)

# ── Helpers ────────────────────────────────────────────────────────────────────


def make_service(
    sheet_titles: list[str] | None = None, batch_values: list | None = None
) -> MagicMock:
    """
    Build a minimal mock of the Google Sheets API service.

    sheet_titles  — tab names returned by spreadsheets().get()
    batch_values  — list of valueRanges entries returned by batchGet()
    """
    service = MagicMock()

    # spreadsheets().get().execute()
    sheets_meta = {
        "sheets": [{"properties": {"title": t}} for t in (sheet_titles or [])]
    }
    service.spreadsheets.return_value.get.return_value.execute.return_value = (
        sheets_meta
    )

    # spreadsheets().values().batchGet().execute()
    batch_result = {"valueRanges": batch_values or []}
    (
        service.spreadsheets.return_value.values.return_value.batchGet.return_value.execute.return_value
    ) = batch_result

    return service


# ── list_sheet_names ───────────────────────────────────────────────────────────


def test_list_sheet_names_returns_all_when_no_pattern():
    service = make_service(["January", "February", "Summary"])
    result = list_sheet_names(service, "spreadsheet-id")
    assert result == ["January", "February", "Summary"]


def test_list_sheet_names_filters_by_pattern():
    service = make_service(["1/2026", "2/2026", "Summary", "Template"])
    result = list_sheet_names(service, "spreadsheet-id", pattern=r"^\d{1,2}/\d{4}$")
    assert result == ["1/2026", "2/2026"]


def test_list_sheet_names_returns_empty_when_nothing_matches():
    service = make_service(["Summary", "Template"])
    result = list_sheet_names(service, "spreadsheet-id", pattern=r"^\d{1,2}/\d{4}$")
    assert result == []


def test_list_sheet_names_returns_empty_spreadsheet():
    service = make_service([])
    result = list_sheet_names(service, "spreadsheet-id")
    assert result == []


def test_list_sheet_names_passes_spreadsheet_id():
    service = make_service(["Sheet1"])
    list_sheet_names(service, "my-spreadsheet-id")
    service.spreadsheets.return_value.get.assert_called_once_with(
        spreadsheetId="my-spreadsheet-id"
    )


def test_get_balances_df_reads_large_range():
    _sheet_len = 10002
    _vals = [["Date", "Account", "Amount", "Notes / Usage"]]
    for i in range(_sheet_len):
        _vals.append([str(i), str(i), str(i)])
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": _vals
    }
    balances = get_balances_df(service, "spreadsheet-id")

    assert len(balances) == _sheet_len


def test_get_balances_df_pads_omitted_trailing_cells_and_coerces_blank_amounts():
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": [
            ["Date", "Account", "Amount", "Notes / Usage"],
            ["1/2/2026", "Checking", "$1,234.56"],
            ["1/3/2026", "Savings", ""],
        ]
    }

    balances = get_balances_df(service, "spreadsheet-id")

    assert balances.columns.tolist() == ["Date", "Account", "Amount", "Notes / Usage"]
    assert balances.loc[0, "Amount"] == 1234.56
    assert balances["Notes / Usage"].isna().all()
    assert pd.isna(balances.loc[1, "Amount"])


# ── fetch_cells ────────────────────────────────────────────────────────────────


def _value_range(value: str | None):
    """Build a valueRanges entry as the API would return it."""
    if value is None:
        return {}  # empty cell — no 'values' key
    return {"values": [[value]]}


def test_fetch_cells_returns_parsed_floats():
    service = make_service(
        batch_values=[
            _value_range("5000"),
            _value_range("4000"),
        ]
    )
    result = fetch_cells(service, "sid", "Sheet1", ["F4", "F5"])
    assert result == {"F4": 5000.0, "F5": 4000.0}


def test_fetch_cells_handles_comma_formatted_numbers():
    service = make_service(batch_values=[_value_range("1,234.56")])
    result = fetch_cells(service, "sid", "Sheet1", ["A1"])
    assert result["A1"] == pytest.approx(1234.56)


def test_fetch_cells_returns_none_for_empty_cell():
    service = make_service(batch_values=[_value_range(None)])
    result = fetch_cells(service, "sid", "Sheet1", ["A1"])
    assert result["A1"] is None


def test_fetch_cells_mixed_present_and_empty():
    service = make_service(
        batch_values=[
            _value_range("100"),
            _value_range(None),
            _value_range("200"),
        ]
    )
    result = fetch_cells(service, "sid", "Sheet1", ["A1", "A2", "A3"])
    assert result["A1"] == 100.0
    assert result["A2"] is None
    assert result["A3"] == 200.0


def test_fetch_cells_sends_correct_ranges():
    service = make_service(batch_values=[_value_range("1"), _value_range("2")])
    fetch_cells(service, "my-sid", "Budget", ["F4", "O16"])
    service.spreadsheets.return_value.values.return_value.batchGet.assert_called_once_with(
        spreadsheetId="my-sid",
        ranges=["Budget!F4", "Budget!O16"],
    )


def test_fetch_cells_empty_refs_returns_empty_dict():
    service = make_service(batch_values=[])
    result = fetch_cells(service, "sid", "Sheet1", [])
    assert result == {}
