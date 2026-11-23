import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import app
from budget.balances_db import _connect
from budget.transfers import suggest_transfer_pairs


def _seed(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDGET_DB_PATH", str(tmp_path / "phase3.sqlite3"))
    connection = _connect()
    connection.executemany(
        "INSERT INTO accounts(id, name) VALUES (?, ?)",
        [("a", "Checking"), ("b", "Savings")],
    )
    transfer_id = connection.execute(
        "INSERT INTO categories(name, is_transfer) VALUES ('Transfers', 1)"
    ).lastrowid
    other_transfer_id = connection.execute(
        "INSERT INTO categories(name, is_transfer) VALUES ('Internal transfer', 1)"
    ).lastrowid
    auto_id = connection.execute(
        "INSERT INTO categories(name) VALUES ('Auto category')"
    ).lastrowid
    connection.execute(
        """INSERT INTO categorization_rules(category_id, conditions)
           VALUES (?, ?)""",
        (
            auto_id,
            json.dumps(
                {
                    "match_type": "ALL",
                    "rules": [
                        {
                            "field": "description",
                            "operator": "contains",
                            "value": "automatic",
                        }
                    ],
                }
            ),
        ),
    )
    connection.executemany(
        """INSERT INTO transactions
           (account_id, transaction_id, posted_date, amount, description)
           VALUES (?, ?, ?, ?, ?)""",
        [
            ("a", "out", "2026-03-01", -30, "automatic outgoing"),
            ("b", "in", "2026-03-03", 30, "incoming transfer"),
        ],
    )
    connection.commit()
    connection.close()
    app.config.update(TESTING=True)
    return transfer_id, other_transfer_id, auto_id


def _pair_form(
    source_account="a",
    source_transaction="out",
    candidate_account="b",
    candidate_transaction="in",
):
    return {
        "source_account_id": source_account,
        "source_transaction_id": source_transaction,
        "candidate_account_id": candidate_account,
        "candidate_transaction_id": candidate_transaction,
    }


def test_review_page_lists_pairs_and_all_flagged_categories(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)

    response = app.test_client().get("/transfers")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "automatic outgoing" in html
    assert "incoming transfer" in html
    assert "Transfers" in html
    assert "Internal transfer" in html
    assert "Reject pair" in html


def test_approval_assigns_selected_transfer_category_manually(monkeypatch, tmp_path):
    _, selected_category, _ = _seed(monkeypatch, tmp_path)
    client = app.test_client()

    response = client.post(
        "/transfers/approve",
        data={**_pair_form(), "category_id": str(selected_category)},
    )

    assert response.status_code == 302
    connection = _connect()
    rows = connection.execute(
        "SELECT category_id, rule_id, is_manual_category FROM transactions ORDER BY amount"
    ).fetchall()
    assert rows == [(selected_category, None, 1), (selected_category, None, 1)]
    assert suggest_transfer_pairs(connection) == []
    connection.close()


def test_reject_persists_pair_and_prevents_resent_suggestion(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    client = app.test_client()

    response = client.post("/transfers/reject", data=_pair_form())

    assert response.status_code == 302
    connection = _connect()
    assert suggest_transfer_pairs(connection) == []
    assert (
        connection.execute("SELECT COUNT(*) FROM transfer_rejections").fetchone()[0]
        == 1
    )
    connection.close()
    assert "No transfer pairs need review" in client.get("/transfers").get_data(
        as_text=True
    )


def test_unmark_returns_transaction_to_rule_categorization(monkeypatch, tmp_path):
    _, transfer_category, auto_category = _seed(monkeypatch, tmp_path)
    client = app.test_client()
    client.post(
        "/transfers/approve",
        data={**_pair_form(), "category_id": str(transfer_category)},
    )

    response = client.post(
        "/transfers/unmark",
        data={"account_id": "a", "transaction_id": "out"},
    )

    assert response.status_code == 302
    connection = _connect()
    assert connection.execute(
        "SELECT category_id, is_manual_category FROM transactions WHERE transaction_id='out'"
    ).fetchone() == (auto_category, 0)
    assert connection.execute(
        "SELECT category_id, is_manual_category FROM transactions WHERE transaction_id='in'"
    ).fetchone() == (transfer_category, 1)
    connection.close()


def test_approval_rejects_non_transfer_categories(monkeypatch, tmp_path):
    _, _, auto_category = _seed(monkeypatch, tmp_path)

    response = app.test_client().post(
        "/transfers/approve",
        data={**_pair_form(), "category_id": str(auto_category)},
    )

    assert response.status_code == 302
    connection = _connect()
    assert connection.execute(
        "SELECT category_id, is_manual_category FROM transactions WHERE transaction_id='out'"
    ).fetchone() == (None, 0)
    connection.close()


def test_child_of_transfer_category_can_be_selected_for_approval(monkeypatch, tmp_path):
    parent_id, _, _ = _seed(monkeypatch, tmp_path)
    connection = _connect()
    child_id = connection.execute(
        "INSERT INTO categories(name, parent_id) VALUES ('Card payment', ?)",
        (parent_id,),
    ).lastrowid
    connection.commit()
    connection.close()

    response = app.test_client().post(
        "/transfers/approve",
        data={**_pair_form(), "category_id": str(child_id)},
    )

    assert response.status_code == 302
    connection = _connect()
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE category_id=? AND is_manual_category=1",
            (child_id,),
        ).fetchone()[0]
        == 2
    )
    connection.close()


def test_ambiguous_rejection_only_hides_rejected_candidate(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    connection = _connect()
    connection.execute(
        """INSERT INTO transactions
           (account_id, transaction_id, posted_date, amount, description)
           VALUES ('b', 'in-2', '2026-03-04', 30, 'another candidate')"""
    )
    connection.commit()
    connection.close()
    client = app.test_client()

    client.post("/transfers/reject", data=_pair_form())

    connection = _connect()
    pairs = suggest_transfer_pairs(connection)
    connection.close()
    assert len(pairs) == 1
    assert pairs[0]["candidate"]["transaction_id"] == "in-2"
