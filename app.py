"""
app.py — Flask web interface for the budget reader.

Routes:
    SQLite pages: /, /transactions, /rules, /balances
    Legacy Google Sheets page: /month/<sheet_name>
    JSON endpoints: /api/transactions/spend, /api/balances/trend

Run:
    flask --app app run
"""

import json
import logging
import os
import secrets
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import HTTPException

from budget.analyze import build_budget
from budget.balances_db import _connect, get_balance_history
from budget.categorizer import categorize_transaction, recategorize_all
from budget.config import ConfigError, get_env, load_toml
from budget.sheets import build_service, fetch_cells, list_sheet_names
from budget.transfers import suggest_transfer_pairs, transfer_pair_key

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["BUDGET_STAGING"] = os.environ.get("BUDGET_STAGING", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.after_request
def add_security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.context_processor
def inject_instance_mode():
    return {"budget_staging": app.config["BUDGET_STAGING"]}


# Legacy Sheets month-detail concurrency controls.
_MAX_WORKERS = max(1, int(os.environ.get("BUDGET_FETCH_WORKERS", 6)))
_RESULT_TIMEOUT = max(1, int(os.environ.get("BUDGET_RESULT_TIMEOUT", 20)))

# ── Helpers ────────────────────────────────────────────────────────────────────


def _sort_key(sheet_name: str):
    """Sort 'M/YYYY' tab names chronologically."""
    try:
        parts = sheet_name.split("/")
        return (int(parts[1]), int(parts[0]))
    except (IndexError, ValueError):
        return (0, 0)


# The functions below are retained for the CLI and the legacy month-detail
# route. The Budget Overview route intentionally does not call them.
def _fetch_one(
    service,
    spreadsheet_id: str,
    sheet: str,
    all_refs: list[str],
    cells_config: dict,
    summary_config: dict,
) -> tuple[str, dict]:
    """Fetch and build budget for a single sheet. Designed for thread use."""
    logger.info("Fetching sheet '%s'...", sheet)
    cell_values = fetch_cells(service, spreadsheet_id, sheet, all_refs)
    budget = build_budget(cells_config, cell_values, summary_config)
    return sheet, budget


@dataclass
class MonthLoadResult:
    months: dict[str, dict]
    failures: dict[str, str]


def load_all_months() -> MonthLoadResult:
    """Legacy Sheets loader retained for month detail and other callers."""
    from budget.cache import budget_cache

    cached = budget_cache.get()
    if cached is not None:
        return MonthLoadResult(months=cached, failures={})

    # Serialize cache misses in this worker and recheck after acquiring the lock.
    # Gunicorn workers have separate process-local caches and locks.
    with budget_cache.lock:
        cached = budget_cache.get()
        if cached is not None:
            return MonthLoadResult(months=cached, failures={})
        return _load_all_months_uncached(budget_cache)


def _load_all_months_uncached(budget_cache) -> MonthLoadResult:

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
        "Discovered %d sheet(s), fetching with %d workers...",
        len(sheet_names),
        _MAX_WORKERS,
    )

    result: dict[str, dict] = {}

    failures: dict[str, str] = {}
    executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
    try:
        futures = {}
        start_times = {}
        for sheet in sheet_names:
            start_times[sheet] = time.monotonic()
            futures[
                executor.submit(
                    _fetch_one,
                    service,
                    spreadsheet_id,
                    sheet,
                    all_refs,
                    cells_config,
                    summary_config,
                )
            ] = sheet

        pending = set(futures)
        while pending:
            now = time.monotonic()
            expired = {
                f for f in pending if now - start_times[futures[f]] >= _RESULT_TIMEOUT
            }
            for future in expired:
                sheet = futures[future]
                future.cancel()
                failures[sheet] = "Timed out"
                logger.warning("Timed out fetching sheet '%s'", sheet)
            pending -= expired
            if not pending:
                break

            next_timeout = min(
                _RESULT_TIMEOUT - (now - start_times[futures[f]]) for f in pending
            )
            done, _ = wait(
                pending, timeout=max(0, next_timeout), return_when=FIRST_COMPLETED
            )
            for future in done:
                pending.remove(future)
                sheet = futures[future]
                elapsed = time.monotonic() - start_times[sheet]
                try:
                    sheet_name, budget = future.result()
                    result[sheet_name] = budget
                    log_fn = logger.warning if elapsed > 5 else logger.info
                    log_fn("Fetched sheet '%s' in %.2fs", sheet, elapsed)
                except Exception as e:
                    failures[sheet] = "Fetch failed"
                    logger.warning(
                        "Failed to fetch sheet '%s' after %.2fs: %s", sheet, elapsed, e
                    )
    finally:
        # A timed-out call may still be running; do not block the request while
        # waiting for a thread that cannot be safely interrupted.
        executor.shutdown(wait=not failures, cancel_futures=True)

    # Re-sort after parallel completion (arrival order is non-deterministic)
    result = dict(sorted(result.items(), key=lambda kv: _sort_key(kv[0])))

    if not failures:
        budget_cache.set(result)
    return MonthLoadResult(months=result, failures=failures)


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


