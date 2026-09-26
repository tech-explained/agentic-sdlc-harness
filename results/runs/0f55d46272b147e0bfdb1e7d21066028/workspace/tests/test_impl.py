import pytest

from impl import retry_call


def test_first_try():
    assert retry_call(lambda: 7, 3) == (7, 1)


def test_flaky():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("boom")
        return "ok"

    assert retry_call(flaky, 3) == ("ok", 2)


def test_exhausted():
    with pytest.raises(RuntimeError):
        retry_call(lambda: 1 / 0 if False else (_ for _ in ()).throw(RuntimeError("x")), 2)


def test_bad_attempts():
    with pytest.raises(ValueError):
        retry_call(lambda: 1, 0)
