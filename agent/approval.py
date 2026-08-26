"""Approval gate for unattended capability invocation.

Only agent.capability_api's POST /invoke route checks this — agent.replay's
CLI is deliberately NOT gated, since "gate unattended replay" (the brief's
own phrasing) means the unattended API path an agent could call on its
own, not an operator running replay directly from a terminal.

A capability with no approval.json file defaults to "draft" — fail
closed, not open: a newly compiled capability nobody has reviewed yet
must not be silently invocable through the API just because no one got
around to writing its approval file.
"""

from __future__ import annotations

import json
from pathlib import Path

CAPABILITIES_DIR = Path("schema/capabilities")


def approval_path(capability_id: str) -> Path:
    return CAPABILITIES_DIR / f"{capability_id}.approval.json"


def approval_status(capability_id: str) -> str:
    """Returns the raw status string, defaulting to "draft" if the file
    is missing or unreadable — never inferred as "approved"."""
    path = approval_path(capability_id)
    if not path.exists():
        return "draft"
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return "draft"
    status = data.get("status")
    return status if status in ("draft", "approved") else "draft"


def is_approved(capability_id: str) -> bool:
    return approval_status(capability_id) == "approved"
