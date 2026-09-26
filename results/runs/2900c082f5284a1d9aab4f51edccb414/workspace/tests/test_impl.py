import pytest

from impl import ParseError, parse_int


def test_ok():
    assert parse_int("42") == 42
    assert parse_int("  7 ") == 7


def test_bad():
    with pytest.raises(ParseError) as exc:
        parse_int("abc")
    assert "abc" in str(exc.value)
