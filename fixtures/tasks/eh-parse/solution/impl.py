"""Strict integer parsing with a typed error."""


class ParseError(ValueError):
    pass


def parse_int(s):
    try:
        return int(str(s).strip())
    except (ValueError, AttributeError):
        raise ParseError(f"cannot parse int from {s!r}")
