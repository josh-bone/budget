def cast_to_float(value: str | None) -> float | None:
    if not isinstance(value, str):
        raise ValueError(f"Expected a string or None, got {type(value).__name__}.")
    if value is None:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        raise ValueError(f"Cannot convert '{value}' to float.")


def month_sheet_sort_key(sheet_name: str) -> tuple[int, int]:
    """Sort 'M/YYYY' tab names chronologically."""
    try:
        parts = sheet_name.split("/")
        return (int(parts[1]), int(parts[0]))
    except (IndexError, ValueError):
        return (0, 0)
