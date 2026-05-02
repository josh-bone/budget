import os
from pathlib import Path
from typing import Any

import tomllib

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


def load_toml() -> dict[str, Any]:
    config_path_str = os.environ.get("BUDGET_CONFIG")
    if config_path_str is None:
        raise ConfigError("Error: BUDGET_CONFIG environment variable is not set.")
    config_path = Path(config_path_str)
    if not config_path.exists():
        raise FileNotFoundError(f"Error: config file not found at {config_path}")
    with open(config_path, "rb") as f:
        return tomllib.load(f)
