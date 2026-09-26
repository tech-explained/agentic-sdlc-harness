"""Provider: emit shipment.created events."""


def shipment_event(order_id, carrier):
    if not order_id or not carrier:
        raise ValueError("order_id and carrier required")
    return {
        "event": "shipment.created",
        "order_id": order_id,
        "carrier": carrier,
        "status": "pending",
        "attempt": 1,
    }
