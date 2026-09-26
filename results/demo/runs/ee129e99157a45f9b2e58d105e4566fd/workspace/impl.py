"""Tiered discount pricing."""

def discounted(subtotal):
    """Apply tiered discount: 20% off >= 500, 10% off >= 100."""
    if subtotal >= 500:
        return round(subtotal * 0.8, 2)
    if subtotal >= 100:
        return round(subtotal * 0.9, 2)
    return round(subtotal, 2)
