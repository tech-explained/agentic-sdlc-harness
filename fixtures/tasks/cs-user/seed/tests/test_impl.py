import pytest

from impl import normalize_user


def test_normalize():
    out = normalize_user({"user_id": 7, "email": " Ada@Example.COM "})
    assert out == {"user_id": "7", "email": "ada@example.com", "signup_date": None}


def test_missing():
    with pytest.raises(ValueError):
        normalize_user({"user_id": "u-1"})
