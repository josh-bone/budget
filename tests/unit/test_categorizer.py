import json

from budget.balances_db import _connect, insert_simplefin_transactions
from budget.categorizer import categorize_transaction, recategorize_all


def _rule(connection, category_id, text, priority=0):
    conditions = json.dumps(
        {
            "match_type": "ALL",
            "rules": [{"field": "description", "operator": "contains", "value": text}],
        }
    )
    return connection.execute(
        "INSERT INTO categorization_rules(category_id, priority, conditions) VALUES (?, ?, ?)",
        (category_id, priority, conditions),
    ).lastrowid


def test_case_insensitive_priority_and_null_description(tmp_path):
    connection = _connect(tmp_path / "db.sqlite")
    first = connection.execute(
        "INSERT INTO categories(name) VALUES ('First')"
    ).lastrowid
    second = connection.execute(
        "INSERT INTO categories(name) VALUES ('Second')"
    ).lastrowid
    _rule(connection, first, "coffee", 5)
    winner = _rule(connection, second, "COFFEE", 1)
    assert categorize_transaction(connection, {"description": "Morning Coffee"}) == (
        second,
        winner,
    )
    assert categorize_transaction(connection, {"description": None}) == (None, None)
    connection.close()


def test_sync_categorizes_but_preserves_manual_override(tmp_path):
    path = tmp_path / "db.sqlite"
    connection = _connect(path)
    category = connection.execute(
        "INSERT INTO categories(name) VALUES ('Coffee')"
    ).lastrowid
    _rule(connection, category, "coffee")
    connection.commit()
    connection.close()
    account = {
        "id": "a",
        "name": "Checking",
        "transactions": [
            {
                "id": "t",
                "posted": 1794000000,
                "amount": -4,
                "description": "Coffee shop",
            }
        ],
    }
    insert_simplefin_transactions([account], db_path=path)
    connection = _connect(path)
    original = connection.execute(
        "SELECT category_id, rule_id FROM transactions"
    ).fetchone()
    manual_category = connection.execute(
        "INSERT INTO categories(name) VALUES ('Manual')"
    ).lastrowid
    connection.execute(
        "UPDATE transactions SET category_id=?, rule_id=NULL, is_manual_category=1",
        (manual_category,),
    )
    connection.commit()
    connection.close()
    account["transactions"][0]["description"] = "Not coffee"
    insert_simplefin_transactions([account], db_path=path)
    connection = _connect(path)
    assert original[0] == category and original[1] is not None
    assert connection.execute(
        "SELECT category_id, rule_id, is_manual_category FROM transactions"
    ).fetchone() == (manual_category, None, 1)
    assert recategorize_all(connection) == 0
    connection.close()


def test_recategorize_clears_no_longer_matching_rule(tmp_path):
    connection = _connect(tmp_path / "db.sqlite")
    category = connection.execute(
        "INSERT INTO categories(name) VALUES ('Coffee')"
    ).lastrowid
    rule_id = _rule(connection, category, "coffee")
    connection.execute("INSERT INTO accounts(id, name) VALUES ('a', 'Checking')")
    connection.execute(
        "INSERT INTO transactions(account_id, transaction_id, description, amount, category_id, rule_id) VALUES ('a','t','coffee',-1,?,?)",
        (category, rule_id),
    )
    connection.execute("UPDATE transactions SET description='tea'")
    assert recategorize_all(connection) == 1
    assert connection.execute(
        "SELECT category_id, rule_id FROM transactions"
    ).fetchone() == (None, None)
    connection.close()


def test_simplefin_reimport_replaces_and_clears_account_owner(tmp_path):
    path = tmp_path / "db.sqlite"
    account = {"id": "a", "name": "Checking", "owner": "Owner A", "transactions": []}

    insert_simplefin_transactions([account], db_path=path)
    account["owner"] = "Owner B"
    insert_simplefin_transactions([account], db_path=path)

    connection = _connect(path)
    assert connection.execute(
        "SELECT owner FROM account_owners WHERE account_id='a'"
    ).fetchone() == ("Owner B",)
    connection.close()

    account.pop("owner")
    insert_simplefin_transactions([account], db_path=path)
    connection = _connect(path)
    assert connection.execute(
        "SELECT owner FROM account_owners WHERE account_id='a'"
    ).fetchone() is None
    connection.close()
