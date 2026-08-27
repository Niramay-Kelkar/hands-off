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

    found.extend(_find_column_fields(page, wanted))
    return found


def _find_column_fields(page: Page, wanted: set[str]) -> list[SensitiveField]:
    """Second detection path, for a table-column shape the row-based
    label-cell/sibling-value convention above can't see: a header cell
    (e.g. "Share ID") naming an entire column, whose sensitive values are
    one per data row rather than living next to a single label cell.

    Unlike the row path's normalization (spaces -> underscore, matching
    config entries already given in that form, e.g. "account_number"),
    this path's config entries come as their natural on-page header text
    (e.g. "Share ID") -- so matching only lowercases/strips, no underscore
    join. Both conventions read from the same `wanted` set; which one
    applies depends on how the caller's redact_fields entry is spelled.

    Uses xpath child-axis locators (./tr, ./td|./th), not the descendant
    locators used elsewhere in this module, because a plain CSS/role
    descendant query on a hostile nested-table layout would reach into a
    nested table's own rows/cells instead of stopping at the outer
    table's direct children -- silently misattributing values to the
    wrong column position.
    """
    found: list[SensitiveField] = []
    tables = page.locator("table")
    for t in range(tables.count()):
        table = tables.nth(t)
        rows = table.locator("xpath=./tr | ./tbody/tr")
        row_count = rows.count()
        if row_count < 2:
            continue

        header_cells = rows.nth(0).locator("xpath=./td | ./th")
        for col in range(header_cells.count()):
            header_text = header_cells.nth(col).inner_text().strip()
            normalized = header_text.lower()
            if normalized.endswith(":"):
                normalized = normalized[:-1].strip()
            if normalized not in wanted:
                continue

            field_name = re.sub(r"\s+", "_", normalized)
            for r in range(1, row_count):
                row_cells = rows.nth(r).locator("xpath=./td | ./th")
                if row_cells.count() <= col:
                    continue
                cell = row_cells.nth(col)
                value = cell.inner_text().strip()
                if value:
                    found.append(SensitiveField(field_name=field_name, value=value, locator=cell))

    return found


def label_for_value(value_locator: Locator) -> str | None:
    """Given a resolved value-cell Locator (e.g. from a discovery extract
    call), finds its associated label by walking to the preceding sibling
    cell -- the same label-cell <-> sibling-value convention as
    find_sensitive_fields, just walked in reverse. Returns the raw label
    text including its trailing ':' (e.g. "Savings Balance:"), or None if
    there's no adjacent label cell."""
    label = value_locator.locator("xpath=preceding-sibling::*[1]")
    if label.count() == 0:
        return None
    text = label.inner_text().strip()
    if not text.endswith(":"):
        return None
    return text


def mask_text(text: str, fields: list[SensitiveField]) -> str:
    for field in fields:
        if field.value:
            text = text.replace(field.value, "[REDACTED]")
    return text
