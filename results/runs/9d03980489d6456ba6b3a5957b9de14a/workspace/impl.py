"""Provider: emit inventory.updated events."""


def inventory_event(sku, quantity):
    if not sku:
        raise ValueError("sku required")
    return {
        "event": "inventory.updated",
        "sku": sku,
        "quantity": quantity,
        "version": 1,
    }
