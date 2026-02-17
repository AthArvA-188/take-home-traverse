"""
Variable interpolation for dotenv values.

Supports ${VAR} and ${VAR:-default} syntax.
"""

import re

_var_re = re.compile(
    r"\$\{(?P<name>[^}:]+)(?::-(?P<default>[^}]*))?\}"
)


class Literal:
    """Plain-text segment."""
    def __init__(self, text):
        self.text = text

    def resolve(self, env):
        return self.text


class Variable:
    """A ${NAME} or ${NAME:-default} reference."""
    def __init__(self, name, default=None):
        self.name = name
        self.default = default

    def resolve(self, env):
        fallback = self.default if self.default is not None else ""
        result = env.get(self.name, fallback)
        return result if result is not None else ""


def parse_variables(value):
    """Split *value* into Literal and Variable atoms."""
    atoms = []
    last = 0
    for m in _var_re.finditer(value):
        if m.start() > last:
            atoms.append(Literal(value[last:m.start()]))
        atoms.append(Variable(m.group("name"), m.group("default")))
        last = m.end()
    if last < len(value):
        atoms.append(Literal(value[last:]))
    return atoms
