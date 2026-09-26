"""Consumer: summarize an order.created event."""


def summarize_order(event):
    try:
        order_id = event["order_id"]
        currency = event["currency"]
        lines = event["lines"]
    except KeyError as exc:
        raise ValueError(f"missing field: {exc}")
    total = round(sum(line["qty"] * line["price"] for line in lines), 2)
    return {
        "order_id": order_id,
        "currency": currency,
        "total": total,
        "line_count": len(lines),
    }
