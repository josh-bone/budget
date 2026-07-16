"""
sheets.py — Google Sheets access layer.
"""

import re

import pandas as pd
from pandas.api.types import is_string_dtype

from budget.utils import cast_to_float

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import Resource, build
except ImportError:
    raise ImportError(
        "Missing dependencies. Run:\n"
        "  pip install google-auth google-auth-httplib2 google-api-python-client"
    )

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def build_service(key_file: str) -> Resource:
    creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def get_balances_df(service: Resource, spreadsheet_id: str) -> pd.DataFrame:
    sheet = "Balances"
    range_ = f"{sheet}!A1:Z1000"
    response = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=range_,
        )
        .execute()
    )
    values = response.get("values", [])

    df = pd.DataFrame(values[1:], columns=values[0])

    # Convert Amount to numeric
    if "Amount" in df.columns:
        if is_string_dtype(df["Amount"]):
            df["Amount"] = (
                df["Amount"].str.replace("$", "").str.replace(",", "").astype(float)
            )  # Assumes currency format like "$1,234.56"

    return df


def list_sheet_names(
    service: Resource, spreadsheet_id: str, pattern: str | None = None
) -> list[str]:
    """
    Return all tab names in the spreadsheet.
    If pattern is provided, only return tabs whose names match the regex.
    """
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    names = [s["properties"]["title"] for s in meta["sheets"]]
    if pattern:
        rx = re.compile(pattern)
        names = [n for n in names if rx.match(n)]
    return names


def parse_value(v: str | None) -> float | None:
    if not v:
        return None
    return cast_to_float(v)


def fetch_cells(
    service: Resource, spreadsheet_id: str, sheet: str, cell_refs: list[str]
) -> dict[str, float | None]:
    """Fetch a list of individual cell references in a single batch request."""
    ranges = [f"{sheet}!{ref}" for ref in cell_refs]
    result = (
        service.spreadsheets()
        .values()
        .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
        .execute()
    )
    values: dict[str, float | None] = {}
    for i, ref in enumerate(cell_refs):
        range_data = result["valueRanges"][i]
        try:
            values[ref] = parse_value(range_data["values"][0][0])
        except (KeyError, IndexError):
            values[ref] = None
    return values
