from impl import validate_user


def test_valid():
    assert validate_user({"name": "Ada", "age": 30}) == []


def test_name():
    errors = validate_user({"name": " ", "age": 30})
    assert any("name" in e for e in errors)


def test_age_range():
    errors = validate_user({"name": "Ada", "age": 200})
    assert any("age" in e for e in errors)


def test_age_type():
    errors = validate_user({"name": "Ada", "age": "30"})
    assert any("age" in e for e in errors)
