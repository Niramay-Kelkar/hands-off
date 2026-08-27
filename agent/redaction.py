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


def normalize_label(text: str) -> str:
    """Lowercase and collapse runs of whitespace AND underscores to a
    single space, so a redact_fields entry can be declared either as
    natural on-page label text ("New Share ID") or pre-joined
    (DEFAULT_REDACT_FIELDS's own "account_number" style) and still match
    a label the other way. Caller strips any trailing ':' first.

    Found live: with the old space-vs-underscore-only handling, a
    multi-word natural-text entry like "New Share ID" could never match
    here, because `wanted` kept it space-form while the label side was
    joined with underscores -- silently un-redactable regardless of what
    was declared, only unnoticed until Open New Share's confirmation
    page ("New Share ID:") was the first multi-word ROW label (as
    opposed to a column header or select-detection label, which already
    matched on space-preserved text) ever exercised against this path.
    """
    return re.sub(r"[\s_]+", " ", text.strip().lower())


def find_sensitive_fields(page: Page, redact_fields: list[str]) -> list[SensitiveField]:
    """Scans the current page for label cells whose normalized text
    matches a redact_fields entry (e.g. "Account Number:" -> "account number"),
    returning the real value and a Locator on it for each match found."""
    if not redact_fields:
        return []

    wanted = {normalize_label(f) for f in redact_fields}
    found: list[SensitiveField] = []

    cells = page.get_by_role("cell")
    for i in range(cells.count()):
        cell = cells.nth(i)
        text = cell.inner_text().strip()
        if not text.endswith(":"):
            continue
        normalized = normalize_label(text[:-1])
        if normalized not in wanted:
            continue

        sibling = cell.locator("xpath=following-sibling::*[1]")
        if sibling.count() == 0:
            continue
        value = sibling.inner_text().strip()
        if value:
            field_name = re.sub(r"\s+", "_", normalized)
            found.append(SensitiveField(field_name=field_name, value=value, locator=sibling))

    found.extend(_find_column_fields(page, wanted))
    found.extend(_find_select_option_fields(page, wanted))
    return found


def _find_column_fields(page: Page, wanted: set[str]) -> list[SensitiveField]:
    """Second detection path, for a table-column shape the row-based
    label-cell/sibling-value convention above can't see: a header cell
    (e.g. "Share ID") naming an entire column, whose sensitive values are
    one per data row rather than living next to a single label cell.

    Uses the same `normalize_label` matching as the row path above, so a
    `redact_fields` entry can be declared either as natural header text
    (e.g. "Share ID") or pre-joined (e.g. "account_number") and match
    either way.

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
        # A genuine data grid in this app always has 3+ columns (e.g. the
        # member detail page's "Share ID | Type | Balance | Status"); a
        # 2-column table is a vertical label:value FORM (e.g. Funds
        # Transfer's "From Share:" / <select> row), where column 0 across
        # every row is a different field's LABEL, not one column's worth of
        # per-row data. Without this guard, a redact_fields entry like
        # "From Share" both correctly matches the select-detection path
        # below AND wrongly matches here as a "column header", making this
        # path walk column 0 of every subsequent row -- i.e. grab "To
        # Share:"/"Amount:"/"Memo:"'s own label cells as if they were
        # sensitive per-row values in a "From Share" column. Found live via
        # a screenshot masking the label column instead of the value
        # column on the Funds Transfer form.
        if header_cells.count() < 3:
            continue
        for col in range(header_cells.count()):
            header_text = header_cells.nth(col).inner_text().strip()
            if header_text.endswith(":"):
                header_text = header_text[:-1].strip()
            normalized = normalize_label(header_text)
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


def _find_select_option_fields(page: Page, wanted: set[str]) -> list[SensitiveField]:
    """Third detection path, for a <select> whose <option> list enumerates
    sensitive values directly (e.g. a Funds Transfer form's From/To Share
    dropdowns) -- neither of the two paths above can see these: there's no
    label CELL ending in ':' next to a single value (the label sits next
    to the whole <select>, not a value), and there's no <table> at all for
    the column-header path to scan.

    Matching is exact (normalized) string equality between the enclosing
    row's label and a redact_fields entry -- the same convention every
    other detection path in this module already uses. This requires the
    caller to declare the field under the label it actually appears as
    (e.g. "From Share" and "To Share" as their own redact_fields entries,
    alongside "Share ID" for the column-header path), rather than relying
    on word-overlap ("Share ID" matching "From Share" because both
    contain "share") -- overlap is looser than intended and can both
    over-match (any other "... Share ..." labelled control) and give a
    false sense of coverage from a single declared entry. Each
    option's identifier is its `value` attribute (e.g.
    `<option value="100234-S0001">100234-S0001 - Regular Shares
    ($1,500.00)</option>`) -- exact and unambiguous, unlike parsing it
    back out of the option's display text.
    """
    found: list[SensitiveField] = []
    selects = page.locator("select")
    for i in range(selects.count()):
        select = selects.nth(i)
        row = select.locator("xpath=ancestor::tr[1]")
        if row.count() == 0:
            continue
        label_cell = row.locator("xpath=./td[1] | ./th[1]")
        if label_cell.count() == 0:
            continue
        label_text = label_cell.first.inner_text().strip()
        if label_text.endswith(":"):
            label_text = label_text[:-1].strip()
        normalized_label = normalize_label(label_text)
        if normalized_label not in wanted:
            continue

        field_name = re.sub(r"\s+", "_", normalized_label)
        options = select.locator("option")
        for j in range(options.count()):
            opt = options.nth(j)
            value = opt.get_attribute("value") or ""
            if value:
                # Locator is the <select> itself, not this <option> -- a
                # closed <select> only ever visibly renders its currently
                # chosen option's text; the option elements have no
                # bounding box of their own for a screenshot's mask= to
                # paint over. Masking the whole control instead actually
                # hides whatever it's currently showing, which is the
                # real screenshot risk (mask_text's substring replace,
                # used for trajectory/log text, still keys off `value`
                # and is unaffected by this choice).
                found.append(SensitiveField(field_name=field_name, value=value, locator=select))

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
