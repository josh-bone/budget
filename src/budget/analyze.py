"""
analyze.py — Budget data assembly.

build_sections  — generic: turns cell values + config into labeled row dicts.
build_summary   — domain: assembles summary fields from config-declared mappings.
build_budget    — composes both into the final budget dict.
"""

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def build_sections(
    cells_config: dict[str, dict[str, str]],
    cell_values: dict[str, float | None],
) -> dict[str, list[dict]]:
    """
    Generic pass: convert cells_config + cell_values into labeled row dicts.

    Returns a dict of section_name -> list of {label, cell, value} rows.
    Has no knowledge of what any label means.
    """
    result: dict[str, list[dict]] = {}
    for section_name, labels in cells_config.items():
        section = []
        for label, cell_ref in labels.items():
            section.append(
                {
                    "label": label,
                    "cell": cell_ref,
                    "value": cell_values.get(cell_ref),
                }
            )
        result[section_name] = section
    return result


def pivot_on_account(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])

    df = df.sort_values(["Account", "Date"])
    df = df.groupby(["Account", "Date"], as_index=False).last()

    pivot = df.pivot(index="Date", columns="Account", values="Amount")

    pivot = pivot.sort_index().ffill()
    return pivot


def build_summary(
    sections: dict[str, list[dict]],
    summary_config: dict[str, dict[str, str]],
) -> dict[str, float | None]:
    """
    Domain pass: assemble summary fields from config-declared mappings.

    summary_config shape (from config.toml [summary] table):
        [summary]
        net_income     = { section = "income",   label = "net_income" }
        total_expenses = { section = "expenses",  label = "total_expenses" }
        disposable     = { derived = "net_income - total_expenses" }

    Supports:
      - { section, label }  — look up a value from a named section row
      - { derived }         — a simple "A - B" or "A + B" expression over
                              other resolved summary fields (evaluated in
                              declaration order, so list dependents after
                              their inputs)
    """
    # Build a flat label->value index across all sections for quick lookup
    label_index: dict[tuple[str, str], float | None] = {}
    for section_name, rows in sections.items():
        for row in rows:
            label_index[(section_name, row["label"])] = row["value"]

    resolved: dict[str, float | None] = {}

    for field_name, mapping in summary_config.items():
        if "derived" in mapping:
            resolved[field_name] = _evaluate_derived(mapping["derived"], resolved)
        elif "section" in mapping and "label" in mapping:
            key = (mapping["section"], mapping["label"])
            value = label_index.get(key)
            resolved[field_name] = value
        else:
            logger.warning(
                f"Summary field '{field_name}' has unrecognised mapping: {mapping}"
            )
            resolved[field_name] = None

    # Omit fields that resolved to None so callers can use .get() safely
    return {k: v for k, v in resolved.items() if v is not None}


def _evaluate_derived(
    expression: str,
    resolved: dict[str, float | None],
) -> float | None:
    """
    Evaluate a simple two-operand arithmetic expression like "a - b" or "a + b".
    Returns None if any operand is None or the expression can't be parsed.
    """
    expression = expression.strip()
    for op in ("-", "+", "*", "/"):
        if op in expression:
            parts = expression.split(op, 1)
            if len(parts) == 2:
                left_key = parts[0].strip()
                right_key = parts[1].strip()
                left = resolved.get(left_key)
                right = resolved.get(right_key)
                if left is None or right is None:
                    return None
                if op == "-":
                    return left - right
                if op == "+":
                    return left + right
                if op == "*":
                    return left * right
                if op == "/" and right != 0:
                    return left / right
    logger.warning(f"Could not evaluate derived expression: '{expression}'")
    return None


def build_budget(
    cells_config: dict[str, dict[str, str]],
    cell_values: dict[str, float | None],
    summary_config: dict[str, Any] | None = None,
) -> dict:
    """
    Compose sections and summary into the final budget dict.

    summary_config is optional — if absent, summary is an empty dict.
    """
    sections = build_sections(cells_config, cell_values)
    summary = build_summary(sections, summary_config) if summary_config else {}

    return {**sections, "summary": summary}
