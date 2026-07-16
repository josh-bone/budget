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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, unquote

from cache import budget_cache
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, url_for

from budget.analyze import build_budget
from budget.config import ConfigError, get_env, load_toml
from budget.sheets import build_service, fetch_cells, list_sheet_names

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app.jinja_env.filters["urlencode"] = quote

# Max parallel Sheets API requests. The API is I/O-bound so this is safe to
# tune upward, but 6 is plenty for ~12 month tabs without hammering the quota.
_MAX_WORKERS = int(os.environ.get("BUDGET_FETCH_WORKERS", 6))


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


def _fetch_one(
    service,
    spreadsheet_id: str,
    sheet: str,
    all_refs: list[str],
    cells_config: dict,
    summary_config: dict,
) -> tuple[str, dict]:
    """Fetch and build budget for a single sheet. Designed for thread use."""
    logger.info(f"  Fetching '{sheet}'...")
    cell_values = fetch_cells(service, spreadsheet_id, sheet, all_refs)
    budget = build_budget(cells_config, cell_values, summary_config)
    return sheet, budget


def load_all_months() -> dict[str, dict]:
    """
    Fetch budget data for every matching sheet tab in parallel.
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
    summary_config = config.get("summary", {})

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

    logger.info(
        f"Discovered {len(sheet_names)} sheet(s), fetching with {_MAX_WORKERS} workers..."
    )

    result: dict[str, dict] = {}

    start_times = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {}
        for sheet in sheet_names:
            start_times[sheet] = time.monotonic()
            future = executor.submit(
                _fetch_one,
                service,
                spreadsheet_id,
                sheet,
                all_refs,
                cells_config,
                summary_config,
            )
            futures[future] = sheet

        for future in as_completed(futures):
            sheet = futures[future]
            elapsed = time.monotonic() - start_times[sheet]
            try:
                sheet_name, budget = future.result()
                result[sheet_name] = budget
                log_fn = logger.warning if elapsed > 5 else logger.info
                log_fn(f"Fetched sheet '{sheet}' in {elapsed:.2f}s")
            except Exception as e:
                logger.error(
                    f"Failed to fetch sheet '{sheet}' after {elapsed:.2f}s: {e}"
                )
                raise

    # Re-sort after parallel completion (arrival order is non-deterministic)
    result = dict(sorted(result.items(), key=lambda kv: _sort_key(kv[0])))

    budget_cache.set(result)
    return result


def build_trends(all_months: dict[str, dict]) -> dict:
    """Derive cross-month trend data from all loaded months."""
    months = list(all_months.keys())
    income = [all_months[m]["summary"].get("net_income") for m in months]
    expenses = [all_months[m]["summary"].get("total_expenses") for m in months]
    diff = [all_months[m]["summary"].get("disposable") for m in months]

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
