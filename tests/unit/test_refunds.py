from datetime import date, timedelta

from budget.balances_db import _connect
from budget.refunds import REFUND_MATCH_WINDOW_DAYS, suggest_refund_pairs


def setup_db(tmp_path):
    db = _connect(tmp_path / "refunds.sqlite3")
    db.execute("INSERT INTO accounts(id,name) VALUES ('a','Checking')")
    cat = db.execute("INSERT INTO categories(name) VALUES ('Food')").lastrowid
    return db, cat


def add(db, ident, amount, offset, *, account='a', pending=0, category=None):
    day = (date(2026, 1, 1) + timedelta(days=offset)).isoformat()
    db.execute("INSERT INTO transactions(account_id,transaction_id,posted_date,amount,description,pending,category_id) VALUES(?,?,?,?,?,?,?)", (account,ident,day,amount,ident,pending,category))


def test_exact_same_account_refund_boundary_and_ambiguity(tmp_path):
    db, cat = setup_db(tmp_path)
    add(db, 'purchase', -20, 0, category=cat)
    add(db, 'another', -20, 1, category=cat)
    add(db, 'credit', 20, REFUND_MATCH_WINDOW_DAYS)
    add(db, 'too-late', -8, 0)
    add(db, 'late-credit', 8, REFUND_MATCH_WINDOW_DAYS + 1)
    add(db, 'pending', -20, 0, pending=1)
    pairs = suggest_refund_pairs(db)
    assert {(p['purchase']['transaction_id'], p['refund']['transaction_id']) for p in pairs} == {
        ('purchase', 'credit'), ('another', 'credit')
    }
    assert db.execute("SELECT category_id FROM transactions WHERE transaction_id='credit'").fetchone() == (None,)
    db.close()


def test_excludes_cross_account_linked_and_transfer_transactions(tmp_path):
    db, _ = setup_db(tmp_path)
    db.execute("INSERT INTO accounts(id,name) VALUES ('b','Savings')")
    add(db, 'purchase', -10, 0)
    add(db, 'credit', 10, 1)
    add(db, 'other-account', -10, 0, account='b')
    assert len(suggest_refund_pairs(db)) == 1
    db.execute("INSERT INTO refunds(refund_account_id,refund_transaction_id,purchase_account_id,purchase_transaction_id) VALUES('a','credit','a','purchase')")
    assert suggest_refund_pairs(db) == []
    db.close()


def test_approved_refund_reporting_undo_and_import_upsert(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("refund_test_app", Path(__file__).resolve().parents[2] / "app.py")
    web_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web_app)
    from budget.balances_db import insert_simplefin_transactions

    path = tmp_path / "report.sqlite3"
    insert_simplefin_transactions([{"id":"a","name":"Checking","transactions":[
        {"id":"purchase","posted":1767225600,"amount":-30,"description":"Groceries"},
        {"id":"refund","posted":1767312000,"amount":30,"description":"Store refund"},
        {"id":"wage","posted":1767312000,"amount":100,"description":"Wages"},
    ]}], db_path=path)
    db = _connect(path)
    category = db.execute("INSERT INTO categories(name) VALUES ('Food')").lastrowid
    db.execute("UPDATE transactions SET category_id=? WHERE transaction_id='purchase'", (category,))
    db.execute("INSERT INTO refunds(refund_account_id,refund_transaction_id,purchase_account_id,purchase_transaction_id) VALUES('a','refund','a','purchase')")
    db.commit()
    monkeypatch.setattr(web_app, "_connect", lambda: _connect(path))
    trend = web_app.load_sqlite_monthly_trends()
    assert trend["income"] == [100.0]
    assert trend["expenses"] == [0.0]
    insert_simplefin_transactions([{"id":"a","name":"Checking","transactions":[
        {"id":"refund","posted":1767312000,"amount":30,"description":"Store refund updated"},
    ]}], db_path=path)
    db = _connect(path)
    assert db.execute("SELECT purchase_transaction_id FROM refunds WHERE refund_transaction_id='refund'").fetchone() == ('purchase',)
    db.close()
    web_app.app.config["TESTING"] = True
    response = web_app.app.test_client().post("/refunds/undo", data={"refund_account_id":"a","refund_transaction_id":"refund"})
    assert response.status_code == 302
    assert _connect(path).execute("SELECT COUNT(*) FROM refunds").fetchone() == (0,)
