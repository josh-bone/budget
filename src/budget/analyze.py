import logging

logger = logging.getLogger(__name__)


def build_budget(
    cells_config: dict[str, dict[str, str]],
    cell_values: dict[str, str],
) -> dict:
    """
    Build a structured budget dictionary from the raw cell values and the config.
    """

    # Build individual sections (e.g. 'income', 'expenses')
    result = {}
    logger.debug(f"{cells_config.items()=}")
    for section_name, labels in cells_config.items():
        logger.debug(f"Processing section '{section_name}' with labels: {labels}")
        section = []

        if section_name == "income":
            net_income = cell_values.get(labels.get("net_income"))
            gross_income = cell_values.get(labels.get("gross_income"))
        elif section_name == "expenses":
            cell_ref = labels.get("total_expenses")
            total_expenses = cell_values.get(cell_ref)

        for label, cell_ref in labels.items():
            section.append(
                {
                    "label": label,
                    "cell": cell_ref,
                    "value": cell_values.get(cell_ref),
                }
            )

        result[section_name] = section

    logger.debug(f"After first pass: {result=}")

    # Get totals for summary section
    result["summary"] = {
        "gross_income": gross_income,
        "net_income": net_income,
        "total_expenses": total_expenses,
        "diff": net_income - total_expenses,
    }

    return result
