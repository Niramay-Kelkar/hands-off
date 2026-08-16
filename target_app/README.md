# target_app — local "legacy bank" demo application

A deliberately hostile, deliberately small Flask app that serves the
member-lookup flow (search by member ID -> results -> detail with name
and savings balance) that `schema/example_artifact.json`'s
`member_balance_lookup` capability is discovered and replayed against.

This app is **not** shaped to make automation easy. It exists to make
automation hard in the specific ways a real legacy enterprise UI is
hard, while still being solvable via the accessibility-tree-first
locator strategy described in the root `CLAUDE.md`.

## Run it

```bash
pip install -r requirements.txt
python -m target_app.seed      # creates and populates target_app/members.db
python -m target_app.server    # serves http://localhost:8000/members
```

`members.db` is generated, not committed — rerun `target_app.seed` any
time to reset it to a known state.

## Hostile markup, on purpose

- Page layout is built from nested `<table>` elements, not semantic
  containers (`pageshell` -> `hdrTbl` / `frmOuter` -> `frmInner` /
  `resWrapOuter` -> `resultsRegion`, etc.), and the nesting shape differs
  between the search page and the detail page.
- Every page embeds an `<iframe>` (branch notices) that is unrelated to
  the actual data flow — present so the perception layer has to deal
  with a frame on the page without the core flow ever requiring
  frame-scoped locators.
- Class names are non-semantic legacy-CMS style (`c1`, `c2`, `lbl1`,
  `fld1`, `dtlCell`) and there are no `data-testid` attributes anywhere.
- Despite all of that, every interactive element has a correct
  accessibility role and accessible name (`<label for>` on the search
  input, real `<button>` text, `aria-label="Search results"` on the
  results table, an `<h2>` containing "Member Detail", link text equal
  to the member ID) — the accessibility tree is the intended primary
  locator strategy, same as it would be on a real legacy enterprise app.

## Two different kinds of "not the happy path" — and why they're built differently

This app deliberately keeps two fault categories implemented in two
different places, because they are two different things in the artifact
schema and should stay visibly different in the code:

**DB-driven business outcomes** (`target_app/db.py`, `members.db`) — a
legitimate, expected end state that the automation should recognize as
a normal result, not an error. Found by a genuine `SELECT ... WHERE id
= ?` against SQLite. Zero rows back means "not found" — there is no
in-app dict of valid IDs to fall back on, so a not-found result reflects
an actual absent row, the way it would against a real database. These
map to the artifact's `expected_outcomes`.

**App-layer fault injection** (`target_app/server.py`, route handlers
only) — a recoverable runtime condition that has nothing to do with
whether the member record exists: an artificial delay, an unexpected
modal. These IDs (`10003`, `10004`) are intentionally **absent** from
`members.db` — their behavior is hardcoded in the route handlers, never
read from the database, so the fault-injection path can never be
confused with genuine record data. `10004` in particular has no DB row
at all; its appearance in search results and its detail-page content
are fabricated inline in `server.py` purely so there is something to
click into and something for the interstitial to sit in front of. These
map to the artifact's recoverable/escalation conditions, not to
`expected_outcomes`.

## Seeded member IDs

| Member ID | Source | Behavior | Maps to |
|---|---|---|---|
| `10001` | DB row (`access_denied=0`) | Golden path: results row -> detail page with name + balance | happy path |
| `10002` | DB row (`access_denied=1`) | Search returns a page-level "You do not have permission" banner, no results table | `ACCESS_DENIED` business outcome (detection scope: `page`) |
| `10003` | Not in DB — app-layer only | Search sleeps ~3.5s server-side before rendering (a genuine, unrelated record would render fine after) | recoverable condition — exercises step 2's `network_idle` wait (8000ms timeout, comfortably survives the delay) |
| `10004` | Not in DB — app-layer only, fixture data in `server.py` | Search results show a fabricated row; clicking into detail renders an unexpected in-page modal (`role="dialog"`, aria-label "Notice") that must be dismissed before the underlying content is usable | recoverable/escalation trigger — lines up with the existing `escalation_override.on_unrecognized_dialog` on step 3 of `schema/example_artifact.json` |
| any other well-formed 5-digit ID (e.g. `99999`) | Genuine `SELECT` returns zero rows | Results region shows "No records found" | `MEMBER_NOT_FOUND` business outcome (detection scope: `results_region`) |

## `detection.scope` is a contract with this app's `aria-label` regions

The replay engine's `text_present` checkpoint resolves `expected_outcomes[].detection.scope`
generically, not via any target-app-specific selector list:

- `scope: "page"` searches the main document's text only (`page.locator("body").inner_text()`).
  Iframe content is never included — the branch-notice `<iframe>` renders a
  separate document, so its text is structurally absent from the parent
  document's DOM regardless of scope, not filtered out after the fact.
- any other `scope` value is looked up as `role="region"` + `aria-label="<scope>"`
  on the current page, e.g. `scope: "results_region"` matches the
  `<div class="resultsRegion" role="region" aria-label="results_region">`
  wrapper on the search page. If no such region is found, the engine falls
  back to a whole-page search and logs a warning — so a scope name must
  match a real `aria-label` here for detection to be scoped correctly.

This means adding a new expected-outcome scope to an artifact requires a
matching `role="region" aria-label="<scope>"` element somewhere on this
app's pages — same spirit as the rest of the app: still no test IDs, but
the accessibility tree is asked to carry real structure.

## Routes

| Route | Purpose |
|---|---|
| `GET /members` | Entry point. Always renders the search form. If `?member_id=` is present, also renders the results section (table, "not found" message, or "access denied" banner) below it. |
| `GET /members/<member_id>` | Member detail page. |
| `GET /branch-notice` | Static content loaded into the sidebar `<iframe>`; not part of the data flow. |
