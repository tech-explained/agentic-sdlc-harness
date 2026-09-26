"""Consumer: authorize a payment.requested event."""

SUPPORTED = ("USD", "EUR")


def authorize_payment(event):
    try:
        payment_id = event["payment_id"]
        amount = event["amount"]
        currency = event["currency"]
    except KeyError as exc:
        raise ValueError(f"missing field: {exc}")
    authorized = amount > 0 and currency in SUPPORTED
    reason = "ok" if authorized else "amount must be positive and currency supported"
    return {"payment_id": payment_id, "authorized": authorized, "reason": reason}
