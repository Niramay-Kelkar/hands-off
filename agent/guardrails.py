"""Guardrail enforcement — checked at the point of action, not just
assumed from prompt/config. A capability (replay) or a discovery run
literally cannot act outside its allowed origin/routes/action-types,
even if some other config layer is wrong or missing (defense in depth,
per CLAUDE.md).

Takes primitives (origin, route patterns, action types) rather than a
Capability so both replay (sourcing them from `artifact.guardrails`) and
discovery (sourcing them from CLI defaults — no artifact exists yet)
call the same enforcement logic.
"""

from __future__ import annotations

from fnmatch import fnmatch
from urllib.parse import urlparse


class GuardrailViolation(RuntimeError):
    pass


def derive_origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def check_action_type(action_type: str, allowed_action_types: list[str]) -> None:
    if action_type not in allowed_action_types:
        raise GuardrailViolation(
            f"action type {action_type!r} is not in the allowed_action_types {allowed_action_types}"
        )


def check_route(url: str, allowed_origin: str, allowlist_routes: list[str]) -> None:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin != allowed_origin:
        raise GuardrailViolation(f"origin {origin!r} is not the allowed origin {allowed_origin!r}")
    if not any(fnmatch(parsed.path, pattern) for pattern in allowlist_routes):
        raise GuardrailViolation(f"route {parsed.path!r} is not in the allowlist_routes {allowlist_routes}")
