import pytest

from impl import shipment_event


def test_shape():
    out = shipment_event("o-9", "fast-freight")
    assert out["event"] == "shipment.created"
    assert out["status"] == "pending"
    assert isinstance(out["attempt"], int)


def test_requires_fields():
    with pytest.raises(ValueError):
        shipment_event("", "fast-freight")
