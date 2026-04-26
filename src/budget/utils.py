def cast_to_float(value: str | None) -> float | None:
    if not isinstance(value, str):
        raise ValueError(f"Expected a string or None, got {type(value).__name__}.")
    if value is None:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        raise ValueError(f"Cannot convert '{value}' to float.")
