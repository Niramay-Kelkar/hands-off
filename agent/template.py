"""{{param_name}} substitution for step action/locator/checkpoint values."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\{\{(\w+)\}\}")


def substitute(value: str, params: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(f"template references unknown param {name!r}")
        return params[name]

    return _TOKEN.sub(_replace, value)


def templatize(value: str, params: dict[str, str]) -> str:
    """Inverse of substitute() — used by the compiler to turn a discovery
    trajectory's literal values back into {{param_name}} tokens. Only
    exact matches are replaced; no partial/substring substitution."""
    for name, param_value in params.items():
        if value == param_value:
            return "{{" + name + "}}"
    return value
