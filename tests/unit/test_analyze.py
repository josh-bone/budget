import pytest

from budget.analyze import (
    _evaluate_derived,
    build_budget,
    build_sections,
    build_summary,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────

CELLS_CONFIG = {
    "income": {
        "gross_income": "F4",
        "net_income": "F5",
    },
    "expenses": {
        "home": "O16",
        "giving": "O21",
        "total_expenses": "O68",
    },
}

SUMMARY_CONFIG = {
    "gross_income": {"section": "income", "label": "gross_income"},
    "net_income": {"section": "income", "label": "net_income"},
    "total_expenses": {"section": "expenses", "label": "total_expenses"},
    "disposable": {"derived": "net_income - total_expenses"},
}

CELL_VALUES = {
    "F4": 5000.0,
    "F5": 4000.0,
    "O16": 1500.0,
    "O21": 200.0,
    "O68": 1700.0,
}


# ── build_sections ─────────────────────────────────────────────────────────────


def test_build_sections_returns_labeled_rows():
    sections = build_sections(CELLS_CONFIG, CELL_VALUES)
    assert sections["income"][0] == {
        "label": "gross_income",
        "cell": "F4",
        "value": 5000.0,
    }
    assert sections["income"][1] == {
        "label": "net_income",
        "cell": "F5",
        "value": 4000.0,
    }


def test_build_sections_row_value_none_when_cell_absent():
    sections = build_sections(CELLS_CONFIG, {"F4": 5000.0})  # F5 and expenses missing
    net_row = next(r for r in sections["income"] if r["label"] == "net_income")
    assert net_row["value"] is None


def test_build_sections_preserves_all_sections():
    sections = build_sections(CELLS_CONFIG, CELL_VALUES)
    assert set(sections.keys()) == {"income", "expenses"}


def test_build_sections_empty_config_returns_empty():
    assert build_sections({}, {}) == {}


def test_build_sections_has_no_summary_key():
    sections = build_sections(CELLS_CONFIG, CELL_VALUES)
    assert "summary" not in sections


# ── build_summary ──────────────────────────────────────────────────────────────


def test_build_summary_resolves_section_labels():
    sections = build_sections(CELLS_CONFIG, CELL_VALUES)
    summary = build_summary(sections, SUMMARY_CONFIG)
    assert summary["gross_income"] == 5000.0
    assert summary["net_income"] == 4000.0
    assert summary["total_expenses"] == 1700.0


def test_build_summary_computes_derived_subtraction():
    sections = build_sections(CELLS_CONFIG, CELL_VALUES)
    summary = build_summary(sections, SUMMARY_CONFIG)
    assert summary["disposable"] == pytest.approx(2300.0)


def test_build_summary_derived_can_be_negative():
    values = {**CELL_VALUES, "F5": 1000.0}
    sections = build_sections(CELLS_CONFIG, values)
    summary = build_summary(sections, SUMMARY_CONFIG)
    assert summary["disposable"] == pytest.approx(-700.0)


def test_build_summary_omits_field_when_source_value_is_none():
    values = {**CELL_VALUES, "F5": None}
    sections = build_sections(CELLS_CONFIG, values)
    summary = build_summary(sections, SUMMARY_CONFIG)
    assert "net_income" not in summary


def test_build_summary_omits_derived_when_operand_missing():
    values = {**CELL_VALUES, "F5": None}  # net_income missing → disposable undefined
    sections = build_sections(CELLS_CONFIG, values)
    summary = build_summary(sections, SUMMARY_CONFIG)
    assert "disposable" not in summary


def test_build_summary_empty_config_returns_empty():
    sections = build_sections(CELLS_CONFIG, CELL_VALUES)
    assert build_summary(sections, {}) == {}


def test_build_summary_unknown_mapping_is_omitted(caplog):
    sections = build_sections(CELLS_CONFIG, CELL_VALUES)
    bad_config = {"mystery": {"unknown_key": "something"}}
    with caplog.at_level("WARNING"):
        summary = build_summary(sections, bad_config)
    assert "mystery" not in summary
    assert "unrecognised mapping" in caplog.text


# ── _evaluate_derived ──────────────────────────────────────────────────────────


def test_derived_subtraction():
    assert _evaluate_derived("a - b", {"a": 10.0, "b": 3.0}) == pytest.approx(7.0)


def test_derived_addition():
    assert _evaluate_derived("a + b", {"a": 10.0, "b": 3.0}) == pytest.approx(13.0)


def test_derived_multiplication():
    assert _evaluate_derived("a * b", {"a": 4.0, "b": 2.5}) == pytest.approx(10.0)


def test_derived_division():
    assert _evaluate_derived("a / b", {"a": 10.0, "b": 4.0}) == pytest.approx(2.5)


def test_derived_division_by_zero_returns_none():
    assert _evaluate_derived("a / b", {"a": 10.0, "b": 0.0}) is None


def test_derived_returns_none_when_left_operand_missing():
    assert _evaluate_derived("a - b", {"b": 5.0}) is None


def test_derived_returns_none_when_right_operand_missing():
    assert _evaluate_derived("a - b", {"a": 5.0}) is None


def test_derived_returns_none_for_unparseable_expression(caplog):
    with caplog.at_level("WARNING"):
        result = _evaluate_derived("not_an_expression", {"a": 1.0})
    assert result is None
    assert "Could not evaluate" in caplog.text


# ── build_budget (integration) ─────────────────────────────────────────────────


def test_build_budget_full_integration():
    result = build_budget(CELLS_CONFIG, CELL_VALUES, SUMMARY_CONFIG)
    assert result["income"][0]["value"] == 5000.0
    assert result["expenses"][0]["value"] == 1500.0
    assert result["summary"]["disposable"] == pytest.approx(2300.0)


def test_build_budget_without_summary_config():
    result = build_budget(CELLS_CONFIG, CELL_VALUES)
    assert result["summary"] == {}
    assert "income" in result
    assert "expenses" in result


def test_build_budget_summary_key_always_present():
    result = build_budget({}, {})
    assert "summary" in result


def test_build_budget_section_order_matches_config():
    result = build_budget(CELLS_CONFIG, CELL_VALUES, SUMMARY_CONFIG)
    keys = [k for k in result.keys() if k != "summary"]
    assert keys == list(CELLS_CONFIG.keys())
