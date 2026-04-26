import os
from pathlib import Path
from typing import Any

import tomllib

CONFIG_PATH = Path(os.environ.get("BUDGET_CONFIG"))
DEFAULT_LOCALE = "en_US.UTF-8"


class ConfigError(Exception):
    pass


def get_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise ConfigError(
            f"Error: environment variable '{key}' is not set.\nDid you forget to run `source .env`?"
        )
    return value


def get_locale() -> str:
    locale = os.environ.get("LOCALE", default=DEFAULT_LOCALE).strip()
    if not locale:
        raise ConfigError(
            "Error: LOCALE environment variable is not set.\nSet it to something like 'en_US.UTF-8'."
        )
    return locale


def load_toml() -> dict[str, Any]:
    if CONFIG_PATH is None:
        raise ConfigError("Error: BUDGET_CONFIG environment variable is not set.")
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Error: config file not found at {CONFIG_PATH}")
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)
