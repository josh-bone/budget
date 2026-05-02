import argparse
import logging

from dotenv import load_dotenv

from budget.analyze import build_budget
from budget.config import ConfigError, get_env, load_toml
from budget.display import print_section
from budget.sheets import Resource, build_service, fetch_cells, list_sheet_names

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and display budget data from Google Sheets."
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Set the logging level (e.g. DEBUG, INFO, WARNING)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()))

    load_dotenv()
    key_file = get_env("GOOGLE_SERVICE_ACCOUNT_KEY")
    spreadsheet_id = get_env("SPREADSHEET_ID")

    config = load_toml()
    cells_config = config.get("cells", {})
    summary_config = config.get("summary", {})

    if not cells_config:
        raise ConfigError("Error: no [cells.*] sections found in config.toml")

    all_refs = list(
        dict.fromkeys(
            ref for labels in cells_config.values() for ref in labels.values()
        )
    )

    if not all_refs:
        raise ConfigError("Error: no cell references defined in config.toml")

    logger.info("Connecting to Google Sheets...")
    service: Resource = build_service(key_file)

    for sheet in list_sheet_names(
        service,
        spreadsheet_id,
        pattern=config.get("spreadsheet", {}).get("sheet_pattern"),
    ):
        logger.info(f"Fetching {len(all_refs)} cell(s) from sheet '{sheet}'...")
        cell_values = fetch_cells(service, spreadsheet_id, sheet, all_refs)
        budget = build_budget(cells_config, cell_values, summary_config)

        print(f"\n{'═' * 45}")
        print(f"  {sheet}")
        print(f"{'═' * 45}")

        for section_name, rows in budget.items():
            print_section(section_name, rows)


if __name__ == "__main__":
    main()
