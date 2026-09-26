"""Bounded retry helper."""

def retry_call(fn, attempts):
    """Call fn(); on Exception retry up to attempts times.

    Returns (result, attempts_used). Raises the last error when exhausted.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last = None
    for used in range(1, attempts + 1):
        try:
            return fn(), used
        except Exception as exc:
            last = exc
    raise last
