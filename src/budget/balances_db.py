"""SQLite persistence for balance snapshots, transactions, and categorization."""

import logging
import os
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from budget.utils import parse_money

logger = logging.getLogger(__name__)


def _database_path(db_path: str | os.PathLike[str] | None = None) -> str:
    if db_path is not None:
        return os.fspath(db_path)
    configured_path = os.environ.get("BUDGET_DB_PATH")
    if configured_path:
        return configured_path
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return os.fspath(data_home / "budget" / "balances.sqlite3")


def _connect(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    path = _database_path(db_path)
    if path != ":memory:":
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        path = os.path.expanduser(path)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    connection.execute("""
        CREATE TABLE IF NOT EXISTS balance_snapshots (
            account TEXT NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual',
            PRIMARY KEY (account, date)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS simplefin_request_counts (
            request_date TEXT PRIMARY KEY,
            request_count INTEGER NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )
    """)
    connection.execute("""CREATE TABLE IF NOT EXISTS account_aliases (
        account_key TEXT NOT NULL,
        alias TEXT NOT NULL COLLATE NOCASE,
        PRIMARY KEY (account_key, alias)
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS account_owners (
        account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON UPDATE CASCADE ON DELETE CASCADE,
        owner TEXT NOT NULL
    )""")
    account_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(accounts)")
    }
    if "closed_date" not in account_columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN closed_date TEXT")
    if "owner" in account_columns:
        connection.execute(
            """INSERT OR IGNORE INTO account_owners (account_id, owner)
               SELECT id, owner FROM accounts WHERE owner IS NOT NULL AND owner != ''"""
        )
        connection.execute("ALTER TABLE accounts DROP COLUMN owner")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            account_id TEXT NOT NULL REFERENCES accounts(id) ON UPDATE CASCADE ON DELETE CASCADE,
            transaction_id TEXT NOT NULL,
            posted_date TEXT,
            amount REAL NOT NULL,
            description TEXT NOT NULL,
            pending INTEGER NOT NULL DEFAULT 0,
            transacted_at TEXT,
            category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            rule_id INTEGER REFERENCES categorization_rules(id) ON DELETE SET NULL,
            is_manual_category INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (account_id, transaction_id)
        )
    """)
    connection.execute("""CREATE TABLE IF NOT EXISTS transfer_rejections (
        account_id_a TEXT NOT NULL,
        transaction_id_a TEXT NOT NULL,
        account_id_b TEXT NOT NULL,
        transaction_id_b TEXT NOT NULL,
        rejected_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (account_id_a, transaction_id_a, account_id_b, transaction_id_b),
        CHECK (account_id_a < account_id_b OR
               (account_id_a = account_id_b AND transaction_id_a < transaction_id_b)),
        FOREIGN KEY (account_id_a, transaction_id_a)
            REFERENCES transactions(account_id, transaction_id) ON UPDATE CASCADE ON DELETE CASCADE,
        FOREIGN KEY (account_id_b, transaction_id_b)
            REFERENCES transactions(account_id, transaction_id) ON UPDATE CASCADE ON DELETE CASCADE
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS refunds (
        refund_account_id TEXT NOT NULL,
        refund_transaction_id TEXT NOT NULL,
        purchase_account_id TEXT NOT NULL,
        purchase_transaction_id TEXT NOT NULL,
        approved_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (refund_account_id, refund_transaction_id),
        UNIQUE (purchase_account_id, purchase_transaction_id),
        FOREIGN KEY (refund_account_id, refund_transaction_id)
            REFERENCES transactions(account_id, transaction_id) ON UPDATE CASCADE ON DELETE CASCADE,
        FOREIGN KEY (purchase_account_id, purchase_transaction_id)
            REFERENCES transactions(account_id, transaction_id) ON UPDATE CASCADE ON DELETE CASCADE
    )""")
    # Create categorization tables before adding their foreign-key columns to
    # an existing transactions table.
    connection.execute("""CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    category_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(categories)")
    }
    if "parent_id" not in category_columns:
        connection.execute(
            "ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT"
        )
    if "is_transfer" not in category_columns:
        connection.execute(
            "ALTER TABLE categories ADD COLUMN is_transfer INTEGER NOT NULL DEFAULT 0"
        )
    if schema_version < 7:
        # Transfer categories may already exist under either common spelling.
        connection.execute(
            "UPDATE categories SET is_transfer=1 WHERE name COLLATE NOCASE IN ('Transfer', 'Transfers')"
        )
        connection.commit()
    connection.execute("""CREATE TRIGGER IF NOT EXISTS categories_parent_insert
        BEFORE INSERT ON categories WHEN NEW.parent_id IS NOT NULL
        BEGIN
          SELECT CASE WHEN NEW.parent_id = NEW.id OR
            (SELECT parent_id FROM categories WHERE id=NEW.parent_id) IS NOT NULL
            THEN RAISE(ABORT, 'category depth limit exceeded') END;
        END""")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS categories_parent_update
        BEFORE UPDATE OF parent_id ON categories WHEN NEW.parent_id IS NOT NULL
        BEGIN
          SELECT CASE WHEN NEW.parent_id = NEW.id OR
            (SELECT parent_id FROM categories WHERE id=NEW.parent_id) IS NOT NULL
            THEN RAISE(ABORT, 'category depth limit exceeded') END;
        END""")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS categories_parent_subtree_update
        BEFORE UPDATE OF parent_id ON categories
        WHEN NEW.parent_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM categories WHERE parent_id=NEW.id
        )
        BEGIN
          SELECT RAISE(ABORT, 'category with subcategories cannot be nested');
        END""")
    connection.execute("""CREATE TABLE IF NOT EXISTS categorization_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
        priority INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 1,
        conditions TEXT NOT NULL CHECK (json_valid(conditions)),
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rules_priority ON categorization_rules(is_active, priority, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_sort_date ON transactions(COALESCE(posted_date, transacted_at) DESC)"
    )
    transaction_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(transactions)")
    }
    for column, definition in (
        ("category_id", "INTEGER REFERENCES categories(id) ON DELETE SET NULL"),
        ("rule_id", "INTEGER REFERENCES categorization_rules(id) ON DELETE SET NULL"),
        ("is_manual_category", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in transaction_columns:
            connection.execute(
                f"ALTER TABLE transactions ADD COLUMN {column} {definition}"
            )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(balance_snapshots)")
    }
    if "source" not in columns:
        # Existing rows came from the manually maintained Balances sheet.
        connection.execute(
            "ALTER TABLE balance_snapshots ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"
        )
    connection.execute("PRAGMA user_version = 9")
    return connection


def reserve_simplefin_request(
    *, limit: int = 24, db_path: str | os.PathLike[str] | None = None
) -> bool:
    """Atomically reserve one SimpleFIN request for today, up to ``limit``."""
    today = date.today().isoformat()
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT request_count FROM simplefin_request_counts WHERE request_date = ?",
            (today,),
        ).fetchone()
        count = row[0] if row else 0
        if count >= limit:
            connection.rollback()
            return False
        connection.execute(
            """INSERT INTO simplefin_request_counts (request_date, request_count)
               VALUES (?, 1) ON CONFLICT(request_date) DO UPDATE SET
               request_count = request_count + 1""",
            (today,),
        )
        connection.commit()
        return True
    finally:
        connection.close()


def _as_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value).isoformat()


def insert_balance_snapshots(
    snapshots: Iterable[Mapping[str, Any]],
    snapshot_date: date | datetime | str | None = None,
    *,
    source: str = "manual",
    db_path: str | os.PathLike[str] | None = None,
) -> None:
    """Insert or replace snapshots for a date.

    Each item must contain ``Account`` or ``name`` and ``Amount`` or ``balance``.
    If a record includes ``Date`` or ``date``, it takes precedence over the
    shared ``snapshot_date`` (which defaults to today).
    """
    default_date = _as_date(snapshot_date or date.today())
    rows = []
    for snapshot in snapshots:
        account = snapshot.get("Account", snapshot.get("name"))
        amount = snapshot.get("Amount", snapshot.get("balance"))
        row_date = snapshot.get("Date", snapshot.get("date", default_date))
        if account is None or amount is None:
            raise ValueError("Each snapshot must include an account and amount")
        rows.append((str(account), _as_date(row_date), float(amount), source))

    connection = _connect(db_path)
    try:
        connection.executemany(
            """INSERT INTO balance_snapshots (account, date, amount, source)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(account, date) DO UPDATE SET
                   amount = excluded.amount, source = excluded.source""",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def import_balance_history(
    balances: pd.DataFrame,
    *,
    db_path: str | os.PathLike[str] | None = None,
) -> tuple[int, int]:
    """Import sheet history, skipping incomplete or invalid rows.

    Returns the number of valid rows written and the number skipped. Repeated
    account/date pairs are processed in sheet order, so the final row wins.
    """
    rows = []
    skipped = 0
    for _, record in balances.iterrows():
        date_value = record.get("Date")
        account_value = record.get("Account")
        amount_value = record.get("Amount")
        is_blank = any(
            pd.isna(value) or not str(value).strip()
            for value in (date_value, account_value, amount_value)
        )
        if is_blank:
            skipped += 1
            continue

        parsed_date = pd.to_datetime(date_value, errors="coerce")
        try:
            amount = parse_money(amount_value)
        except ValueError:
            amount = float("nan")
        if pd.isna(parsed_date) or amount is None or pd.isna(amount):
            skipped += 1
            continue
        rows.append(
            (
                str(account_value).strip(),
                parsed_date.date().isoformat(),
                amount,
                "manual",
            )
        )

    connection = _connect(db_path)
    try:
        connection.executemany(
            """INSERT INTO balance_snapshots (account, date, amount, source)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(account, date) DO UPDATE SET
                   amount = excluded.amount, source = excluded.source""",
            rows,
        )
        connection.commit()
    finally:
        connection.close()

    logger.info(
        "Imported %d balance row(s); skipped %d incomplete or invalid row(s).",
        len(rows),
        skipped,
    )
    return len(rows), skipped


def get_balance_history(
    *, db_path: str | os.PathLike[str] | None = None
) -> pd.DataFrame:
    """Return history under canonical account names when a name or alias matches."""
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            "SELECT date, account, amount, source FROM balance_snapshots ORDER BY date, account"
        ).fetchall()
        canonical_names = {
            account_id: name
            for account_id, name in connection.execute("SELECT id, name FROM accounts")
        }
        name_map = {name.casefold(): name for name in canonical_names.values()}
        for account_id, alias in connection.execute(
            "SELECT account_key, alias FROM account_aliases"
        ):
            if account_id in canonical_names:
                name_map[alias.casefold()] = canonical_names[account_id]
    finally:
        connection.close()
    rows = [
        (date_value, name_map.get(account.casefold(), account), amount, source)
        for date_value, account, amount, source in rows
    ]
    return pd.DataFrame(rows, columns=["Date", "Account", "Amount", "Source"])


def insert_simplefin_transactions(
    accounts: Iterable[Mapping[str, Any]],
    *,
    db_path: str | os.PathLike[str] | None = None,
) -> int:
    """Upsert mapped SimpleFIN accounts and their transactions; return rows written."""
    account_rows = []
    owner_rows = []
    transaction_rows = []
    for account in accounts:
        account_id = account.get("id")
        account_name = account.get("name")
        if account_id is None or account_name is None:
            raise ValueError("Each account must include an id and name")
        account_rows.append((str(account_id), str(account_name)))
        if account.get("owner") not in (None, ""):
            owner_rows.append((str(account_id), str(account["owner"])))
        for transaction in account.get("transactions", []):
            transaction_id = transaction.get("id")
            description = transaction.get("description")
            posted = transaction.get("posted")
            amount = transaction.get("amount")
            if (
                transaction_id is None
                or description is None
                or posted is None
                or amount is None
            ):
                raise ValueError(
                    "Each transaction must include id, posted, amount, and description"
                )
            posted_date = (
                None
                if int(posted) == 0
                else datetime.fromtimestamp(int(posted), UTC).date().isoformat()
            )
            transacted = transaction.get("transacted_at")
            transacted_date = (
                None
                if transacted in (None, 0)
                else datetime.fromtimestamp(int(transacted), UTC).date().isoformat()
            )
            transaction_rows.append(
                (
                    str(account_id),
                    str(transaction_id),
                    posted_date,
                    float(amount),
                    str(description),
                    int(bool(transaction.get("pending", False))),
                    transacted_date,
                )
            )

    connection = _connect(db_path)
    try:
        connection.executemany(
            """INSERT INTO accounts (id, name) VALUES (?, ?)
               ON CONFLICT(id) DO UPDATE SET name = excluded.name""",
            account_rows,
        )
        # The latest SimpleFIN payload is authoritative for every imported
        # account, including when it no longer includes an owner.
        imported_account_ids = [account_id for account_id, _ in account_rows]
        connection.executemany(
            "DELETE FROM account_owners WHERE account_id=?",
            [(account_id,) for account_id in imported_account_ids],
        )
        connection.executemany(
            "INSERT INTO account_owners (account_id, owner) VALUES (?, ?)",
            owner_rows,
        )
        connection.executemany(
            """INSERT INTO transactions
               (account_id, transaction_id, posted_date, amount, description, pending, transacted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, transaction_id) DO UPDATE SET
                 posted_date=excluded.posted_date, amount=excluded.amount,
                 description=excluded.description, pending=excluded.pending,
                 transacted_at=excluded.transacted_at""",
            transaction_rows,
        )
        from budget.categorizer import categorize_unassigned

        categorize_unassigned(connection)
        connection.commit()
    finally:
        connection.close()
    return len(transaction_rows)


def get_transactions(*, db_path: str | os.PathLike[str] | None = None) -> pd.DataFrame:
    """Return saved transactions with their mapped account names."""
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            """SELECT t.posted_date, a.name, t.amount, t.description, t.pending,
                      t.transaction_id, t.transacted_at, c.name
               FROM transactions t JOIN accounts a ON a.id = t.account_id
               LEFT JOIN categories c ON c.id = t.category_id
               ORDER BY t.posted_date, a.name, t.transaction_id"""
        ).fetchall()
    finally:
        connection.close()
    return pd.DataFrame(
        rows,
        columns=[
            "Date",
            "Account",
            "Amount",
            "Description",
            "Pending",
            "Transaction ID",
            "Transacted At",
            "Category",
        ],
    )
