"""
app.py — Flask web interface for the budget reader.

Routes:
    GET /                       Cross-month summary (trends)
    GET /month/<sheet_name>     Single-month detail view
    POST /refresh               Invalidate cache and reload

Run:
    flask --app app run --debug
"""

import logging
import os
from urllib.parse import quote, unquote

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, url_for

from budget.analyze import build_budget
from budget.config import ConfigError, get_env, load_toml
from budget.sheets import build_service, fetch_cells, list_sheet_names
from cache import budget_cache

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Expose urlencode in Jinja templates
app.jinja_env.filters["urlencode"] = quote


# ── Helpers ────────────────────────────────────────────────────────────────────


def _sort_key(sheet_name: str):
    """Sort 'M/YYYY' tab names chronologically."""
    try:
        parts = sheet_name.split("/")
        return (int(parts[1]), int(parts[0]))
    except (IndexError, ValueError):
        return (0, 0)


def _cache_ttl() -> int:
    try:
        return int(os.environ.get("BUDGET_CACHE_TTL", 300))
    except ValueError:
        return 300


def load_all_months() -> dict[str, dict]:
    """
    Fetch budget data for every matching sheet tab.
    Returns dict keyed by sheet name, sorted oldest -> newest.
    """
    cached = budget_cache.get()
    if cached is not None:
        return cached

    key_file = get_env("GOOGLE_SERVICE_ACCOUNT_KEY")
    spreadsheet_id = get_env("SPREADSHEET_ID")
    config = load_toml()

    pattern = config.get("spreadsheet", {}).get("sheet_pattern")
    cells_config = config.get("cells", {})

    if not cells_config:
        raise ConfigError("No [cells.*] sections found in config.toml")

    all_refs = list(
        dict.fromkeys(
            ref for labels in cells_config.values() for ref in labels.values()
        )
    )

    service = build_service(key_file)
    sheet_names = list_sheet_names(service, spreadsheet_id, pattern=pattern)
    sheet_names.sort(key=_sort_key)

    logger.info(f"Discovered {len(sheet_names)} sheet(s): {sheet_names}")

    result: dict[str, dict] = {}
    for sheet in sheet_names:
        logger.info(f"  Fetching '{sheet}'...")
        cell_values = fetch_cells(service, spreadsheet_id, sheet, all_refs)
        result[sheet] = build_budget(cells_config, cell_values)

    budget_cache.set(result)
    return result


def build_trends(all_months: dict[str, dict]) -> dict:
    """Derive cross-month trend data from all loaded months."""
    months = list(all_months.keys())
    income = [all_months[m]["summary"].get("net_income") for m in months]
    expenses = [all_months[m]["summary"].get("total_expenses") for m in months]
    diff = [all_months[m]["summary"].get("diff") for m in months]

    def _sum(vals):
        return sum(v for v in vals if v is not None)

    return {
        "months": months,
        "income": income,
        "expenses": expenses,
        "diff": diff,
        "total_income": _sum(income),
        "total_expenses": _sum(expenses),
        "avg_income": _sum(income) / len(months) if months else 0,
        "avg_expenses": _sum(expenses) / len(months) if months else 0,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    error = all_months = trends = fetched_at = None
    try:
        all_months = load_all_months()
        trends = build_trends(all_months)
        fetched_at = budget_cache.fetched_at
    except Exception as e:
        logger.exception("Failed to load budget")
        error = str(e)

    return render_template(
        "index.html",
        trends=trends,
        all_months=all_months,
        fetched_at=fetched_at,
        cache_ttl=_cache_ttl(),
        error=error,
    )


@app.route("/month/<path:sheet_name>")
def month_detail(sheet_name: str):
    sheet_name = unquote(sheet_name)
    error = budget = all_months = None
    try:
        all_months = load_all_months()
        if sheet_name not in all_months:
            error = f"Sheet '{sheet_name}' not found."
        else:
            budget = all_months[sheet_name]
    except Exception as e:
        logger.exception("Failed to load budget")
        error = str(e)

    months = list(all_months.keys()) if all_months else []
    return render_template(
        "month.html",
        sheet_name=sheet_name,
        budget=budget,
        months=months,
        cache_ttl=_cache_ttl(),
        error=error,
    )


@app.route("/refresh", methods=["POST"])
def refresh():
    budget_cache.invalidate()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
