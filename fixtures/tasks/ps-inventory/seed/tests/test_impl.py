import pytest

from impl import inventory_event


def test_shape():
    out = inventory_event("sku-1", 4)
    assert out == {
        "event": "inventory.updated",
        "sku": "sku-1",
        "quantity": 4,
        "version": 1,
    }


def test_requires_sku():
    with pytest.raises(ValueError):
        inventory_event("", 4)
