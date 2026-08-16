"""Discovery's "observe" step: a text description of the current
accessibility tree — role, accessible name, and enough structure to
distinguish elements. Deliberately not a screenshot: the model reasons
over the same representation the replay engine's locator strategies are
built from (role + accessible name), so what it decides maps cleanly
onto artifact-ready locators later.
"""

from __future__ import annotations

from playwright.sync_api import Page

MAX_OBSERVATION_CHARS = 4000


def observe(page: Page) -> str:
    snapshot = page.locator("body").aria_snapshot()
    if len(snapshot) > MAX_OBSERVATION_CHARS:
        snapshot = snapshot[:MAX_OBSERVATION_CHARS] + "\n... [truncated, page has more content]"
    return snapshot
