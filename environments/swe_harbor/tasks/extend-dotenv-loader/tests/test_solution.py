"""
Tests for the extend-dotenv-loader task.

Tests loader.py, validator.py, and the load_and_validate integration in main.py.
"""

import os
import sys
import tempfile
import pytest

sys.path.insert(0, "/app")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_env(tmp_path, name, content):
    """Write a .env file into *tmp_path* and return its path."""
    p = os.path.join(tmp_path, name)
    with open(p, "w") as f:
        f.write(content)
    return p


# ===========================================================================
# Loader tests (8)
# ===========================================================================

class TestLoader:

    def test_single_file(self, tmp_path):
        from loader import load_dotenv
        p = _write_env(str(tmp_path), ".env", "HOST=localhost\nPORT=8080\n")
        result = load_dotenv(p)
        assert result == {"HOST": "localhost", "PORT": "8080"}

    def test_merge_order(self, tmp_path):
        """Later file overrides earlier."""
        from loader import load_dotenv
        p1 = _write_env(str(tmp_path), "a.env", "A=1\nB=2\n")
        p2 = _write_env(str(tmp_path), "b.env", "B=99\nC=3\n")
        result = load_dotenv(p1, p2)
        assert result == {"A": "1", "B": "99", "C": "3"}

    def test_missing_file_skip(self, tmp_path):
        """Missing file is silently skipped when strict=False."""
        from loader import load_dotenv
        p = _write_env(str(tmp_path), ".env", "X=1\n")
        result = load_dotenv(p, "/nonexistent/.env")
        assert result == {"X": "1"}

    def test_missing_file_strict_raises(self):
        """Missing file raises FileNotFoundError when strict=True."""
        from loader import load_dotenv
        with pytest.raises(FileNotFoundError):
            load_dotenv("/nonexistent/.env", strict=True)

    def test_no_paths(self):
        """No paths returns empty dict."""
        from loader import load_dotenv
        assert load_dotenv() == {}

    def test_empty_file(self, tmp_path):
        """An empty .env file produces an empty dict."""
        from loader import load_dotenv
        p = _write_env(str(tmp_path), ".env", "")
        assert load_dotenv(p) == {}

    def test_variable_interpolation(self, tmp_path):
        """Variable interpolation works through load_dotenv."""
        from loader import load_dotenv
        p = _write_env(str(tmp_path), ".env", "BASE=/opt\nPATH_DIR=${BASE}/bin\n")
        result = load_dotenv(p)
        assert result == {"BASE": "/opt", "PATH_DIR": "/opt/bin"}

    def test_merge_three_files(self, tmp_path):
        """Three files merge in order."""
        from loader import load_dotenv
        p1 = _write_env(str(tmp_path), "a.env", "K=a\n")
        p2 = _write_env(str(tmp_path), "b.env", "K=b\n")
        p3 = _write_env(str(tmp_path), "c.env", "K=c\n")
        assert load_dotenv(p1, p2, p3)["K"] == "c"


# ===========================================================================
# Validator tests (14)
# ===========================================================================

class TestValidator:

    def test_required_missing(self):
        from validator import validate
        errors = validate({}, {"HOST": {"required": True}})
        assert len(errors) == 1
        assert errors[0].key == "HOST"
        assert errors[0].message == "required"

    def test_required_present(self):
        from validator import validate
        errors = validate({"HOST": "localhost"}, {"HOST": {"required": True}})
        assert errors == []

    def test_int_valid(self):
        from validator import validate
        errors = validate({"PORT": "8080"}, {"PORT": {"type": "int"}})
        assert errors == []

    def test_int_negative_valid(self):
        from validator import validate
        errors = validate({"OFFSET": "-1"}, {"OFFSET": {"type": "int"}})
        assert errors == []

    def test_int_invalid(self):
        from validator import validate
        errors = validate({"PORT": "abc"}, {"PORT": {"type": "int"}})
        assert len(errors) == 1
        assert errors[0].message == "must be type int"

    def test_bool_valid_true(self):
        from validator import validate
        errors = validate({"DEBUG": "true"}, {"DEBUG": {"type": "bool"}})
        assert errors == []

    def test_bool_valid_yes(self):
        from validator import validate
        errors = validate({"DEBUG": "yes"}, {"DEBUG": {"type": "bool"}})
        assert errors == []

    def test_bool_valid_zero(self):
        from validator import validate
        errors = validate({"DEBUG": "0"}, {"DEBUG": {"type": "bool"}})
        assert errors == []

    def test_bool_case_insensitive(self):
        from validator import validate
        for v in ["True", "TRUE", "False", "FALSE", "Yes", "YES", "No", "NO"]:
            errors = validate({"FLAG": v}, {"FLAG": {"type": "bool"}})
            assert errors == [], f"Expected no errors for bool value {v!r}"

    def test_bool_invalid(self):
        from validator import validate
        errors = validate({"DEBUG": "maybe"}, {"DEBUG": {"type": "bool"}})
        assert len(errors) == 1
        assert errors[0].message == "must be type bool"

    def test_choices_valid(self):
        from validator import validate
        errors = validate(
            {"MODE": "prod"},
            {"MODE": {"choices": ["dev", "staging", "prod"]}},
        )
        assert errors == []

    def test_choices_invalid(self):
        from validator import validate
        errors = validate(
            {"MODE": "testing"},
            {"MODE": {"choices": ["dev", "staging", "prod"]}},
        )
        assert len(errors) == 1
        assert "must be one of" in errors[0].message
        assert "dev" in errors[0].message

    def test_multiple_errors(self):
        from validator import validate
        schema = {
            "HOST": {"required": True},
            "PORT": {"required": True, "type": "int"},
        }
        errors = validate({}, schema)
        assert len(errors) == 2
        keys = {e.key for e in errors}
        assert keys == {"HOST", "PORT"}

    def test_extra_keys_ignored(self):
        from validator import validate
        errors = validate(
            {"HOST": "localhost", "SECRET": "abc123"},
            {"HOST": {"required": True}},
        )
        assert errors == []


# ===========================================================================
# Validator — str() output
# ===========================================================================

class TestValidationErrorStr:

    def test_str_representation(self):
        from validator import ValidationError
        e = ValidationError("PORT", "must be type int")
        assert str(e) == "PORT: must be type int"

    def test_str_required(self):
        from validator import ValidationError
        e = ValidationError("HOST", "required")
        assert str(e) == "HOST: required"


# ===========================================================================
# Integration tests (3)
# ===========================================================================

class TestIntegration:

    def test_load_and_validate_success(self, tmp_path):
        from main import load_and_validate
        p = _write_env(str(tmp_path), ".env", "HOST=localhost\nPORT=8080\nDEBUG=true\n")
        schema = {
            "HOST": {"required": True, "type": "str"},
            "PORT": {"required": True, "type": "int"},
            "DEBUG": {"type": "bool"},
        }
        config, errors = load_and_validate([p], schema)
        assert errors == []
        assert config["HOST"] == "localhost"
        assert config["PORT"] == "8080"

    def test_load_and_validate_with_errors(self, tmp_path):
        from main import load_and_validate
        p = _write_env(str(tmp_path), ".env", "PORT=abc\n")
        schema = {
            "HOST": {"required": True},
            "PORT": {"type": "int"},
        }
        config, errors = load_and_validate([p], schema)
        assert len(errors) == 2
        keys = {e.key for e in errors}
        assert "HOST" in keys
        assert "PORT" in keys

    def test_load_and_validate_strict_raises(self):
        from main import load_and_validate
        with pytest.raises(FileNotFoundError):
            load_and_validate(["/nonexistent/.env"], {}, strict=True)