def load_sqlite_monthly_trends() -> dict:
    """Aggregate posted SQLite transactions into calendar-month totals."""
    connection = _connect()
    try:
        rows = connection.execute(
            """SELECT substr(COALESCE(t.posted_date, t.transacted_at), 1, 7) AS month,
                      SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS income,
                      SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END) AS expenses
               FROM transactions t LEFT JOIN categories c ON c.id=t.category_id
               WHERE COALESCE(t.posted_date, t.transacted_at) IS NOT NULL
                 AND t.pending = 0 AND """
            + _non_transfer_clause("t")
            + """
               GROUP BY month ORDER BY month"""
        ).fetchall()
    finally:
        connection.close()
    months = [row[0] for row in rows]
    income = [float(row[1] or 0) for row in rows]
    expenses = [float(row[2] or 0) for row in rows]
    return {
        "months": months,
        "income": income,
        "expenses": expenses,
        "diff": [i - e for i, e in zip(income, expenses)],
        "total_income": sum(income),
        "total_expenses": sum(expenses),
        "avg_income": sum(income) / len(months) if months else 0,
        "avg_expenses": sum(expenses) / len(months) if months else 0,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    error = trends = None
    try:
        trends = load_sqlite_monthly_trends()
    except Exception:
        logger.exception("Failed to load transaction summary from SQLite")
        error = (
            "Unable to load the budget summary. Check the application logs for details."
        )

    return render_template(
        "index.html",
        trends=trends,
        error=error,
    )


@app.route("/month/<month_slug>")
def month_detail(month_slug: str):
    # Month sheet names use M/YYYY; a single hyphen makes a URL-safe path segment.
    sheet_name = month_slug.replace("-", "/", 1)
    error = warning = budget = all_months = None
    try:
        load_result = load_all_months()
        # Accept a plain mapping from older callers and route-level overrides.
        all_months = (
            load_result if isinstance(load_result, dict) else load_result.months
        )
        failures = {} if isinstance(load_result, dict) else load_result.failures
        if failures:
            failed_sheets = ", ".join(sorted(failures, key=_sort_key))
            warning = f"Some months could not be loaded: {failed_sheets}. Data may be incomplete."
        if sheet_name not in all_months:
            abort(404)
        budget = all_months[sheet_name]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to load budget")
        error = "Unable to load this month's budget. Check the application logs for details."

    months = list(all_months.keys()) if all_months else []
    return render_template(
        "month.html",
        sheet_name=sheet_name,
        budget=budget,
        months=months,
        error=error,
        warning=warning,
    )


@app.route("/month/<path:sheet_name>")
def legacy_month_detail(sheet_name: str):
    """Keep older slash-containing month URLs working."""
    return month_detail(sheet_name.replace("/", "-", 1))


@app.route("/refresh", methods=["POST"])
def refresh():
    # Balance pages read SQLite directly; rendering the destination reloads it.
    from urllib.parse import urlsplit

    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        abort(403)
    parsed_source = urlsplit(source)
    if (
        parsed_source.scheme not in {"http", "https"}
        or parsed_source.netloc != request.host
    ):
        abort(403)
    return redirect(url_for("balances"))


@app.route("/balances")
def balances():
    """Show current per-account balances and the SQLite net worth history."""
    source_filter = request.args.get("source", "all").lower()
    if source_filter not in {"all", "manual", "simplefin"}:
        source_filter = "all"
    error = None
    accounts = []
    net_worth = None
    latest_date = None
    try:
        history = get_balance_history()
        connection = _connect()
        try:
            closed_accounts = {
                name.casefold()
                for (name,) in connection.execute(
                    "SELECT name FROM accounts WHERE closed_date IS NOT NULL"
                )
            }
        finally:
            connection.close()
        if "Source" in history.columns and source_filter != "all":
            history = history[history["Source"] == source_filter]
        if not history.empty:
            history["Date"] = history["Date"].astype(str)
            latest_date = history["Date"].max()

            for account, rows in history.groupby("Account", sort=True):
                latest = rows.sort_values("Date").iloc[-1]
                accounts.append(
                    {
                        "name": account,
                        "amount": float(latest["Amount"]),
                        "date": latest["Date"],
                        "stale": latest["Date"] != latest_date
                        and account.casefold() not in closed_accounts,
                    }
                )

            net_worth = sum(account["amount"] for account in accounts)
    except Exception:
        logger.exception("Failed to load balances from SQLite")
        error = "Unable to load balances. Check the application logs for details."

    return render_template(
        "balances.html",
        accounts=accounts,
        net_worth=net_worth,
        latest_date=latest_date,
        source_filter=source_filter,
        error=error,
    )


@app.get("/accounts")
def accounts():
    """List canonical accounts with their latest directly named or aliased snapshot."""
    error = None
    account_rows = []
    connection = _connect()
    try:
        rows = connection.execute(
            """SELECT a.id, a.name, ao.owner FROM accounts a
               LEFT JOIN account_owners ao ON ao.account_id=a.id
               WHERE a.closed_date IS NULL ORDER BY a.name COLLATE NOCASE"""
        ).fetchall()
        aliases = {}
        for account_id, alias in connection.execute(
            "SELECT account_key, alias FROM account_aliases ORDER BY alias COLLATE NOCASE"
        ):
            aliases.setdefault(account_id, []).append(alias)
        snapshots = connection.execute(
            "SELECT account, date, amount, source FROM balance_snapshots ORDER BY date DESC"
        ).fetchall()
        for account_id, name, owner in rows:
            names = {name.casefold(), *(alias.casefold() for alias in aliases.get(account_id, []))}
            latest = next((row for row in snapshots if row[0].casefold() in names), None)
            date_value, amount, source = latest[1:] if latest else (None, None, None)
            account_rows.append({
                "id": account_id,
                "name": name,
                "owner": owner or "",
                "date": date_value,
                "amount": amount,
                "source": {
                    "manual": "Google Sheets / manual",
                    "simplefin": "SimpleFIN",
                }.get(source, source) or ("Manually added" if account_id.startswith("manual:") else "SimpleFIN"),
                "key": account_id,
                "aliases": aliases.get(account_id, []),
            })
    except Exception:
        logger.exception("Failed to load accounts from SQLite")
        error = "Unable to load accounts. Check the application logs for details."
    finally:
        connection.close()
    return render_template("accounts.html", accounts=account_rows, error=error)


@app.post("/accounts/owner")
def save_account_owner():
    """Set or clear an account's manually assigned owner."""
    account_id = request.form.get("account_id", "")
    owner = request.form.get("owner", "").strip()
    connection = _connect()
    try:
        if not account_id or not connection.execute(
            "SELECT 1 FROM accounts WHERE id=?", (account_id,)
        ).fetchone():
            abort(404)
        if owner:
            connection.execute(
                """INSERT INTO account_owners (account_id, owner) VALUES (?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET owner=excluded.owner""",
                (account_id, owner),
            )
        else:
            connection.execute("DELETE FROM account_owners WHERE account_id=?", (account_id,))
        connection.commit()
        flash("Account owner saved.", "success")
    finally:
        connection.close()
    return redirect(url_for("accounts"))


@app.route("/accounts/<account_id>/close", methods=["GET", "POST"])
def close_account(account_id):
    """Close an account while retaining its historical balance snapshots."""
    connection = _connect()
    try:
        account = connection.execute(
            "SELECT id, name, closed_date FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        if not account:
            abort(404)
        if account[2] is not None:
            return redirect(url_for("accounts"))

        aliases = [
            row[0]
            for row in connection.execute(
                "SELECT alias FROM account_aliases WHERE account_key=?", (account_id,)
            )
        ]
        names = [account[1], *aliases]
        if request.method == "GET":
            return render_template("close_account.html", account=account, close_date="")

        close_date = request.form.get("closed_date", "").strip()
        try:
            close_date = date.fromisoformat(close_date).isoformat()
        except ValueError:
            flash("Enter a valid closing date.", "error")
            return (
                render_template(
                    "close_account.html",
                    account=account,
                    close_date=request.form.get("closed_date", ""),
                ),
                400,
            )

        placeholders = ",".join("?" for _ in names)
        closing_rows = connection.execute(
            f"SELECT account, amount FROM balance_snapshots WHERE date=? AND account COLLATE NOCASE IN ({placeholders})",
            [close_date, *names],
        ).fetchall()
        closing_balance = (
            sum(row[1] for row in closing_rows) if closing_rows else None
        )
        confirmed = request.form.get("zeroed")
        if (
            closing_balance is None or closing_balance != 0
        ) and confirmed not in {"yes", "no"}:
            return render_template(
                "confirm_close_account.html",
                account=account,
                close_date=close_date,
                closing_balance=closing_balance,
            )
        if confirmed == "yes":
            if closing_rows:
                connection.executemany(
                    "UPDATE balance_snapshots SET amount=0 WHERE account=? AND date=?",
                    [(row[0], close_date) for row in closing_rows],
                )
            else:
                connection.execute(
                    "INSERT INTO balance_snapshots(account,date,amount,source) VALUES(?,?,0,'manual')",
                    (account[1], close_date),
                )
        connection.execute(
            "UPDATE accounts SET closed_date=? WHERE id=?", (close_date, account_id)
        )
        connection.commit()
        flash(f"{account[1]} closed.", "success")
        return redirect(url_for("accounts"))
    finally:
        connection.close()


@app.post("/accounts")
def create_account():
    """Create a canonical account that may have no SimpleFIN connection."""
    name = request.form.get("name", "").strip()
    if not name:
        flash("Enter an account name.", "error")
        return redirect(url_for("accounts"))
    connection = _connect()
    try:
        if connection.execute(
            "SELECT 1 FROM accounts WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone():
            flash("An account with that name already exists.", "error")
        elif connection.execute(
            "SELECT 1 FROM account_aliases WHERE alias=? COLLATE NOCASE", (name,)
        ).fetchone():
            flash("That name is already an alias for another account.", "error")
        else:
            import uuid

            connection.execute(
                "INSERT INTO accounts (id, name) VALUES (?, ?)",
                (f"manual:{uuid.uuid4()}", name),
            )
            connection.commit()
            flash("Account added.", "success")
    finally:
        connection.close()
    return redirect(url_for("accounts"))


@app.post("/accounts/aliases")
def save_account_aliases():
    """Replace an imported account's comma-separated aliases."""
    submitted = request.form.get("aliases", "")
    account_key = request.form.get("account_key", "")
    alias_values = []
    seen_aliases = set()
    for value in submitted.split(","):
        alias = value.strip()
        if alias and alias.casefold() not in seen_aliases:
            alias_values.append(alias)
            seen_aliases.add(alias.casefold())
    connection = _connect()
    try:
        if not account_key:
            abort(404)
        exists = connection.execute(
            "SELECT 1 FROM accounts WHERE id=?", (account_key,)
        ).fetchone()
        if not exists:
            abort(404)
        for alias in alias_values:
            conflicting = connection.execute(
                """SELECT 1 FROM account_aliases WHERE alias=? COLLATE NOCASE
                   AND account_key != ? LIMIT 1""",
                (alias, account_key),
            ).fetchone()
            if conflicting:
                flash("An alias is already assigned to another account.", "error")
                return redirect(url_for("accounts"))
            if connection.execute(
                "SELECT 1 FROM accounts WHERE name=? COLLATE NOCASE AND id != ?",
                (alias, account_key),
            ).fetchone():
                flash("An alias cannot match another account's canonical name.", "error")
                return redirect(url_for("accounts"))
        connection.execute("DELETE FROM account_aliases WHERE account_key=?", (account_key,))
        connection.executemany(
            "INSERT INTO account_aliases (account_key, alias) VALUES (?, ?)",
            [(account_key, alias) for alias in alias_values],
        )
        connection.commit()
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("Aliases must be unique for this account.", "error")
    finally:
        connection.close()
    return redirect(url_for("accounts"))


@app.post("/attention/map-sheet-account")
def map_sheet_account():
    """Save an unmatched sheet label as an alias of an existing canonical account."""
    sheet_name = request.form.get("sheet_name", "").strip()
    account_id = request.form.get("account_id", "")
    connection = _connect()
    try:
        if not sheet_name or not connection.execute(
            "SELECT 1 FROM balance_snapshots WHERE account=? AND source='manual' LIMIT 1",
            (sheet_name,),
        ).fetchone():
            abort(404)
        if not connection.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
            abort(404)
        existing = connection.execute(
            "SELECT account_key FROM account_aliases WHERE alias=? COLLATE NOCASE",
            (sheet_name,),
        ).fetchone()
        if connection.execute(
            "SELECT 1 FROM accounts WHERE id=? AND name=? COLLATE NOCASE",
            (account_id, sheet_name),
        ).fetchone():
            flash("That sheet name already matches the selected account.", "error")
        elif existing and existing[0] != account_id:
            flash("That sheet name is already an alias for another account.", "error")
        else:
            connection.execute(
                "INSERT OR IGNORE INTO account_aliases (account_key, alias) VALUES (?, ?)",
                (account_id, sheet_name),
            )
            connection.commit()
            flash(f"‘{sheet_name}’ now matches the selected account.", "success")
    finally:
        connection.close()
    return redirect(url_for("attention", _anchor="accounts"))


@app.get("/api/balances/trend")
def balances_trend():
    source_filter = request.args.get("source", "all").lower()
    if source_filter not in {"all", "manual", "simplefin"}:
        source_filter = "all"
    history = get_balance_history()
    if "Source" in history.columns and source_filter != "all":
        history = history[history["Source"] == source_filter]
    points = []
    if not history.empty:
        history["Date"] = history["Date"].astype(str)
        account_histories = {
            account: rows.sort_values("Date")
            for account, rows in history.groupby("Account", sort=True)
        }
        for day in sorted(history["Date"].unique()):
            amount = sum(
                float(available.iloc[-1]["Amount"])
                for rows in account_histories.values()
                if not (available := rows[rows["Date"] <= day]).empty
            )
            points.append({"date": day, "amount": amount})
    return jsonify(points)


def _non_transfer_clause(transaction_alias="t"):
    """Exclude transactions in transfer categories or their subcategories."""
    return (
        f"({transaction_alias}.category_id IS NULL OR NOT EXISTS ("
        "SELECT 1 FROM categories assigned_category "
        "LEFT JOIN categories assigned_parent ON assigned_parent.id=assigned_category.parent_id "
        f"WHERE assigned_category.id={transaction_alias}.category_id "
        "AND (assigned_category.is_transfer=1 OR assigned_parent.is_transfer=1)))"
    )


def _category_options(connection, *, exclude_transfers=False):
    query = "SELECT c.id, c.name, c.parent_id, parent.name FROM categories c LEFT JOIN categories parent ON parent.id=c.parent_id"
    if exclude_transfers:
        query += " WHERE c.is_transfer=0 AND COALESCE(parent.is_transfer, 0)=0"
    rows = connection.execute(query + " ORDER BY c.name COLLATE NOCASE").fetchall()
    return [
        (category_id, f"{parent_name} › {name}" if parent_id else name)
        for category_id, name, parent_id, parent_name in rows
    ]


@app.route("/rules", methods=["GET"])
def rules():
    connection = _connect()
    try:
        categories = _category_options(connection)
        parent_categories = connection.execute(
            "SELECT id, name FROM categories WHERE parent_id IS NULL ORDER BY name COLLATE NOCASE"
        ).fetchall()
        rule_rows = connection.execute(
            """SELECT r.id, r.name, r.category_id, CASE WHEN p.id IS NULL THEN c.name ELSE p.name || ' › ' || c.name END, r.priority, r.is_active, r.conditions
               FROM categorization_rules r JOIN categories c ON c.id=r.category_id LEFT JOIN categories p ON p.id=c.parent_id
               ORDER BY r.priority, r.id"""
        ).fetchall()
        transfer_categories = {
            row[0]
            for row in connection.execute(
                "SELECT id FROM categories WHERE is_transfer=1"
            ).fetchall()
        }
        inherited_transfer_categories = {
            row[0]
            for row in connection.execute(
                """SELECT c.id FROM categories c JOIN categories parent ON parent.id=c.parent_id
                   WHERE c.is_transfer=0 AND parent.is_transfer=1"""
            ).fetchall()
        }
    finally:
        connection.close()
    rules_data = []
    for row in rule_rows:
        try:
            conditions = json.loads(row[6])
            condition = conditions.get("rules", [{}])[0]
            match_field = condition.get("field", "description")
            match_value = condition.get("value", "")
            amount_minimum = condition.get("minimum", "")
            amount_maximum = condition.get("maximum", "")
        except (json.JSONDecodeError, AttributeError, IndexError):
            match_value = ""
            match_field, amount_minimum, amount_maximum = "description", "", ""
        rules_data.append(
            {
                "id": row[0],
                "name": row[1],
                "category_id": row[2],
                "category": row[3],
                "priority": row[4],
                "active": row[5],
                "match_value": match_value,
                "match_field": match_field,
                "amount_minimum": amount_minimum,
                "amount_maximum": amount_maximum,
            }
        )
    return render_template(
        "rules.html",
        categories=categories,
        rules=rules_data,
        description=request.args.get("description", ""),
        selected_category=request.args.get("category_id", ""),
        parent_categories=parent_categories,
        transfer_categories=transfer_categories,
        inherited_transfer_categories=inherited_transfer_categories,
    )


@app.post("/categories")
def create_category():
    name = request.form.get("name", "").strip()
    raw_parent = request.form.get("parent_id", "").strip()
    try:
        parent_id = int(raw_parent) if raw_parent else None
    except ValueError:
        parent_id = None
    if not name:
        flash("Enter a category name.", "error")
    else:
        connection = _connect()
        try:
            connection.execute(
                "INSERT INTO categories(name, parent_id) VALUES (?, ?)",
                (name, parent_id),
            )
            connection.commit()
            flash(f"Added category {name}.", "success")
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            flash(
                f"Category {name} already exists."
                if "UNIQUE" in str(exc)
                else "Subcategories can only be added to top-level categories.",
                "error",
            )
        finally:
            connection.close()
    return redirect(url_for("rules"))


@app.post("/categories/<int:category_id>/transfer")
def toggle_transfer_category(category_id):
    is_transfer = "1" in request.form.getlist("is_transfer")
    connection = _connect()
    try:
        connection.execute(
            "UPDATE categories SET is_transfer=? WHERE id=?",
            (int(is_transfer), category_id),
        )
        connection.commit()
        flash("Transfer category updated.", "success")
    finally:
        connection.close()
    return redirect(url_for("rules"))


@app.post("/categories/<int:category_id>/rename")
def rename_category(category_id):
    name = request.form.get("name", "").strip()
    connection = _connect()
    try:
        if not name:
            flash("Enter a category name.", "error")
        else:
            connection.execute(
                "UPDATE categories SET name=? WHERE id=?", (name, category_id)
            )
            connection.commit()
            flash(
                "Category renamed."
                if connection.total_changes
                else "Category not found.",
                "success",
            )
    except sqlite3.IntegrityError:
        connection.rollback()
        flash(f"Category {name} already exists.", "error")
    finally:
        connection.close()
    return redirect(url_for("rules"))


@app.post("/categories/<int:category_id>/parent")
def reparent_category(category_id):
    raw_parent = request.form.get("parent_id", "").strip()
    try:
        parent_id = int(raw_parent) if raw_parent else None
        connection = _connect()
        try:
            connection.execute(
                "UPDATE categories SET parent_id=? WHERE id=?", (parent_id, category_id)
            )
            connection.commit()
            flash("Category moved.", "success")
        finally:
            connection.close()
    except (ValueError, sqlite3.IntegrityError):
        flash("Categories can only be nested one level deep.", "error")
    return redirect(url_for("rules"))


@app.post("/categories/<int:category_id>/delete")
def delete_category(category_id):
    connection = _connect()
    try:
        if connection.execute(
            "SELECT 1 FROM categories WHERE parent_id=? LIMIT 1", (category_id,)
        ).fetchone():
            flash("Category has subcategories.", "error")
            return redirect(url_for("rules"))
        referenced = connection.execute(
            "SELECT 1 FROM transactions WHERE category_id=? LIMIT 1", (category_id,)
        ).fetchone()
        if referenced:
            flash("Category is in use by a transaction.", "error")
            return redirect(url_for("rules"))
        connection.execute("DELETE FROM categories WHERE id=?", (category_id,))
        connection.commit()
        flash(
            "Category deleted." if connection.total_changes else "Category not found.",
            "success",
        )
    except sqlite3.IntegrityError:
        connection.rollback()
        flash("Category is in use by a rule or transaction.", "error")
    finally:
        connection.close()
    return redirect(url_for("rules"))


def _save_rule(rule_id=None):
    category_raw = request.form.get("category_id", "")
    match_field = request.form.get("match_field", "description")
    value = request.form.get("value", "").strip()
    minimum_raw = request.form.get("minimum", "").strip()
    maximum_raw = request.form.get("maximum", "").strip()
    condition = None
    try:
        if match_field == "amount":
            minimum, maximum = float(minimum_raw), float(maximum_raw)
            if minimum <= maximum:
                condition = {
                    "field": "amount",
                    "operator": "between",
                    "minimum": minimum,
                    "maximum": maximum,
                }
        elif match_field == "description" and value:
            condition = {"field": "description", "operator": "contains", "value": value}
    except ValueError:
        condition = None
    try:
        category_id = int(category_raw)
        priority = int(request.form.get("priority", "0"))
    except ValueError:
        category_id, priority = 0, 0
    connection = _connect()
    try:
        if (
            condition is None
            or not connection.execute(
                "SELECT 1 FROM categories WHERE id=?", (category_id,)
            ).fetchone()
        ):
            flash("Choose a category and enter a valid rule criterion.", "error")
        else:
            conditions = json.dumps(
                {
                    "match_type": "ALL",
                    "rules": [condition],
                }
            )
            if rule_id is None:
                connection.execute(
                    "INSERT INTO categorization_rules(name, category_id, priority, conditions) VALUES (?, ?, ?, ?)",
                    (
                        request.form.get("name", "").strip() or None,
                        category_id,
                        priority,
                        conditions,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE categorization_rules SET name=?, category_id=?, priority=?, is_active=?, conditions=? WHERE id=?",
                    (
                        request.form.get("name", "").strip() or None,
                        category_id,
                        priority,
                        int(request.form.get("is_active") == "1"),
                        conditions,
                        rule_id,
                    ),
                )
            recategorize_all(connection)
            connection.commit()
            flash("Rule saved.", "success")
    finally:
        connection.close()
    return redirect(url_for("rules"))


@app.post("/rules")
def create_rule():
    return _save_rule()


@app.post("/rules/<int:rule_id>/edit")
def edit_rule(rule_id):
    return _save_rule(rule_id)


@app.post("/rules/<int:rule_id>/delete")
def delete_rule(rule_id):
    connection = _connect()
    try:
        connection.execute("DELETE FROM categorization_rules WHERE id=?", (rule_id,))
        recategorize_all(connection)
        connection.commit()
    finally:
        connection.close()
    return redirect(url_for("rules"))


@app.post("/rules/run")
def run_rules():
    connection = _connect()
    try:
        count = recategorize_all(connection)
        connection.commit()
    finally:
        connection.close()
    flash(f"Updated {count} transaction(s).", "success")
    return redirect(url_for("rules"))


@app.route("/transactions")
def transactions():
    per_page = 50
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    account_id = request.args.get("account_id", "").strip()
    category_ids = [
        value.strip() for value in request.args.getlist("category_id") if value.strip()
    ]
    owner = request.args.get("owner", "").strip()
    pending = request.args.get("pending", "").strip()
    query = request.args.get("q", "").strip()
    selected_month = request.args.get("month", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    min_amount = request.args.get("min_amount", "").strip()
    max_amount = request.args.get("max_amount", "").strip()
    sort = request.args.get("sort", "date").strip()
    order = request.args.get("order", "desc").lower()
    if sort not in {"date", "amount"}:
        sort = "date"
    if order not in {"asc", "desc"}:
        order = "desc"
    show_transfers = request.args.get("show_transfers") == "1"
    clauses, values = [], []
    if account_id:
        clauses.append("t.account_id = ?")
        values.append(account_id)
    selected_category_ids = []
    category_filter_clauses = []
    if "uncategorized" in category_ids:
        category_filter_clauses.append("t.category_id IS NULL")
    for category_value in category_ids:
        try:
            selected_category_ids.append(int(category_value))
        except ValueError:
            continue
    if selected_category_ids:
        placeholders = ",".join("?" for _ in selected_category_ids)
        category_filter_clauses.append(
            "t.category_id IN (SELECT id FROM categories WHERE id IN ("
            + placeholders
            + ") OR parent_id IN ("
            + placeholders
            + "))"
        )
        values.extend(selected_category_ids * 2)
    if category_filter_clauses:
        clauses.append("(" + " OR ".join(category_filter_clauses) + ")")
    if owner:
        clauses.append("ao.owner = ?")
        values.append(owner)
    if pending in ("0", "1"):
        clauses.append("t.pending = ?")
        values.append(int(pending))
    else:
        pending = ""
    if query:
        clauses.append("INSTR(LOWER(t.description), LOWER(?)) > 0")
        values.append(query)
    try:
        if min_amount:
            clauses.append("t.amount >= ?")
            values.append(float(min_amount))
    except ValueError:
        min_amount = ""
    try:
        if max_amount:
            clauses.append("t.amount <= ?")
            values.append(float(max_amount))
    except ValueError:
        max_amount = ""
    # A date range takes precedence when either bound is supplied.
    date_clauses = []
    has_date_range = bool(start_date or end_date)
    if start_date:
        date_clauses.append("COALESCE(t.posted_date, t.transacted_at) >= ?")
        values.append(start_date)
    if end_date:
        date_clauses.append("COALESCE(t.posted_date, t.transacted_at) <= ?")
        values.append(end_date)
    elif selected_month and not has_date_range:
        date_clauses.append(
            "substr(COALESCE(t.posted_date, t.transacted_at), 1, 7) = ?"
        )
        values.append(selected_month)
    clauses.extend(date_clauses)
    if has_date_range:
        selected_month = ""
    list_clauses = list(clauses)
    if not show_transfers:
        list_clauses.append(_non_transfer_clause("t"))
    where = (" WHERE " + " AND ".join(list_clauses)) if list_clauses else ""
    connection = _connect()
    try:
        total = connection.execute(
            "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN account_owners ao ON ao.account_id=a.id"
            + where,
            values,
        ).fetchone()[0]
        rows = connection.execute(
            """SELECT t.account_id, t.transaction_id, COALESCE(t.posted_date, t.transacted_at),
                      a.name, t.description, t.pending, t.amount, t.category_id,
                      CASE WHEN parent.id IS NULL THEN c.name ELSE parent.name || ' › ' || c.name END,
                      t.is_manual_category
               FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN account_owners ao ON ao.account_id=a.id
               LEFT JOIN categories c ON c.id=t.category_id LEFT JOIN categories parent ON parent.id=c.parent_id"""
            + where
            + (
                " ORDER BY t.amount "
                + order.upper()
                + ", COALESCE(t.posted_date, t.transacted_at) DESC, t.rowid DESC"
                if sort == "amount"
                else " ORDER BY COALESCE(t.posted_date, t.transacted_at) "
                + order.upper()
                + ", t.rowid DESC"
            )
            + " LIMIT ? OFFSET ?",
            [*values, per_page, (page - 1) * per_page],
        ).fetchall()
        accounts = connection.execute(
            "SELECT id, name FROM accounts ORDER BY name COLLATE NOCASE"
        ).fetchall()
        owners = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT owner FROM account_owners ORDER BY owner COLLATE NOCASE"
            ).fetchall()
        ]
        categories = _category_options(connection)
        chart_categories = _category_options(connection, exclude_transfers=True)
        months = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT substr(COALESCE(posted_date, transacted_at), 1, 7) "
                "FROM transactions WHERE COALESCE(posted_date, transacted_at) IS NOT NULL "
                "ORDER BY 1 DESC"
            ).fetchall()
        ]
        chart_clauses = [*clauses, _non_transfer_clause("t")]
        chart_values = values
        rollup = request.args.get("leaf", "") != "1"
        category_expr = "COALESCE(parent.name, c.name)" if rollup else "c.name"
        spend_rows = connection.execute(
            "SELECT COALESCE(" + category_expr + ", 'Uncategorized'), SUM(-t.amount) "
            "FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN account_owners ao ON ao.account_id=a.id LEFT JOIN categories c ON c.id=t.category_id LEFT JOIN categories parent ON parent.id=c.parent_id WHERE t.amount < 0"
            + ((" AND " + " AND ".join(chart_clauses)) if chart_clauses else "")
            + (
                " GROUP BY COALESCE(parent.id, c.id), COALESCE(parent.name, c.name)"
                if rollup
                else " GROUP BY t.category_id, c.name"
            )
            + " ORDER BY 2 DESC",
            chart_values,
        ).fetchall()
    finally:
        connection.close()
    transactions_data = [
        dict(
            zip(
                (
                    "account_id",
                    "transaction_id",
                    "date",
                    "account",
                    "description",
                    "pending",
                    "amount",
                    "category_id",
                    "category",
                    "manual",
                ),
                row,
            )
        )
        for row in rows
    ]
    filters = {
        "account_id": account_id,
        "category_id": category_ids,
        "owner": owner,
        "pending": pending,
        "q": query,
        "month": selected_month,
        "start_date": start_date,
        "end_date": end_date,
        "show_transfers": "1" if show_transfers else "",
        "min_amount": min_amount,
        "max_amount": max_amount,
        "sort": sort,
        "order": order,
    }
    spend_total = sum(amount for _, amount in spend_rows)
    palette = [
        "#4778a8",
        "#e08b45",
        "#65a36e",
        "#b65d5d",
        "#8a70ad",
        "#c5a537",
        "#4b9b9a",
        "#ce719b",
        "#7d8791",
        "#9b704d",
    ]
    spend_breakdown = []
    spend_rows.sort(key=lambda row: row[1], reverse=True)
    for index, (name, amount) in enumerate(spend_rows):
        percent = amount / spend_total * 100 if spend_total else 0
        color = palette[index % len(palette)]
        spend_breakdown.append(
            {"name": name, "amount": amount, "percent": percent, "color": color}
        )
    return render_template(
        "transactions.html",
        transactions=transactions_data,
        accounts=accounts,
        owners=owners,
        categories=categories,
        chart_categories=chart_categories,
        filters=filters,
        page=page,
        pages=max(1, (total + per_page - 1) // per_page),
        total=total,
        months=months,
        selected_month=selected_month,
        start_date=start_date,
        end_date=end_date,
        spend_breakdown=spend_breakdown,
        spend_total=spend_total,
        rollup=rollup,
        show_transfers=show_transfers,
        sort=sort,
        order=order,
    )


@app.get("/api/transactions/spend")
def transaction_spend():
    month = request.args.get("month", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    clauses, values = [_non_transfer_clause("t")], []
    if start_date:
        clauses.append("COALESCE(t.posted_date, t.transacted_at) >= ?")
        values.append(start_date)
    if end_date:
        clauses.append("COALESCE(t.posted_date, t.transacted_at) <= ?")
        values.append(end_date)
    elif month and not (start_date or end_date):
        clauses.append("substr(COALESCE(t.posted_date, t.transacted_at), 1, 7) = ?")
        values.append(month)
    selected = request.args.getlist("category_id")
    rollup = request.args.get("leaf", "") != "1"
    category_ids = []
    category_clauses = []
    account_id = request.args.get("account_id", "").strip()
    owner = request.args.get("owner", "").strip()
    pending = request.args.get("pending", "").strip()
    query = request.args.get("q", "").strip()
    if account_id:
        clauses.append("t.account_id = ?")
        values.append(account_id)
    if owner:
        clauses.append("ao.owner = ?")
        values.append(owner)
    if pending in ("0", "1"):
        clauses.append("t.pending = ?")
        values.append(int(pending))
    if query:
        clauses.append("INSTR(LOWER(t.description), LOWER(?)) > 0")
        values.append(query)
    min_amount = request.args.get("min_amount", "").strip()
    max_amount = request.args.get("max_amount", "").strip()
    try:
        if min_amount:
            clauses.append("t.amount >= ?")
            values.append(float(min_amount))
    except ValueError:
        min_amount = ""
    try:
        if max_amount:
            clauses.append("t.amount <= ?")
            values.append(float(max_amount))
    except ValueError:
        max_amount = ""
    for value in selected:
        if value == "uncategorized":
            category_clauses.append("t.category_id IS NULL")
        else:
            try:
                category_ids.append(int(value))
            except ValueError:
                continue
    if category_ids:
        placeholders = ",".join("?" for _ in category_ids)
        category_clauses.append(
            "t.category_id IN (SELECT id FROM categories WHERE id IN ("
            + placeholders
            + ") OR parent_id IN ("
            + placeholders
            + "))"
        )
        values.extend(category_ids * 2)
    if category_clauses:
        clauses.append("(" + " OR ".join(category_clauses) + ")")
    connection = _connect()
    try:
        category_expr = "COALESCE(parent.name, c.name)" if rollup else "c.name"
        rows = connection.execute(
            "SELECT COALESCE(" + category_expr + ", 'Uncategorized'), SUM(-t.amount) "
            "FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN account_owners ao ON ao.account_id=a.id LEFT JOIN categories c ON c.id=t.category_id LEFT JOIN categories parent ON parent.id=c.parent_id "
            "WHERE t.amount < 0 AND "
            + " AND ".join(clauses)
            + (
                " GROUP BY COALESCE(parent.id, c.id), COALESCE(parent.name, c.name)"
                if rollup
                else " GROUP BY t.category_id, c.name"
            )
            + " ORDER BY 1 COLLATE NOCASE",
            values,
        ).fetchall()
    finally:
        connection.close()
    palette = [
        "#4778a8",
        "#e08b45",
        "#65a36e",
        "#b65d5d",
        "#8a70ad",
        "#c5a537",
        "#4b9b9a",
        "#ce719b",
        "#7d8791",
        "#9b704d",
    ]
    total = sum(float(amount) for _, amount in rows)
    breakdown = [
        {
            "name": name,
            "amount": float(amount),
            "percent": float(amount) / total * 100 if total else 0,
            "color": palette[index % len(palette)],
        }
        for index, (name, amount) in enumerate(
            sorted(rows, key=lambda row: row[1], reverse=True)
        )
    ]
    return jsonify({"total": total, "breakdown": breakdown})


@app.get("/transfers")
@app.get("/attention")
def attention():
    connection = _connect()
    try:
        suggestions = suggest_transfer_pairs(connection)
        unmatched_sheet_names = connection.execute(
            """SELECT s.account, MAX(s.date), COUNT(*),
                      (SELECT latest.amount FROM balance_snapshots latest
                       WHERE latest.account=s.account AND latest.source='manual'
                       ORDER BY latest.date DESC LIMIT 1)
               FROM balance_snapshots s
               WHERE s.source='manual'
                 AND NOT EXISTS (SELECT 1 FROM accounts a WHERE a.name=s.account COLLATE NOCASE)
                 AND NOT EXISTS (SELECT 1 FROM account_aliases aa
                                 WHERE aa.alias=s.account COLLATE NOCASE)
               GROUP BY s.account ORDER BY s.account COLLATE NOCASE"""
        ).fetchall()
        canonical_accounts = connection.execute(
            "SELECT id, name FROM accounts ORDER BY name COLLATE NOCASE"
        ).fetchall()
        flagged_ids = {
            row[0]
            for row in connection.execute(
                """SELECT c.id FROM categories c LEFT JOIN categories parent ON parent.id=c.parent_id
                   WHERE c.is_transfer=1 OR parent.is_transfer=1"""
            )
        }
        transfer_categories = [
            {"id": category_id, "name": name}
            for category_id, name in _category_options(connection)
            if category_id in flagged_ids
        ]
        approved = connection.execute(
            """SELECT t.account_id, t.transaction_id, a.name,
                      COALESCE(t.posted_date, t.transacted_at), t.amount,
                      t.description, c.name
               FROM transactions t JOIN accounts a ON a.id=t.account_id
               JOIN categories c ON c.id=t.category_id
               LEFT JOIN categories parent ON parent.id=c.parent_id
               WHERE (c.is_transfer=1 OR parent.is_transfer=1) AND t.is_manual_category=1
               ORDER BY COALESCE(t.posted_date, t.transacted_at) DESC,
                        a.name COLLATE NOCASE, t.transaction_id"""
        ).fetchall()
    finally:
        connection.close()
    return render_template(
        "transfers.html",
        suggestions=suggestions,
        unmatched_sheet_names=[
            {"name": row[0], "latest_date": row[1], "count": row[2], "amount": row[3]}
            for row in unmatched_sheet_names
        ],
        canonical_accounts=canonical_accounts,
        transfer_categories=transfer_categories,
        approved=[
            {
                "account_id": row[0],
                "transaction_id": row[1],
                "account": row[2],
                "date": row[3],
                "amount": row[4],
                "description": row[5],
                "category": row[6],
            }
            for row in approved
        ],
    )


def _posted_transfer_suggestion(connection, source, candidate):
    pair = transfer_pair_key(source, candidate)
    for suggestion in suggest_transfer_pairs(connection):
        if transfer_pair_key(suggestion["source"], suggestion["candidate"]) == pair:
            return suggestion
    return None


def _transfer_form_pair(connection):
    source = {
        "account_id": request.form.get("source_account_id", ""),
        "transaction_id": request.form.get("source_transaction_id", ""),
    }
    candidate = {
        "account_id": request.form.get("candidate_account_id", ""),
        "transaction_id": request.form.get("candidate_transaction_id", ""),
    }
    if not all(
        (
            source["account_id"],
            source["transaction_id"],
            candidate["account_id"],
            candidate["transaction_id"],
        )
    ):
        return None, None
    return source, candidate


@app.post("/transfers/approve")
def approve_transfer():
    connection = _connect()
    try:
        source, candidate = _transfer_form_pair(connection)
        if (
            source is None
            or _posted_transfer_suggestion(connection, source, candidate) is None
        ):
            flash("That transfer suggestion is no longer available.", "error")
            return redirect(url_for("attention"))
        try:
            category_id = int(request.form.get("category_id", ""))
        except ValueError:
            category_id = 0
        if not connection.execute(
            """SELECT 1 FROM categories c LEFT JOIN categories parent ON parent.id=c.parent_id
               WHERE c.id=? AND (c.is_transfer=1 OR parent.is_transfer=1)""",
            (category_id,),
        ).fetchone():
            flash("Choose a transfer category before approving.", "error")
            return redirect(url_for("attention"))
        for transaction in (source, candidate):
            cursor = connection.execute(
                """UPDATE transactions
                   SET category_id=?, rule_id=NULL, is_manual_category=1
                   WHERE account_id=? AND transaction_id=? AND pending=0""",
                (category_id, transaction["account_id"], transaction["transaction_id"]),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                flash(
                    "That transfer suggestion changed. Please review it again.", "error"
                )
                return redirect(url_for("attention"))
        connection.commit()
        flash("Transfer approved and categorized.", "success")
    finally:
        connection.close()
    return redirect(url_for("attention"))


@app.post("/transfers/reject")
def reject_transfer():
    connection = _connect()
    try:
        source, candidate = _transfer_form_pair(connection)
        if (
            source is None
            or _posted_transfer_suggestion(connection, source, candidate) is None
        ):
            flash("That transfer suggestion is no longer available.", "error")
            return redirect(url_for("attention"))
        account_id_a, transaction_id_a, account_id_b, transaction_id_b = (
            transfer_pair_key(source, candidate)
        )
        connection.execute(
            """INSERT OR IGNORE INTO transfer_rejections
               (account_id_a, transaction_id_a, account_id_b, transaction_id_b)
               VALUES (?, ?, ?, ?)""",
            (account_id_a, transaction_id_a, account_id_b, transaction_id_b),
        )
        connection.commit()
        flash("Transfer suggestion rejected.", "success")
    finally:
        connection.close()
    return redirect(url_for("attention"))


@app.post("/transfers/unmark")
def unmark_transfer():
    account_id = request.form.get("account_id", "")
    transaction_id = request.form.get("transaction_id", "")
    connection = _connect()
    try:
        row = connection.execute(
            """SELECT t.description FROM transactions t
               JOIN categories c ON c.id=t.category_id
               LEFT JOIN categories parent ON parent.id=c.parent_id
               WHERE t.account_id=? AND t.transaction_id=?
                 AND t.is_manual_category=1 AND (c.is_transfer=1 OR parent.is_transfer=1)""",
            (account_id, transaction_id),
        ).fetchone()
        if row is None:
            flash("That approved transfer is no longer available.", "error")
        else:
            category_id, rule_id = categorize_transaction(
                connection,
                {"description": row[0], "is_manual_category": 0},
            )
            connection.execute(
                """UPDATE transactions SET category_id=?, rule_id=?, is_manual_category=0
                   WHERE account_id=? AND transaction_id=?""",
                (category_id, rule_id, account_id, transaction_id),
            )
            connection.commit()
            flash("Transfer returned to automatic categorization.", "success")
    finally:
        connection.close()
    return redirect(url_for("attention"))


@app.post("/transactions/<account_id>/<transaction_id>/category")
def update_transaction_category(account_id, transaction_id):
    raw_category_id = request.form.get("category_id", "").strip()
    connection = _connect()
    try:
        exists = connection.execute(
            "SELECT description FROM transactions WHERE account_id=? AND transaction_id=?",
            (account_id, transaction_id),
        ).fetchone()
        if exists is None:
            if request.is_json or request.headers.get("X-Requested-With") == "fetch":
                return jsonify({"error": "Transaction not found."}), 404
            flash("Transaction not found.", "error")
        elif raw_category_id:
            try:
                category_id = int(raw_category_id)
            except ValueError:
                category_id = None
            if (
                category_id is None
                or not connection.execute(
                    "SELECT 1 FROM categories WHERE id=?", (category_id,)
                ).fetchone()
            ):
                if (
                    request.is_json
                    or request.headers.get("X-Requested-With") == "fetch"
                ):
                    return jsonify({"error": "Choose a valid category."}), 400
                flash("Choose a valid category.", "error")
            else:
                connection.execute(
                    "UPDATE transactions SET category_id=?, is_manual_category=1, rule_id=NULL WHERE account_id=? AND transaction_id=?",
                    (category_id, account_id, transaction_id),
                )
        else:
            from budget.categorizer import categorize_transaction

            transaction = connection.execute(
                "SELECT description, amount, category_id, rule_id FROM transactions WHERE account_id=? AND transaction_id=?",
                (account_id, transaction_id),
            ).fetchone()
            category_id, rule_id = categorize_transaction(
                connection,
                {
                    "description": transaction[0],
                    "amount": transaction[1],
                    "category_id": None,
                    "rule_id": None,
                    "is_manual_category": 0,
                },
            )
            connection.execute(
                "UPDATE transactions SET category_id=?, rule_id=?, is_manual_category=0 WHERE account_id=? AND transaction_id=?",
                (category_id, rule_id, account_id, transaction_id),
            )
        connection.commit()
        updated = connection.execute(
            "SELECT t.category_id, c.name, t.is_manual_category FROM transactions t "
            "LEFT JOIN categories c ON c.id=t.category_id "
            "WHERE t.account_id=? AND t.transaction_id=?",
            (account_id, transaction_id),
        ).fetchone()
    finally:
        connection.close()
    if request.is_json or request.headers.get("X-Requested-With") == "fetch":
        return jsonify(
            {
                "category_id": updated[0],
                "category": updated[1],
                "manual": bool(updated[2]),
            }
        )
    return_to = request.form.get("return_to", "")
    if not return_to.startswith("/") or return_to.startswith("//"):
        return_to = url_for("transactions")
    return redirect(return_to)


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    app.run(debug=debug)
