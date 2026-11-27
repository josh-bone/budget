import importlib.util
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_APP_PATH = Path(__file__).resolve().parents[2] / "app.py"
_APP_SPEC = importlib.util.spec_from_file_location("budget_web_app", _APP_PATH)
assert _APP_SPEC is not None and _APP_SPEC.loader is not None
web_app = importlib.util.module_from_spec(_APP_SPEC)
_APP_SPEC.loader.exec_module(web_app)


def test_home_renders_sqlite_trends_without_loading_legacy_months(monkeypatch):
    trends = {
        "months": ["2026-01"],
        "income": [100.0],
        "expenses": [40.0],
        "diff": [60.0],
        "total_income": 100.0,
        "total_expenses": 40.0,
        "avg_income": 100.0,
        "avg_expenses": 40.0,
    }
    monkeypatch.setattr(web_app, "load_sqlite_monthly_trends", lambda: trends)
    monkeypatch.setattr(
        web_app,
        "load_all_months",
        lambda: (_ for _ in ()).throw(AssertionError("legacy loader called")),
    )
    monkeypatch.setitem(web_app.app.config, "TESTING", True)
    client = web_app.app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"2026-01" in response.data


def test_month_detail_renders_requested_budget(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "load_all_months",
        lambda: {
            "1/2026": {
                "summary": {
                    "net_income": 4000,
                    "total_expenses": 1700,
                    "diff": 2300,
                },
                "income": [{"label": "net_income", "cell": "F5", "value": 4000}],
            }
        },
    )
    monkeypatch.setitem(web_app.app.config, "TESTING", True)
    response = web_app.app.test_client().get("/month/1/2026")

    assert response.status_code == 200
    assert b"1/2026" in response.data
    assert b'data-url="/month/1-2026"' in response.data


def test_concurrent_month_loads_fetch_once(monkeypatch):
    from budget.cache import budget_cache

    budget_cache.invalidate()
    fetch_count = 0
    count_lock = threading.Lock()
    months = {"1/2026": {}}

    def uncached_load(cache):
        nonlocal fetch_count
        with count_lock:
            fetch_count += 1
        time.sleep(0.03)
        cache.set(months)
        return web_app.MonthLoadResult(months=months, failures={})

    monkeypatch.setattr(web_app, "_load_all_months_uncached", uncached_load)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: web_app.load_all_months(), range(8)))
        assert fetch_count == 1
        assert all(result.months == months for result in results)
    finally:
        budget_cache.invalidate()


def test_month_detail_logs_loader_failure(caplog, monkeypatch):
    def fail():
        raise RuntimeError("Sheets unavailable")

    monkeypatch.setattr(web_app, "load_all_months", fail)
    monkeypatch.setitem(web_app.app.config, "TESTING", True)
    response = web_app.app.test_client().get("/month/1/2026")

    assert response.status_code == 200
    assert b"Sheets unavailable" not in response.data
    assert b"Unable to load this month&#39;s budget" in response.data
    assert "Failed to load budget" in caplog.text


def test_unknown_month_returns_not_found(monkeypatch):
    monkeypatch.setattr(web_app, "load_all_months", lambda: {"1/2026": {}})
    response = web_app.app.test_client().get("/month/2/2026")

    assert response.status_code == 404


def test_security_headers_and_no_external_page_resources(monkeypatch):
    monkeypatch.setattr(
        web_app,
        "load_sqlite_monthly_trends",
        lambda: {
            "months": [],
            "income": [],
            "expenses": [],
            "diff": [],
            "total_income": 0,
            "total_expenses": 0,
            "avg_income": 0,
            "avg_expenses": 0,
        },
    )
    monkeypatch.setattr(
        web_app,
        "load_all_months",
        lambda: {
            "1/2026": {
                "summary": {"net_income": None, "total_expenses": None, "diff": None}
            }
        },
    )
    client = web_app.app.test_client()

    for path in ("/", "/month/1/2026"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert b"fonts.googleapis.com" not in response.data
        assert b"fonts.gstatic.com" not in response.data


def test_refresh_redirects_to_balances():
    response = web_app.app.test_client().post(
        "/refresh", headers={"Origin": "http://localhost"}
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/balances")


def test_refresh_rejects_cross_origin_post():
    response = web_app.app.test_client().post(
        "/refresh", headers={"Origin": "https://attacker.example"}
    )

    assert response.status_code == 403


def test_refresh_requires_origin_or_referer():
    assert web_app.app.test_client().post("/refresh").status_code == 403


def test_sort_key_orders_month_tabs_and_places_invalid_names_first():
    names = ["2/2025", "12/2024", "not-a-month", "1/2025"]

    assert sorted(names, key=web_app._sort_key) == [
        "not-a-month",
        "12/2024",
        "1/2025",
        "2/2025",
    ]


def test_build_trends_aggregates_values_and_ignores_missing_totals():
    trends = web_app.build_trends(
        {
            "1/2026": {
                "summary": {
                    "net_income": 4000,
                    "total_expenses": 1700,
                    "disposable": 2300,
                }
            },
            "2/2026": {
                "summary": {
                    "net_income": None,
                    "total_expenses": 1800,
                    "disposable": None,
                }
            },
        }
    )

    assert trends == {
        "months": ["1/2026", "2/2026"],
        "income": [4000, None],
        "expenses": [1700, 1800],
        "diff": [2300, None],
        "total_income": 4000,
        "total_expenses": 3500,
        "avg_income": 2000,
        "avg_expenses": 1750,
    }


def test_build_trends_handles_empty_input():
    assert web_app.build_trends({}) == {
        "months": [],
        "income": [],
        "expenses": [],
        "diff": [],
        "total_income": 0,
        "total_expenses": 0,
        "avg_income": 0,
        "avg_expenses": 0,
    }
