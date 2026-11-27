def parse_money(value: object) -> float | None:
    """Parse a money value, accepting common currency and accounting notation."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"Expected a money string or number, got {type(value).__name__}.")
    text = value.strip()
    if not text:
        return None
    text = text.replace("$", "").replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"Cannot parse money value {value!r}.") from None
