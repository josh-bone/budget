import logging

logger = logging.getLogger(__name__)


def print_section(section_name: str, rows: dict[str, str]) -> None:
    try:
        from tabulate import tabulate
    except ImportError:
        raise ImportError("Missing dependency. Run:\n  pip install tabulate")

    if section_name == "summary":
        print(
            f"\n  SUMMARY\n  Net Income: {rows['net_income']}\n  Total Expenses: {rows['total_expenses']}\n  Net: {rows['diff']}"
        )
        return

    print(f"\n  {section_name.upper()}")

    logger.debug(f"{rows=}")
    table = [[row["label"], row["cell"], row["value"] or ""] for row in rows]

    print(
        tabulate(table, headers=["Label", "Cell", "Value"], tablefmt="rounded_outline")
    )
