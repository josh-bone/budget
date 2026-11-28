from html.parser import HTMLParser
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import app, load_sqlite_monthly_trends
from budget.balances_db import _connect


class _CategoryFilterParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.category_ids = set()

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if (
            attributes.get("type") == "checkbox"
            and "cat-cb" in classes
            and attributes.get("name") == "category_id"
        ):
            self.category_ids.add(attributes.get("value"))


def _category_filter_ids(html):
    parser = _CategoryFilterParser()
    parser.feed(html)
    return parser.category_ids


def _seed_transfer_data(monkeypatch, tmp_path):
    db_path = tmp_path / "transfers.sqlite3"
    monkeypatch.setenv("BUDGET_DB_PATH", str(db_path))
    connection = _connect()
    connection.execute("INSERT INTO accounts(id, name) VALUES ('checking', 'Checking')")
    connection.execute("INSERT INTO accounts(id, name) VALUES ('savings', 'Savings')")
    connection.execute(
        "INSERT INTO categories(name, is_transfer) VALUES ('Transfer', 1)"
    )
    transfer_id = connection.execute(
        "SELECT id FROM categories WHERE name='Transfer'"
    ).fetchone()[0]
    connection.execute("INSERT INTO categories(name) VALUES ('Food')")
    food_id = connection.execute(
        "SELECT id FROM categories WHERE name='Food'"
    ).fetchone()[0]
    connection.executemany(
        """INSERT INTO transactions(account_id, transaction_id, posted_date, amount,
                  description, category_id) VALUES (?, ?, '2026-05-10', ?, ?, ?)""",
        [
            ("checking", "transfer-out", -40, "Move to savings", transfer_id),
            ("savings", "transfer-in", 40, "Move from checking", transfer_id),
            ("checking", "food", -20, "Groceries", food_id),
            ("checking", "income", 100, "Paycheck", None),
        ],
    )
    connection.commit()
    connection.close()
    app.config.update(TESTING=True)
    return transfer_id


def test_categories_migrate_with_transfer_flag_default(tmp_path):
    db_path = tmp_path / "migration.sqlite3"
    connection = _connect(db_path)
    connection.execute("INSERT INTO categories(name) VALUES ('Old category')")
    connection.execute("ALTER TABLE categories DROP COLUMN is_transfer")
    connection.commit()
    connection.close()

    connection = _connect(db_path)
    flag = connection.execute(
        "SELECT is_transfer FROM categories WHERE name='Old category'"
    ).fetchone()[0]
    connection.close()
    assert flag == 0


def test_existing_transfer_category_is_flagged_by_migration(tmp_path):
    db_path = tmp_path / "existing-transfer.sqlite3"
    connection = _connect(db_path)
    connection.execute("INSERT INTO categories(name) VALUES ('Transfer')")
    connection.execute("PRAGMA user_version = 5")
    connection.commit()
    connection.close()

    connection = _connect(db_path)
    flag = connection.execute(
        "SELECT is_transfer FROM categories WHERE name='Transfer'"
    ).fetchone()[0]
    connection.close()
    assert flag == 1


def test_existing_transfers_category_is_flagged_by_migration(tmp_path):
    db_path = tmp_path / "existing-transfers.sqlite3"
    connection = _connect(db_path)
    connection.execute("INSERT INTO categories(name) VALUES ('Transfers')")
    connection.execute("PRAGMA user_version = 6")
    connection.commit()
    connection.close()

    connection = _connect(db_path)
    flag = connection.execute(
        "SELECT is_transfer FROM categories WHERE name='Transfers'"
    ).fetchone()[0]
    connection.close()
    assert flag == 1


def test_transfer_flag_controls_category_ui(monkeypatch, tmp_path):
    transfer_id = _seed_transfer_data(monkeypatch, tmp_path)
    client = app.test_client()

    response = client.get("/rules")
    assert b"Transfer" in response.data
    response = client.get("/transactions")
    html = response.get_data(as_text=True)
    assert f'value="{transfer_id}"' in html  # editable category selector
    assert str(transfer_id) not in _category_filter_ids(html)

    client.post(f"/categories/{transfer_id}/transfer", data={"is_transfer": "0"})
    response = client.get("/transactions")
    assert str(transfer_id) in _category_filter_ids(response.get_data(as_text=True))

    # Checked HTML forms submit both hidden 0 and checked 1 values.
    client.post(
        f"/categories/{transfer_id}/transfer",
        data={"is_transfer": ["0", "1"]},
    )
    assert client.get("/api/transactions/spend").get_json()["total"] == 20


def test_transactions_toggle_count_and_spend_exclusions(monkeypatch, tmp_path):
    _seed_transfer_data(monkeypatch, tmp_path)
    client = app.test_client()

    default_page = client.get("/transactions").get_data(as_text=True)
    assert "2 transactions" in default_page
    assert "Move to savings" not in default_page
    assert "$20.00" in default_page

    shown_page = client.get("/transactions?show_transfers=1").get_data(as_text=True)
    assert "4 transactions" in shown_page
    assert "Move to savings" in shown_page
    assert "Show transfers" in shown_page

    spend = client.get("/api/transactions/spend").get_json()
    assert spend["total"] == 20
    assert spend["breakdown"] == [
        {"name": "Food", "amount": 20, "percent": 100, "color": "#4778a8"}
    ]


def test_budget_overview_omits_transfer_income_and_expense(monkeypatch, tmp_path):
    _seed_transfer_data(monkeypatch, tmp_path)

    trends = load_sqlite_monthly_trends()
    assert trends["total_income"] == 100
    assert trends["total_expenses"] == 20


def test_child_of_transfer_category_inherits_transfer_behavior(monkeypatch, tmp_path):
    transfer_id = _seed_transfer_data(monkeypatch, tmp_path)
    connection = _connect()
    child_id = connection.execute(
        "INSERT INTO categories(name, parent_id) VALUES ('Credit Card Payment', ?)",
        (transfer_id,),
    ).lastrowid
    connection.execute(
        "UPDATE transactions SET category_id=? WHERE transaction_id IN ('transfer-out', 'transfer-in')",
        (child_id,),
    )
    connection.commit()
    connection.close()
    client = app.test_client()

    page = client.get("/transactions").get_data(as_text=True)
    shown = client.get("/transactions?show_transfers=1").get_data(as_text=True)
    assert "2 transactions" in page
    assert "Move to savings" not in page
    assert "Credit Card Payment" in shown
    assert (
        "Credit Card Payment"
        not in page.split('id="catDropdown"', 1)[1].split("</details>", 1)[0]
    )
    assert client.get("/api/transactions/spend").get_json()["total"] == 20
    assert load_sqlite_monthly_trends()["total_expenses"] == 20
    assert b"Transfer via parent" in client.get("/rules").data
