"""
dotenv parser — reads .env content and extracts key-value pairs.

Handles single-quoted, double-quoted, and unquoted values.
"""

import re

# Escape patterns recognized in each quoting style
_single_quote_escapes = re.compile(r"\\[\\']")
_double_quote_escapes = re.compile(r"\\[\\'\"abfnrtv]")

_escape_map = {
    "\\\\": "\\", "\\'": "'", '\\"': '"',
    "\\n": "\n", "\\t": "\t", "\\r": "\r",
    "\\a": "\a", "\\b": "\b", "\\f": "\f", "\\v": "\v",
}


def decode_escapes(pattern, s):
    """Replace escape sequences matched by *pattern* with their values."""
    return pattern.sub(lambda m: _escape_map.get(m.group(0), m.group(0)), s)


def _parse_value(raw):
    """Parse the value part of a KEY=VALUE line."""
    raw = raw.strip()
    if not raw:
        return ""

    if raw[0] == "'":
        m = re.match(r"^'((?:[^'\\]|\\.)*)'", raw)
        if m:
            return decode_escapes(_single_quote_escapes, m.group(1))
        return raw

    if raw[0] == '"':
        m = re.match(r'^"((?:[^"\\]|\\.)*)"', raw)
        if m:
            return decode_escapes(_double_quote_escapes, m.group(1))
        return raw

    # Unquoted: strip inline comments
    return re.sub(r"\s+#.*", "", raw).rstrip()


def parse(content):
    """Parse .env content into a list of (key, value) tuples."""
    pairs = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        pairs.append((key.strip(), _parse_value(raw)))
    return pairs
