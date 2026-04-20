"""
budget_reader.py — Read and summarize a budget from Google Sheets.

.env (secrets):
  GOOGLE_SERVICE_ACCOUNT_KEY  Path to your service account JSON key file
  SPREADSHEET_ID              The ID from your Google Sheet URL

config.toml (config):
  Defines which sheet tab and which cells to read, organized into sections.
  See config.toml for an example.
"""

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
except ImportError:
    sys.exit(
        "Missing dependencies. Run:\n  pip install google-auth google-auth-httplib2 google-api-python-client"
    )

try:
    from tabulate import tabulate
except ImportError:
    sys.exit("Missing dependency. Run:\n  pip install tabulate")


SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
TOML_PATH = Path(__file__).parents[2] / "config.toml"


# ── Config ─────────────────────────────────────────────────────────────────────


class ConfigError(Exception):
    pass


def get_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise ValueError(f"Error: environment variable '{key}' is not set.")
    return value


def load_toml() -> dict[str, Any]:
    if not TOML_PATH.exists():
        raise FileNotFoundError(f"Error: config file not found at {TOML_PATH}")
    with open(TOML_PATH, "rb") as f:
        return tomllib.load(f)


# ── Google Sheets ──────────────────────────────────────────────────────────────


def build_service(key_file: str):
    creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def fetch_cells(
    service, spreadsheet_id: str, sheet: str, cell_refs: list[str]
) -> dict[str, str]:
    """Fetch a list of individual cell references in a single batch request."""
    ranges = [f"{sheet}!{ref}" for ref in cell_refs]
    result = (
        service.spreadsheets()
        .values()
        .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
        .execute()
    )
    values = {}
    for i, ref in enumerate(cell_refs):
        range_data = result["valueRanges"][i]
        # A cell with a value returns [["value"]], an empty cell returns {}
        try:
            values[ref] = range_data["values"][0][0]
        except (KeyError, IndexError):
            values[ref] = ""
    return values


# ── Display ────────────────────────────────────────────────────────────────────


def print_section(
    section_name: str, labels: dict[str, str], cell_values: dict[str, str]
):
    print(f"\n  {section_name.upper()}")
    rows = [
        [label, cell_ref, cell_values.get(cell_ref, "")]
        for label, cell_ref in labels.items()
    ]
    print(
        tabulate(rows, headers=["Label", "Cell", "Value"], tablefmt="rounded_outline")
    )


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    # Environment variables
    load_dotenv()
    key_file = get_env("GOOGLE_SERVICE_ACCOUNT_KEY")
    spreadsheet_id = get_env("SPREADSHEET_ID")

    config = load_toml()

    sheet = config.get("spreadsheet", {}).get("sheet")
    if not sheet:
        raise ConfigError(
            "Error: [spreadsheet] sheet = '...' is missing from config.toml"
        )

    cells_config: dict[str, dict[str, str]] = config.get("cells", {})
    if not cells_config:
        raise ConfigError("Error: no [cells.*] sections found in config.toml")
    # Collect all cell references across all sections
    all_refs: list[str] = []
    for labels in cells_config.values():
        all_refs.extend(labels.values())

    if not all_refs:
        raise ConfigError("Error: no cell references defined in config.toml")

    print("Connecting to Google Sheets...")
    service = build_service(key_file)

    print(f"Fetching {len(all_refs)} cell(s) from sheet '{sheet}'...")
    cell_values = fetch_cells(service, spreadsheet_id, sheet, all_refs)

    print(f"\n{'═' * 45}")
    print("  📊  Budget Summary")
    print(f"{'═' * 45}")

    for section_name, labels in cells_config.items():
        print_section(section_name, labels, cell_values)

    print()


if __name__ == "__main__":
    main()
