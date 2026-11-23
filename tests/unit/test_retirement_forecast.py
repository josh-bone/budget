import numpy as np
import pandas as pd
import pytest

from budget.retirement_forecast import (
    RetirementForecastConfig,
    block_bootstrap_paths,
    build_contribution_schedule,
    probability_of_reaching_target,
    simulate_wealth,
    summarize_terminal_wealth,
    load_historical_data,
    run_retirement_forecast,
    get_total_retirement_savings_balance,
    get_current_retirement_savings_contributions,
)


def test_load_historical_data_validates_and_cleans_csv(tmp_path, caplog):
    csv = tmp_path / "history.csv"
    pd.DataFrame(
        {
            "date": ["2020-02-01", "2020-01-01", "2020-03-01"],
            "nominal_return": [0.2, 0.1, None],
            "inflation": [0.02, 0.01, None],
        }
    ).to_csv(csv, index=False)
    frame = load_historical_data(str(csv))
    assert list(frame.date.dt.month) == [1, 2]
    assert "Dropped 1 row" in caplog.text
    with pytest.raises(FileNotFoundError):
        load_historical_data(str(tmp_path / "missing.csv"))
    pd.DataFrame({"date": ["2020-01-01"]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="missing required"):
        load_historical_data(str(csv))


def test_load_historical_data_rejects_duplicates_and_warns_missing_month(
    tmp_path, caplog
):
    csv = tmp_path / "history.csv"
    pd.DataFrame(
        {
            "date": ["2020-01-01", "2020-03-01"],
            "nominal_return": [0, 0],
            "inflation": [0, 0],
        }
    ).to_csv(csv, index=False)
    load_historical_data(str(csv))
    assert "missing month" in caplog.text
    pd.DataFrame(
        {
            "date": ["2020-01-01", "2020-01-01"],
            "nominal_return": [0, 0],
            "inflation": [0, 0],
        }
    ).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="Duplicate dates"):
        load_historical_data(str(csv))


def test_forecast_runs_and_csv_override_preserves_config(tmp_path):
    csv = tmp_path / "history.csv"
    pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=3, freq="MS"),
            "nominal_return": [0.01] * 3,
            "inflation": [0] * 3,
        }
    ).to_csv(csv, index=False)
    config = RetirementForecastConfig(
        current_age=30,
        retirement_age=31,
        life_expectancy=32,
        block_length_months=2,
        n_simulations=4,
        target_real_balance=100,
        contribution_mode="fixed",
    )
    result = run_retirement_forecast(
        config=config, csv_path=str(csv), initial_balance=50, monthly_contribution=10
    )
    assert result.config.current_age == 30
    assert result.nominal_wealth.shape == (4, 25)
    assert result.contributions.shape == (4, 24)
    assert result.probability_of_reaching_target == 1


def test_retirement_balance_resolution_handles_fallbacks_and_patterns():
    assert get_total_retirement_savings_balance(balances_df=pd.DataFrame()) == 135838.28
    assert (
        get_total_retirement_savings_balance(balances_df=pd.DataFrame({"amount": [1]}))
        == 135838.28
    )
    frame = pd.DataFrame(
        {
            "Date": ["2024-02", "2024-02", "2024-02"],
            "Account Name": ["401k", "checking", "Roth"],
            "Amount": [10, 20, "$30"],
        }
    )
    assert get_total_retirement_savings_balance(balances_df=frame) == 40
    assert (
        get_total_retirement_savings_balance(
            balances_df=frame, account_patterns=["no match"]
        )
        == 60
    )


def test_contribution_resolution_summary_rows_and_fallback():
    assert get_current_retirement_savings_contributions() == 4306.58
    assert (
        get_current_retirement_savings_contributions(
            latest_budget={"summary": {"monthly_contributions": "25"}}
        )
        == 25
    )
    budget = {
        "summary": {},
        "income": [
            {"label": "401k match", "value": 20},
            {"label": "ordinary", "value": 90},
            "ignored",
        ],
    }
    assert get_current_retirement_savings_contributions(latest_budget=budget) == 20


def test_config_calculates_month_horizons():
    config = RetirementForecastConfig(
        current_age=30, retirement_age=60, life_expectancy=85
    )

    assert config.horizon_months == 660
    assert config.accumulation_months == 360


def test_block_bootstrap_is_seeded_and_keeps_return_inflation_pairs():
    returns = np.array([0.1, 0.2, 0.3, 0.4])
    inflation = returns + 1

    sampled_returns, sampled_inflation = block_bootstrap_paths(
        returns,
        inflation,
        horizon_months=5,
        block_length=2,
        n_simulations=3,
        rng=np.random.default_rng(17),
    )
    repeated_returns, repeated_inflation = block_bootstrap_paths(
        returns,
        inflation,
        horizon_months=5,
        block_length=2,
        n_simulations=3,
        rng=np.random.default_rng(17),
    )

    assert sampled_returns.shape == (3, 5)
    np.testing.assert_array_equal(sampled_returns, repeated_returns)
    np.testing.assert_array_equal(sampled_inflation, repeated_inflation)
    np.testing.assert_array_equal(sampled_inflation, sampled_returns + 1)


def test_block_bootstrap_rejects_block_larger_than_history():
    with pytest.raises(ValueError, match="can't exceed the data length"):
        block_bootstrap_paths(
            np.ones(2), np.zeros(2), 4, 3, 1, np.random.default_rng(0)
        )


def test_fixed_contributions_stop_at_accumulation_horizon():
    schedule = build_contribution_schedule("fixed", 50, 2, 4, np.zeros((2, 4)))

    np.testing.assert_array_equal(schedule, [[50, 50, 0, 0], [50, 50, 0, 0]])


def test_inflation_ramped_contributions_compound_inflation():
    inflation = np.array([[0.1, 0.2, 0.3]])

    schedule = build_contribution_schedule("inflation_ramp", 100, 3, 3, inflation)

    np.testing.assert_allclose(schedule, [[110, 132, 171.6]])


def test_unknown_contribution_mode_is_rejected():
    with pytest.raises(ValueError, match="Unknown contribution mode"):
        build_contribution_schedule("variable", 100, 2, 2, np.zeros((1, 2)))


def test_simulate_wealth_tracks_nominal_and_real_values():
    nominal, real = simulate_wealth(
        100,
        np.array([[0.1, 0.0]]),
        np.array([[0.0, 0.1]]),
        np.array([[10.0, 0.0]]),
    )

    np.testing.assert_allclose(nominal, [[100, 120, 120]])
    np.testing.assert_allclose(real, [[100, 120, 120 / 1.1]])


def test_summary_and_target_probability():
    summary = summarize_terminal_wealth(np.array([0.0, 10.0, 20.0]))

    assert summary.loc[50, "real_terminal_wealth"] == "$10"
    assert probability_of_reaching_target(np.array([0, 10, 20]), 10) == pytest.approx(
        2 / 3
    )
