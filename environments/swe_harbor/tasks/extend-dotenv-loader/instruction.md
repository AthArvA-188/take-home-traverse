# Extend the dotenv loader

You are working on a small `.env` file parser written in Python. The project has three modules:

- **`parser.py`** — reads `.env` file content and extracts key-value pairs. Handles single-quoted, double-quoted, and unquoted values with escape sequence processing.
- **`variables.py`** — handles `${VAR}` and `${VAR:-default}` variable interpolation in values.
- **`main.py`** — combines parsing and variable resolution into a single `parse_dotenv()` function.

The parser is working correctly. Your job is to **extend** it by creating two new modules and adding one function to `main.py`.

## What to implement

### 1. `/app/loader.py` (new file)

Create a `load_dotenv()` function that loads and merges multiple `.env` files.

```python
def load_dotenv(*paths, strict=False) -> dict:
```

- Accepts zero or more file paths as positional arguments.
- Reads each file and parses it using `parse_dotenv()` from `main.py`.
- Merges results in order — if the same key appears in multiple files, the **later** file wins.
- **Missing files:** if a file does not exist and `strict=False` (the default), silently skip it. If `strict=True`, raise `FileNotFoundError`.
- If no paths are given, return an empty dict.

### 2. `/app/validator.py` (new file)

Create a validation system for parsed configuration.

**`ValidationError` class:**
- Has `.key` and `.message` attributes (set via `__init__(self, key, message)`)
- `__str__()` returns `"key: message"`

**`validate(config, schema)` function:**
```python
def validate(config: dict, schema: dict) -> list[ValidationError]:
```

- `config` is a dict of string keys to string values (as returned by `load_dotenv`).
- `schema` is a dict where each key maps to a dict of rules. Example:
  ```python
  schema = {
      "PORT": {"required": True, "type": "int"},
      "DEBUG": {"required": False, "type": "bool"},
      "MODE": {"required": True, "type": "str", "choices": ["dev", "prod"]},
  }
  ```
- **Schema rules:**
  - `required` (bool): if `True` and the key is missing from `config`, add a `ValidationError` with message `"required"`.
  - `type` (str): one of `"str"`, `"int"`, `"bool"`. Validates that the value can be interpreted as the given type:
    - `"str"` — always valid (all values are strings).
    - `"int"` — valid if the value is a string of digits, optionally with a leading `-` (e.g., `"42"`, `"-1"`). Use `int(value)` to test.
    - `"bool"` — valid if the value (case-insensitive) is one of: `true`, `false`, `1`, `0`, `yes`, `no`.
    - If the value doesn't match the expected type, add a `ValidationError` with message `"must be type <type>"` (e.g., `"must be type int"`).
  - `choices` (list of str): if present, the value must be one of the listed choices. If not, add a `ValidationError` with message `"must be one of: x, y, z"` (choices joined by `", "`).
- **Validation order** for each key: check `required` first, then `type`, then `choices`. If a key is missing and not required, skip `type` and `choices` checks for that key.
- **Extra keys** in `config` that are not in the schema should be silently ignored.
- Return a list of all `ValidationError` objects found (may be empty if everything is valid).

### 3. `/app/main.py` (modify — append a function)

Add a `load_and_validate()` function to the existing `main.py`:

```python
def load_and_validate(paths, schema, strict=False) -> tuple[dict, list]:
```

- `paths` is a list of file paths.
- Calls `load_dotenv(*paths, strict=strict)` to load configuration.
- Calls `validate(config, schema)` to validate it.
- Returns `(config, errors)` where `config` is the loaded dict and `errors` is the list of `ValidationError` objects.

## Important notes

- Do **not** modify `parser.py` or `variables.py` — they are correct.
- The `load_dotenv` function must use `parse_dotenv` from `main.py` (not `parse` from `parser.py` directly) so that variable interpolation works.
- Be careful with circular imports: `loader.py` imports from `main.py`, so `main.py` should use **local imports** (inside the function body) when importing from `loader` or `validator`.
- All new code should be in `/app/loader.py`, `/app/validator.py`, and appended to `/app/main.py`.
