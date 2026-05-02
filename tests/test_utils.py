import pytest
from budget.utils import cast_to_float


# ── Happy path ─────────────────────────────────────────────────────────────────

def test_plain_integer_string():
    assert cast_to_float("1000") == 1000.0


def test_plain_float_string():
    assert cast_to_float("1234.56") == pytest.approx(1234.56)


def test_comma_formatted_number():
    assert cast_to_float("1,234.56") == pytest.approx(1234.56)


def test_large_comma_formatted_number():
    assert cast_to_float("1,000,000.00") == pytest.approx(1_000_000.0)


def test_zero():
    assert cast_to_float("0") == 0.0


def test_zero_point_zero():
    assert cast_to_float("0.00") == 0.0


def test_negative_number():
    assert cast_to_float("-500.00") == pytest.approx(-500.0)


def test_negative_comma_number():
    assert cast_to_float("-1,500.00") == pytest.approx(-1500.0)


# ── Error cases ────────────────────────────────────────────────────────────────

def test_non_string_int_raises():
    with pytest.raises(ValueError, match="Expected a string or None"):
        cast_to_float(1234)  # type: ignore


def test_non_string_float_raises():
    with pytest.raises(ValueError, match="Expected a string or None"):
        cast_to_float(12.34)  # type: ignore


def test_non_string_none_raises():
    # None is not a str — cast_to_float checks isinstance first
    with pytest.raises(ValueError, match="Expected a string or None"):
        cast_to_float(None)  # type: ignore


def test_empty_string_raises():
    with pytest.raises(ValueError, match="Cannot convert"):
        cast_to_float("")


def test_plain_text_raises():
    with pytest.raises(ValueError, match="Cannot convert"):
        cast_to_float("hello")


def test_currency_symbol_raises():
    # Sheets should return raw numbers; dollar signs are not stripped
    with pytest.raises(ValueError, match="Cannot convert"):
        cast_to_float("$1,234.56")


def test_whitespace_only_raises():
    with pytest.raises(ValueError, match="Cannot convert"):
        cast_to_float("   ")
