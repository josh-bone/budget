import locale

from budget.config import get_locale

locale.setlocale(locale.LC_ALL, get_locale())


def cast_to_float(value: str | None) -> float | None:
    if not isinstance(value, str):
        raise ValueError(f"Expected a string or None, got {type(value).__name__}.")
    if value is None:
        return None
    try:
        return locale.atof(value)
    except ValueError:
        raise ValueError(f"Cannot convert '{value}' to float.")
