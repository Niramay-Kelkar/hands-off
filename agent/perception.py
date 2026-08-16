"""Discovery's "observe" step: a text description of the current
accessibility tree — role, accessible name, and enough structure to
distinguish elements. Deliberately not a screenshot: the model reasons
over the same representation the replay engine's locator strategies are
built from (role + accessible name), so what it decides maps cleanly
onto artifact-ready locators later.

Redaction happens here, at the source — this is the one string that
both feeds the model and gets logged/persisted, so masking it once here
means the model never sees a raw sensitive value it doesn't need,
covering both the "never persist" evidence requirement and, at
effectively zero extra cost, the live API call too.
"""

from __future__ import annotations

from playwright.sync_api import Page

from agent import redaction

MAX_OBSERVATION_CHARS = 4000


def observe(page: Page, redact_fields: list[str] | None = None) -> str:
    snapshot = page.locator("body").aria_snapshot()
    if redact_fields:
        fields = redaction.find_sensitive_fields(page, redact_fields)
        snapshot = redaction.mask_text(snapshot, fields)
    if len(snapshot) > MAX_OBSERVATION_CHARS:
        snapshot = snapshot[:MAX_OBSERVATION_CHARS] + "\n... [truncated, page has more content]"
    return snapshot
