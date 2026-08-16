"""Guardrail enforcement — checked at the point of action, not just
assumed from prompt/config. A capability literally cannot act outside
the routes/action-types it was discovered against, even if some other
config layer is wrong or missing (defense in depth, per CLAUDE.md).
"""

from __future__ import annotations

from fnmatch import fnmatch
from urllib.parse import urlparse

from agent.models import ActionModel, Capability


class GuardrailViolation(RuntimeError):
    pass


def check_action_type(action: ActionModel, artifact: Capability) -> None:
    if action.type not in artifact.guardrails.allowed_action_types:
        raise GuardrailViolation(
            f"action type {action.type!r} is not in this capability's allowed_action_types "
            f"{artifact.guardrails.allowed_action_types}"
        )


def check_route(url: str, artifact: Capability) -> None:
    path = urlparse(url).path
    if not any(fnmatch(path, pattern) for pattern in artifact.guardrails.allowlist_routes):
        raise GuardrailViolation(
            f"route {path!r} is not in this capability's allowlist_routes {artifact.guardrails.allowlist_routes}"
        )
