from budget.analyze import build_budget


def test_build_budget_basic():
    cells_config = {"income": {"Salary": "A1"}}

    cell_values = {"A1": "1000"}

    result = build_budget(cells_config, cell_values)

    assert result == {
        "income": [{"label": "Salary", "cell": "A1", "value": "1000"}],
        "summary": {"total_income": 1000.0, "total_expenses": 0.0, "net": 1000.0},
    }, f"Unexpected result: {result}"
