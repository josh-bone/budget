import pytest

from budget.utils import parse_money


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1000", 1000.0),
        ("1234.56", 1234.56),
        ("1,234.56", 1234.56),
        ("1,000,000.00", 1_000_000.0),
        ("0", 0.0),
        ("-1,500.00", -1500.0),
        ("$1,234.56", 1234.56),
        ("(1,234.56)", -1234.56),
        (1234, 1234.0),
        (12.34, 12.34),
    ],
)
def test_parse_money(value, expected):
    assert parse_money(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_blank_money_is_none(value):
    assert parse_money(value) is None


@pytest.mark.parametrize("value", ["hello", "$oops", object()])
def test_invalid_money_raises(value):
    with pytest.raises(ValueError, match="parse|Expected"):
        parse_money(value)
