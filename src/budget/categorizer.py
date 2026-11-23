"""Rule based transaction categorization."""

import json
import sqlite3
from collections.abc import Mapping
from typing import Any


def _matches(conditions: Mapping[str, Any], transaction: Mapping[str, Any]) -> bool:
    """Return whether a transaction satisfies the supported v1 conditions."""
    rules = conditions.get("rules")
    if not isinstance(rules, list) or not rules:
        return False
    outcomes = []
    for condition in rules:
        if not isinstance(condition, dict):
            return False
        field, operator = condition.get("field"), condition.get("operator")
        if field == "description" and operator == "contains":
            value = condition.get("value")
            if not isinstance(value, str) or not value:
                return False
            outcomes.append(
                value.casefold() in str(transaction.get("description") or "").casefold()
            )
        elif field == "amount" and operator == "between":
            try:
                amount = float(transaction["amount"])
                minimum = float(condition["minimum"])
                maximum = float(condition["maximum"])
            except (KeyError, TypeError, ValueError):
                return False
            if minimum > maximum:
                return False
            outcomes.append(minimum <= amount <= maximum)
        else:
            return False
    match_type = conditions.get("match_type", "ALL")
    if match_type == "ALL":
        return all(outcomes)
    if match_type == "ANY":
        return any(outcomes)
    return False


def categorize_transaction(
    connection: sqlite3.Connection, transaction: Mapping[str, Any]
) -> tuple[int | None, int | None]:
    """Find the first matching active rule, respecting manual overrides."""
    if transaction.get("is_manual_category", 0):
        return transaction.get("category_id"), transaction.get("rule_id")
    rules = connection.execute(
        "SELECT id, category_id, conditions FROM categorization_rules "
        "WHERE is_active = 1 ORDER BY priority ASC, id ASC"
    ).fetchall()
    for rule_id, category_id, raw_conditions in rules:
        try:
            conditions = json.loads(raw_conditions)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(conditions, dict) and _matches(conditions, transaction):
            return category_id, rule_id
    return None, None


def categorize_unassigned(connection: sqlite3.Connection) -> int:
    """Apply rules to all non-manual rows and return rows whose category changed."""
    rows = connection.execute(
        "SELECT account_id, transaction_id, description, amount, category_id, rule_id, "
        "is_manual_category FROM transactions WHERE is_manual_category = 0"
    ).fetchall()
    changed = 0
    for (
        account_id,
        transaction_id,
        description,
        amount,
        old_category,
        old_rule,
        manual,
    ) in rows:
        category_id, rule_id = categorize_transaction(
            connection,
            {
                "description": description,
                "amount": amount,
                "category_id": old_category,
                "rule_id": old_rule,
                "is_manual_category": manual,
            },
        )
        if (category_id, rule_id) != (old_category, old_rule):
            connection.execute(
                "UPDATE transactions SET category_id = ?, rule_id = ? "
                "WHERE account_id = ? AND transaction_id = ?",
                (category_id, rule_id, account_id, transaction_id),
            )
            changed += 1
    return changed


def recategorize_all(connection: sqlite3.Connection) -> int:
    """Re-evaluate all non-manual transactions and return changed row count."""
    return categorize_unassigned(connection)
