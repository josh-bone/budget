import argparse
import logging

from dotenv import load_dotenv

from budget.analyze import build_budget
from budget.balances_db import (
    import_balance_history,
    insert_balance_snapshots,
    insert_simplefin_transactions,
)
from budget.config import ConfigError, get_env, load_toml
from budget.display import print_section
from budget.retirement_forecast import (
    RetirementForecastConfig,
    get_current_retirement_savings_contributions,
    get_total_retirement_savings_balance,
    run_retirement_forecast,
)
from budget.sheets import (
    Resource,
    build_service,
    fetch_cells,
    get_balances_df,
    list_sheet_names,
)
from budget.simplefin import claim_setup_token, get_accounts, get_transactions
from budget.utils import month_sheet_sort_key

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch budget data from Google Sheets or run the retirement forecast."
    )
    subparsers = parser.add_subparsers(dest="command")

    sheets_parser = subparsers.add_parser(
        "sheets",
        help="Fetch and display monthly budget data.",
    )
    sheets_parser.set_defaults(command="sheets")

    forecast_parser = subparsers.add_parser(
        "retirement-forecast",
        help="Run the retirement Monte Carlo forecast.",
    )
    forecast_parser.add_argument(
        "--csv-path",
        help="Path to the historical returns/inflation CSV.",
    )

    claim_parser = subparsers.add_parser(
        "simplefin-claim",
        help="Claim a SimpleFIN setup token and print the access URL.",
    )
    claim_parser.add_argument("setup_token", help="One-time SimpleFIN setup token.")

    simplefin_parser = subparsers.add_parser(
        "simplefin",
        help="Fetch and display current SimpleFIN account balances.",
    )
    simplefin_parser.add_argument(
        "--list-raw-ids",
        action="store_true",
        help="List account IDs from SimpleFIN without requiring a config mapping.",
    )
    transactions_parser = subparsers.add_parser(
        "transactions",
        help="Fetch and save recent SimpleFIN transactions.",
    )
    transactions_parser.add_argument(
        "--start-date", help="Inclusive start date (YYYY-MM-DD)."
    )
    transactions_parser.add_argument(
        "--end-date", help="Exclusive end date (YYYY-MM-DD)."
    )

    subparsers.add_parser(
        "snapshot",
        help="Fetch current SimpleFIN balances and save a daily snapshot.",
    )
    subparsers.add_parser(
        "import-balances",
        help="Import historical balances from the Balances sheet into SQLite.",
    )
    forecast_parser.add_argument(
        "--initial-balance",
        type=float,
        help="Override the resolved retirement balance.",
    )
    forecast_parser.add_argument(
        "--monthly-contribution",
        type=float,
        help="Override the resolved monthly retirement contribution.",
    )
    forecast_parser.add_argument(
        "--account-patterns",
        help="Comma-separated account name patterns to match in the latest SQLite balance snapshot.",
    )
    forecast_parser.add_argument(
        "--contribution-patterns",
        help="Comma-separated label patterns to match in the latest budget.",
    )
    forecast_parser.add_argument(
        "--contribution-fields",
        help="Comma-separated exact labels to sum from the latest budget.",
    )
    forecast_parser.add_argument(
        "--summary-keys",
        help="Comma-separated summary keys to check before scanning budget rows.",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Set the logging level (e.g. DEBUG, INFO, WARNING)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()))

    load_dotenv()

    if args.command == "simplefin-claim":
        access_url = claim_setup_token(args.setup_token)
        print("SimpleFIN access URL (store it securely as SIMPLEFIN_ACCESS_URL):")
        print(access_url)
        print("For example, add it to your private environment configuration.")
        return

    if args.command in ("simplefin", "snapshot"):
        accounts = get_accounts(
            include_unmapped=bool(getattr(args, "list_raw_ids", False))
        )
        if args.command == "snapshot":
            # A daily fetch is a snapshot of what SimpleFIN returned today.
            # Its per-account ``date`` is the institution's balance as-of date,
            # which can remain unchanged across many fetches and would cause
            # each run to overwrite the previous snapshot.
            snapshots = [
                {"name": account["name"], "balance": account["balance"]}
                for account in accounts
            ]
            insert_balance_snapshots(snapshots, source="simplefin")
            print(f"Saved balance snapshot for {len(accounts)} account(s).")
            return
        if not accounts:
            print("No mapped SimpleFIN accounts found.")
            return
        if getattr(args, "list_raw_ids", False):
            for account in accounts:
                print(f"{account['id']}\t{account['name']}")
            return
        print(f"{'Account':<36} {'Balance':>14}")
        for account in accounts:
            print(f"{account['name']:<36} ${float(account['balance']):>13,.2f}")
        return

    if args.command == "transactions":
        accounts = get_transactions(start_date=args.start_date, end_date=args.end_date)
        count = insert_simplefin_transactions(accounts)
        print(f"Saved {count} transaction(s) across {len(accounts)} mapped account(s).")
        return

    key_file = get_env("GOOGLE_SERVICE_ACCOUNT_KEY")
    spreadsheet_id = get_env("SPREADSHEET_ID")

    if args.command == "import-balances":
        logger.info("Fetching balance history from Google Sheets...")
        service: Resource = build_service(key_file)
        balances = get_balances_df(service, spreadsheet_id)
        imported, skipped = import_balance_history(balances)
        logger.info(
            "Balance import complete: %d imported, %d skipped.", imported, skipped
        )
        return

    config = load_toml()
    cells_config = config.get("cells", {})
    summary_config = config.get("summary", {})
    sheet_pattern = config.get("spreadsheet", {}).get("sheet_pattern")

    if args.command in (None, "sheets"):
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

        for sheet in list_sheet_names(service, spreadsheet_id, pattern=sheet_pattern):
            logger.info(f"Fetching {len(all_refs)} cell(s) from sheet '{sheet}'...")
            cell_values = fetch_cells(service, spreadsheet_id, sheet, all_refs)
            budget = build_budget(cells_config, cell_values, summary_config)

            print(f"\n{'═' * 45}")
            print(f"  {sheet}")
            print(f"{'═' * 45}")

            for section_name, rows in budget.items():
                print_section(section_name, rows)
        return

    if args.command == "retirement-forecast":
        forecast_config = RetirementForecastConfig(
            csv_path=args.csv_path or RetirementForecastConfig().csv_path,
            current_age=RetirementForecastConfig().current_age,
            retirement_age=RetirementForecastConfig().retirement_age,
            life_expectancy=RetirementForecastConfig().life_expectancy,
            block_length_months=RetirementForecastConfig().block_length_months,
            n_simulations=RetirementForecastConfig().n_simulations,
            random_seed=RetirementForecastConfig().random_seed,
            target_real_balance=RetirementForecastConfig().target_real_balance,
            contribution_mode=RetirementForecastConfig().contribution_mode,
        )

        service: Resource = build_service(key_file)
        sheet_names = list_sheet_names(service, spreadsheet_id, pattern=sheet_pattern)
        sheet_names.sort(key=month_sheet_sort_key)
        latest_sheet = sheet_names[-1] if sheet_names else None
        latest_budget = None
        if latest_sheet and cells_config:
            all_refs = list(
                dict.fromkeys(
                    ref for labels in cells_config.values() for ref in labels.values()
                )
            )
            if all_refs:
                logger.info(f"Fetching latest budget sheet '{latest_sheet}'...")
                cell_values = fetch_cells(
                    service, spreadsheet_id, latest_sheet, all_refs
                )
                latest_budget = build_budget(cells_config, cell_values, summary_config)

        account_patterns = (
            tuple(s.strip() for s in args.account_patterns.split(",") if s.strip())
            if args.account_patterns
            else None
        )
        contribution_patterns = (
            tuple(s.strip() for s in args.contribution_patterns.split(",") if s.strip())
            if args.contribution_patterns
            else None
        )
        contribution_fields = (
            tuple(s.strip() for s in args.contribution_fields.split(",") if s.strip())
            if args.contribution_fields
            else None
        )
        summary_keys = (
            tuple(s.strip() for s in args.summary_keys.split(",") if s.strip())
            if args.summary_keys
            else None
        )

        result = run_retirement_forecast(
            config=forecast_config,
            initial_balance=args.initial_balance
            if args.initial_balance is not None
            else get_total_retirement_savings_balance(
                account_patterns=account_patterns,
            ),
            monthly_contribution=args.monthly_contribution
            if args.monthly_contribution is not None
            else get_current_retirement_savings_contributions(
                latest_budget=latest_budget,
                contribution_patterns=contribution_patterns,
                contribution_fields=contribution_fields,
                summary_keys=summary_keys,
            ),
            latest_budget=latest_budget,
            account_patterns=account_patterns,
            contribution_patterns=contribution_patterns,
        )

        print("\nRETIREMENT FORECAST")
        print(f"Initial balance: ${result.initial_balance:,.2f}")
        print(f"Monthly contribution: ${result.monthly_contribution:,.2f}")
        print(
            f"P(real terminal wealth >= ${result.config.target_real_balance:,.0f}) = "
            f"{result.probability_of_reaching_target:.1%}"
        )
        print(result.summary.to_string())
        return

    raise ConfigError(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
