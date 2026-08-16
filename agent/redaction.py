"""Real redaction, against real sensitive data actually visible on the
live page — not a guess at what might be sensitive.

DOM-based rather than a target-app DB lookup on purpose: it's what
generalizes to discovery (which has no DB access and no fixed params to
key a lookup on, only a goal and whatever it observes) as well as
replay, and it's the same accessibility-tree-first principle the rest
of this system's perception is built on. Reuses the same label-cell ->
sibling-value convention already established in target_app's markup and
agent/actions.py's extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Locator, Page


@dataclass
class SensitiveField:
    field_name: str
    value: str
    locator: Locator


def find_sensitive_fields(page: Page, redact_fields: list[str]) -> list[SensitiveField]:
    """Scans the current page for label cells whose normalized text
    matches a redact_fields entry (e.g. "Account Number:" -> "account_number"),
    returning the real value and a Locator on it for each match found."""
    if not redact_fields:
        return []

    wanted = {f.strip().lower() for f in redact_fields}
    found: list[SensitiveField] = []

    cells = page.get_by_role("cell")
    for i in range(cells.count()):
        cell = cells.nth(i)
        text = cell.inner_text().strip()
        if not text.endswith(":"):
            continue
        normalized = re.sub(r"\s+", "_", text[:-1].strip().lower())
        if normalized not in wanted:
            continue

        sibling = cell.locator("xpath=following-sibling::*[1]")
        if sibling.count() == 0:
            continue
        value = sibling.inner_text().strip()
        if value:
            found.append(SensitiveField(field_name=normalized, value=value, locator=sibling))

    return found


def mask_text(text: str, fields: list[SensitiveField]) -> str:
    for field in fields:
        if field.value:
            text = text.replace(field.value, "[REDACTED]")
    return text
