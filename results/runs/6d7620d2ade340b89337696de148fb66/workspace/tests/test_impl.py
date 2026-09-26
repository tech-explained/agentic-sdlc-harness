import pytest

from impl import authorize_payment


def test_authorized():
    out = authorize_payment({"payment_id": "p-1", "amount": 9.99, "currency": "USD"})
    assert out["authorized"] is True


def test_declined_currency():
    out = authorize_payment({"payment_id": "p-2", "amount": 9.99, "currency": "JPY"})
    assert out["authorized"] is False


def test_declined_amount():
    out = authorize_payment({"payment_id": "p-3", "amount": 0, "currency": "EUR"})
    assert out["authorized"] is False
