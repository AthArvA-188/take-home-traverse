"""
Main dotenv module — combines parsing and variable resolution.
"""

import os

from parser import parse
from variables import parse_variables


def parse_dotenv(content, override=False):
    """Parse a .env-format string and return resolved values as a dict.

    When *override* is True the .env values take precedence over existing
    environment variables during interpolation; otherwise existing env vars win.
    """
    return resolve_variables(parse(content), override=override)


def resolve_variables(pairs, override=False):
    """Resolve ${VAR} and ${VAR:-default} references in parsed pairs."""
    resolved = {}
    for name, value in pairs:
        if value is None:
            resolved[name] = None
            continue

        env = {}
        if override:
            env.update(os.environ)
            env.update(resolved)
        else:
            env.update(resolved)
            env.update(os.environ)

        atoms = parse_variables(value)
        resolved[name] = "".join(atom.resolve(env) for atom in atoms)
    return resolved
