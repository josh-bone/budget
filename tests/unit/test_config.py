import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from budget.config import ConfigError, get_env, load_toml


# ── get_env ────────────────────────────────────────────────────────────────────


def test_get_env_returns_value_when_set():
    with patch.dict(os.environ, {"MY_KEY": "my_value"}):
        assert get_env("MY_KEY") == "my_value"


def test_get_env_strips_whitespace():
    with patch.dict(os.environ, {"MY_KEY": "  spaced  "}):
        assert get_env("MY_KEY") == "spaced"


def test_get_env_raises_when_missing():
    env = {k: v for k, v in os.environ.items() if k != "MISSING_KEY"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigError, match="MISSING_KEY"):
            get_env("MISSING_KEY")


def test_get_env_raises_when_empty_string():
    with patch.dict(os.environ, {"MY_KEY": ""}):
        with pytest.raises(ConfigError, match="MY_KEY"):
            get_env("MY_KEY")


def test_get_env_raises_when_whitespace_only():
    with patch.dict(os.environ, {"MY_KEY": "   "}):
        with pytest.raises(ConfigError, match="MY_KEY"):
            get_env("MY_KEY")


# ── load_toml ──────────────────────────────────────────────────────────────────

VALID_TOML = textwrap.dedent("""\
    [spreadsheet]
    sheet_pattern = "^\\\\d{1,2}/\\\\d{4}$"

    [cells.income]
    net_income = "F5"

    [cells.expenses]
    home = "O16"
""")


def test_load_toml_parses_valid_file(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(VALID_TOML)
    with patch.dict(os.environ, {"BUDGET_CONFIG": str(config_file)}):
        result = load_toml()
    assert result["spreadsheet"]["sheet_pattern"] == "^\\d{1,2}/\\d{4}$"
    assert result["cells"]["income"]["net_income"] == "F5"
    assert result["cells"]["expenses"]["home"] == "O16"


def test_load_toml_raises_when_env_not_set():
    env = {k: v for k, v in os.environ.items() if k != "BUDGET_CONFIG"}
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigError, match="BUDGET_CONFIG"):
            load_toml()


def test_load_toml_raises_when_file_missing(tmp_path):
    missing = tmp_path / "nonexistent.toml"
    with patch.dict(os.environ, {"BUDGET_CONFIG": str(missing)}):
        with pytest.raises(FileNotFoundError, match="nonexistent.toml"):
            load_toml()


def test_load_toml_raises_on_invalid_toml(tmp_path):
    bad_file = tmp_path / "bad.toml"
    bad_file.write_text("this is not [ valid toml !!!@#")
    with patch.dict(os.environ, {"BUDGET_CONFIG": str(bad_file)}):
        with pytest.raises(Exception):  # tomllib.TOMLDecodeError
            load_toml()
