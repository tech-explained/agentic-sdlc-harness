"""User record validation returning error strings."""

def validate_user(d):
    errors = []
    name = d.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name must be a non-empty string")
    age = d.get("age")
    if isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 150:
        errors.append("age must be an integer in 0..150")
    return errors
