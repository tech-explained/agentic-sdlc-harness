import pytest

from impl import summarize_order

EVENT = {
    "order_id": "o-1",
    "currency": "USD",
    "lines": [{"qty": 2, "price": 10.0}, {"qty": 1, "price": 5.5}],
}


def test_summary():
    out = summarize_order(EVENT)
    assert out == {"order_id": "o-1", "currency": "USD", "total": 25.5, "line_count": 2}


def test_missing_field():
    with pytest.raises(ValueError):
        summarize_order({"order_id": "o-1"})
