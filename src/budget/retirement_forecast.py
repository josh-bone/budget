"""
retirement_forecast.py — Retirement Monte Carlo helpers.

This module lifts the notebook prototype into reusable project code. It keeps
the same bootstrap/simulation flow as the notebook while adding small data
hooks for SQLite-backed balances and legacy Sheets budget values.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from budget.balances_db import get_balance_history
from budget.utils import parse_money

logger = logging.getLogger(__name__)

DEFAULT_CSV_PATH = "data/input/sample_returns_inflation.csv"
DEFAULT_CURRENT_AGE = 27
DEFAULT_RETIREMENT_AGE = 65
DEFAULT_LIFE_EXPECTANCY = 90
DEFAULT_BLOCK_LENGTH_MONTHS = 24
DEFAULT_N_SIMULATIONS = 10_000
DEFAULT_RANDOM_SEED = 42
DEFAULT_TARGET_REAL_BALANCE = 2_000_000.0
DEFAULT_CONTRIBUTION_MODE = "fixed"

# The notebook currently falls back to these hard-coded values when project
# data is unavailable. They remain as a safety net, not the primary source.
DEFAULT_INITIAL_BALANCE = 135_838.28
DEFAULT_MONTHLY_CONTRIBUTION = 4_306.58

DEFAULT_BALANCE_PATTERNS = ("hsa", "401k", "roth", "savings")
DEFAULT_CONTRIBUTION_PATTERNS = ("hsa", "401k", "roth")


@dataclass(frozen=True)
class RetirementForecastConfig:
    csv_path: str = DEFAULT_CSV_PATH
    current_age: int = DEFAULT_CURRENT_AGE
    retirement_age: int = DEFAULT_RETIREMENT_AGE
    life_expectancy: int = DEFAULT_LIFE_EXPECTANCY
    block_length_months: int = DEFAULT_BLOCK_LENGTH_MONTHS
    n_simulations: int = DEFAULT_N_SIMULATIONS
    random_seed: int = DEFAULT_RANDOM_SEED
    target_real_balance: float = DEFAULT_TARGET_REAL_BALANCE
    contribution_mode: str = DEFAULT_CONTRIBUTION_MODE

    @property
    def horizon_months(self) -> int:
        return (self.life_expectancy - self.current_age) * 12

    @property
    def accumulation_months(self) -> int:
        return (self.retirement_age - self.current_age) * 12


@dataclass(frozen=True)
class RetirementForecastResult:
    history: pd.DataFrame
    returns_paths: np.ndarray
    inflation_paths: np.ndarray
    contributions: np.ndarray
    nominal_wealth: np.ndarray
    real_wealth: np.ndarray
    terminal_real_wealth: np.ndarray
    summary: pd.DataFrame
    probability_of_reaching_target: float
    config: RetirementForecastConfig
    initial_balance: float
    monthly_contribution: float


def load_historical_data(csv_path: str) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Historical returns file not found: {path}")

    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)

    required_cols = {"date", "nominal_return", "inflation"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(f"CSV is missing required column(s): {sorted(missing_cols)}")

    n_before = len(df)
    df = df.dropna(subset=["nominal_return", "inflation"]).reset_index(drop=True)
    if len(df) < n_before:
        logger.warning("Dropped %d row(s) with missing values.", n_before - len(df))

    if df["date"].duplicated().any():
        raise ValueError(
            "Duplicate dates found in input CSV; expected one row per month."
        )

    expected = pd.date_range(df["date"].min(), df["date"].max(), freq="MS")
    missing_months = expected.difference(df["date"])
    if len(missing_months) > 0:
        logger.warning(
            "Historical data has %d missing month(s), e.g. %s",
            len(missing_months),
            [str(m) for m in missing_months[:3]],
        )

    return df


def block_bootstrap_paths(
    returns: np.ndarray,
    inflation: np.ndarray,
    horizon_months: int,
    block_length: int,
    n_simulations: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (returns_paths, inflation_paths), each shape (n_simulations, horizon_months)."""
    n = len(returns)
    if block_length > n:
        raise ValueError(
            f"block_length ({block_length}) can't exceed the data length ({n})"
        )

    max_start = n - block_length + 1
    n_blocks_needed = -(-horizon_months // block_length)

    starts = rng.integers(0, max_start, size=(n_simulations, n_blocks_needed))
    offsets = np.arange(block_length)
    idx = (starts[:, :, None] + offsets[None, None, :]).reshape(n_simulations, -1)
    idx = idx[:, :horizon_months]

    return returns[idx], inflation[idx]


def build_contribution_schedule(
    mode: str,
    monthly_amount: float,
    accumulation_months: int,
    horizon_months: int,
    inflation_paths: np.ndarray,
) -> np.ndarray:
    """Return a contributions array with shape (n_simulations, horizon_months)."""
    n_simulations = inflation_paths.shape[0]
    contributions = np.zeros((n_simulations, horizon_months))

    active = min(accumulation_months, horizon_months)
    if active <= 0:
        return contributions

    if mode == "fixed":
        contributions[:, :active] = monthly_amount
    elif mode == "inflation_ramp":
        cum_inflation = np.cumprod(1 + inflation_paths[:, :active], axis=1)
        contributions[:, :active] = monthly_amount * cum_inflation
    else:
        raise ValueError(
            f"Unknown contribution mode: {mode!r} (expected 'fixed' or 'inflation_ramp')"
        )

    return contributions


def simulate_wealth(
    initial_balance: float,
    returns_paths: np.ndarray,
    inflation_paths: np.ndarray,
    contributions: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (nominal_wealth, real_wealth), each including the starting balance at month 0."""
    n_simulations, horizon_months = returns_paths.shape

    nominal_wealth = np.empty((n_simulations, horizon_months + 1))
    nominal_wealth[:, 0] = initial_balance
    for s in range(horizon_months):
        nominal_wealth[:, s + 1] = (
            nominal_wealth[:, s] * (1 + returns_paths[:, s]) + contributions[:, s]
        )

    cum_inflation = np.empty((n_simulations, horizon_months + 1))
    cum_inflation[:, 0] = 1.0
    cum_inflation[:, 1:] = np.cumprod(1 + inflation_paths, axis=1)

    real_wealth = nominal_wealth / cum_inflation
    return nominal_wealth, real_wealth


def summarize_terminal_wealth(
    terminal_real_wealth: np.ndarray,
    percentiles: Sequence[int] = (5, 10, 25, 50, 75, 90, 95),
) -> pd.DataFrame:
    values = np.percentile(terminal_real_wealth, percentiles)
    summary = pd.DataFrame(
        {
            "percentile": list(percentiles),
            "real_terminal_wealth": values,
        }
    ).set_index("percentile")
    summary["real_terminal_wealth"] = summary["real_terminal_wealth"].map(
        lambda x: f"${x:,.0f}"
    )
    return summary


def probability_of_reaching_target(
    terminal_real_wealth: np.ndarray,
    target_real_balance: float,
) -> float:
    return float(np.mean(terminal_real_wealth >= target_real_balance))


def run_retirement_forecast(
    *,
    config: RetirementForecastConfig | None = None,
    csv_path: str | None = None,
    initial_balance: float | None = None,
    monthly_contribution: float | None = None,
    latest_budget: Mapping[str, Any] | None = None,
    balances_df: pd.DataFrame | None = None,
    account_patterns: Sequence[str] | None = None,
    contribution_patterns: Sequence[str] | None = None,
    contribution_fields: Sequence[str] | None = None,
    summary_keys: Sequence[str] | None = None,
) -> RetirementForecastResult:
    config = config or RetirementForecastConfig()
    if csv_path is not None:
        config = replace(config, csv_path=csv_path)

    history = load_historical_data(config.csv_path)
    rng = np.random.default_rng(config.random_seed)

    initial_balance = (
        initial_balance
        if initial_balance is not None
        else get_total_retirement_savings_balance(
            balances_df=balances_df,
            account_patterns=account_patterns,
        )
    )
    monthly_contribution = (
        monthly_contribution
        if monthly_contribution is not None
        else get_current_retirement_savings_contributions(
            latest_budget=latest_budget,
            contribution_patterns=contribution_patterns,
            contribution_fields=contribution_fields,
            summary_keys=summary_keys,
        )
    )

    returns_paths, inflation_paths = block_bootstrap_paths(
        returns=history["nominal_return"].to_numpy(),
        inflation=history["inflation"].to_numpy(),
        horizon_months=config.horizon_months,
        block_length=config.block_length_months,
        n_simulations=config.n_simulations,
        rng=rng,
    )

    contributions = build_contribution_schedule(
        mode=config.contribution_mode,
        monthly_amount=monthly_contribution,
        accumulation_months=config.accumulation_months,
        horizon_months=config.horizon_months,
        inflation_paths=inflation_paths,
    )

    nominal_wealth, real_wealth = simulate_wealth(
        initial_balance=initial_balance,
        returns_paths=returns_paths,
        inflation_paths=inflation_paths,
        contributions=contributions,
    )

    terminal_idx = min(config.accumulation_months, real_wealth.shape[1] - 1)
    terminal_real_wealth = real_wealth[:, terminal_idx]
    summary = summarize_terminal_wealth(terminal_real_wealth)
    probability = probability_of_reaching_target(
        terminal_real_wealth, config.target_real_balance
    )

    return RetirementForecastResult(
        history=history,
        returns_paths=returns_paths,
        inflation_paths=inflation_paths,
        contributions=contributions,
        nominal_wealth=nominal_wealth,
        real_wealth=real_wealth,
        terminal_real_wealth=terminal_real_wealth,
        summary=summary,
        probability_of_reaching_target=probability,
        config=config,
        initial_balance=initial_balance,
        monthly_contribution=monthly_contribution,
    )


def _contains_any(text: str, needles: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles if needle)


def _find_account_column(df: pd.DataFrame) -> str | None:
    for column in df.columns:
        lowered = str(column).lower()
        if (
            lowered in {"account", "account name", "name", "label"}
            or "account" in lowered
        ):
            return column
    return None


def _find_amount_column(df: pd.DataFrame) -> str | None:
    for column in df.columns:
        lowered = str(column).lower()
        if lowered in {"amount", "balance", "value"} or "amount" in lowered:
            return column
    return None


def _coerce_amount_series(series: pd.Series) -> pd.Series:
    def coerce(value: Any) -> float:
        try:
            parsed = parse_money(value)
            return float(parsed) if parsed is not None else float("nan")
        except ValueError:
            return float("nan")

    return series.map(coerce).astype(float)


def get_total_retirement_savings_balance(
    *,
    balances_df: pd.DataFrame | None = None,
    account_patterns: Sequence[str] | None = None,
    db_path: str | None = None,
) -> float:
    """
    Resolve the current retirement balance from the latest SQLite snapshot.

    ``balances_df`` is accepted as an explicit data override. If the database
    has no snapshots, the notebook's current hard-coded value is used.
    """
    account_patterns = tuple(account_patterns or DEFAULT_BALANCE_PATTERNS)

    if balances_df is None:
        logger.debug("Fetching balances from SQLite")
        balances_df = get_balance_history(db_path=db_path)

    if balances_df is not None and not balances_df.empty and "Date" in balances_df:
        latest_date = balances_df["Date"].max()
        balances_df = balances_df[balances_df["Date"] == latest_date]

    if balances_df is None or balances_df.empty:
        logger.debug(f"Returning default initial balance of {DEFAULT_INITIAL_BALANCE}")
        return DEFAULT_INITIAL_BALANCE

    amount_column = _find_amount_column(balances_df)
    if amount_column is None:
        logger.warning("SQLite balance history did not contain an amount-like column.")
        logger.info(f"Returning default initial balance of {DEFAULT_INITIAL_BALANCE}")
        return DEFAULT_INITIAL_BALANCE

    amounts = _coerce_amount_series(balances_df[amount_column]).fillna(0.0)
    account_column = _find_account_column(balances_df)
    if account_column is None:
        logger.warning("SQLite balance history did not contain an account-like column.")
        logger.info(f"Returning default initial balance of {DEFAULT_INITIAL_BALANCE}")
        return DEFAULT_INITIAL_BALANCE

    accounts = balances_df[account_column].fillna("").astype(str)
    mask = accounts.apply(lambda value: _contains_any(value, account_patterns))
    matched = float(amounts[mask].sum())
    if mask.any():
        return matched

    logger.warning(
        "No balance rows matched account patterns %s; using the full balance total.",
        account_patterns,
    )
    result = float(amounts.sum())
    return result


def _iter_budget_rows(latest_budget: Mapping[str, Any]):
    for section_name, rows in latest_budget.items():
        if section_name == "summary":
            continue
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, Mapping):
                    yield section_name, row


def get_current_retirement_savings_contributions(
    *,
    latest_budget: Mapping[str, Any] | None = None,
    contribution_patterns: Sequence[str] | None = None,
    contribution_fields: Sequence[str] | None = None,
    summary_keys: Sequence[str] | None = None,
) -> float:
    """
    Resolve monthly retirement contributions from the latest budget.

    The helper looks for a dedicated summary field first, then scans budget
    section rows for matching labels. If nothing can be resolved, it falls back
    to the notebook's current hard-coded value.
    """
    contribution_patterns = tuple(
        contribution_patterns or DEFAULT_CONTRIBUTION_PATTERNS
    )
    contribution_fields = tuple(contribution_fields or ())
    summary_keys = tuple(
        summary_keys
        or (
            "retirement_contributions",
            "monthly_retirement_contributions",
            "monthly_contributions",
            "contributions",
        )
    )

    if latest_budget:
        summary = latest_budget.get("summary", {})
        if isinstance(summary, Mapping):
            for key in summary_keys:
                value = summary.get(key)
                if value is not None:
                    return float(value)

        total = 0.0
        matched = False
        for _, row in _iter_budget_rows(latest_budget):
            label = str(row.get("label", ""))
            value = row.get("value")
            if value is None:
                continue

            exact_match = contribution_fields and label in contribution_fields
            pattern_match = contribution_patterns and _contains_any(
                label, contribution_patterns
            )
            if exact_match or pattern_match:
                total += float(value)
                matched = True

        if matched:
            return total

    return DEFAULT_MONTHLY_CONTRIBUTION
