#!/bin/bash

# Create loader.py — loads and merges multiple .env files
cat > /app/loader.py << 'PYEOF'
"""
Dotenv loader — loads and merges multiple .env files.
"""

from main import parse_dotenv


def load_dotenv(*paths, strict=False):
    """Load one or more .env files and return a merged dict.

    Later files override earlier ones. Missing files are skipped unless
    *strict* is True, in which case FileNotFoundError is raised.
    """
    merged = {}
    for path in paths:
        try:
            with open(path, "r") as f:
                content = f.read()
        except FileNotFoundError:
            if strict:
                raise
            continue
        merged.update(parse_dotenv(content))
    return merged
PYEOF

# Create validator.py — validates config dicts against a schema
cat > /app/validator.py << 'PYEOF'
"""
Configuration validator for dotenv-loaded config dicts.
"""

_BOOL_VALUES = {"true", "false", "1", "0", "yes", "no"}


class ValidationError:
    """A single validation error tied to a config key."""

    def __init__(self, key, message):
        self.key = key
        self.message = message

    def __str__(self):
        return f"{self.key}: {self.message}"


def _check_type(value, expected):
    """Return True if *value* satisfies the type constraint."""
    if expected == "str":
        return True
    if expected == "int":
        try:
            int(value)
            return True
        except (ValueError, TypeError):
            return False
    if expected == "bool":
        return value.lower() in _BOOL_VALUES
    return True


def validate(config, schema):
    """Validate *config* against *schema* and return a list of ValidationError."""
    errors = []
    for key, rules in schema.items():
        value = config.get(key)

        # required check
        if value is None:
            if rules.get("required", False):
                errors.append(ValidationError(key, "required"))
            continue

        # type check
        expected_type = rules.get("type")
        if expected_type and not _check_type(value, expected_type):
            errors.append(ValidationError(key, f"must be type {expected_type}"))
            continue

        # choices check
        choices = rules.get("choices")
        if choices is not None and value not in choices:
            errors.append(ValidationError(key, f"must be one of: {', '.join(choices)}"))

    return errors
PYEOF

# Patch main.py — append load_and_validate function
cat >> /app/main.py << 'PYEOF'


def load_and_validate(paths, schema, strict=False):
    """Load .env files and validate the result against a schema.

    Returns (config, errors) where *config* is the merged dict and
    *errors* is a list of ValidationError objects.
    """
    from loader import load_dotenv
    from validator import validate

    config = load_dotenv(*paths, strict=strict)
    errors = validate(config, schema)
    return config, errors
PYEOF
