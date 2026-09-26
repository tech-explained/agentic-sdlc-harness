"""Consumer: normalize a user.signed_up event."""


def normalize_user(event):
    try:
        user_id = event["user_id"]
        email = event["email"]
    except KeyError as exc:
        raise ValueError(f"missing field: {exc}")
    return {
        "user_id": str(user_id),
        "email": email.strip().lower(),
        "signup_date": event.get("signup_date"),
    }
