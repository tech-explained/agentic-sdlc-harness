import pytest

from impl import MissingKey, safe_get


def test_ok():
    assert safe_get({"a": {"b": 1}}, "a", "b") == 1


def test_missing():
    with pytest.raises(MissingKey) as exc:
        safe_get({"a": {}}, "a", "b")
    assert "a.b" in str(exc.value)
