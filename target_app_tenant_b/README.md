# target_app_tenant_b — cross-tenant reuse test fixture

A second, differently-branded "credit union" app serving the same
member search -> results -> detail flow as `target_app`. Built to
answer one specific, previously-untested claim from `REPORT.md`'s
Heterogeneity section: does a capability compiled against tenant A
replay unmodified against a genuinely different skin of the same flow?
See root `CLAUDE.md`'s cross-tenant reuse addendum for the full brief.

## What's the same as target_app (the control)

Every interactive element the compiled `member_balance_lookup` artifact
locates keeps the exact same accessible role + name:

- `<label for="...">Member ID</label>` on the search input
- `<button>Search</button>`
- the results link's accessible name equals the member ID (not the
  member's name)
- detail-page cells named `Name:` and `Savings Balance:` (real `<td>`
  elements, so `role=cell` is genuine, not ARIA-faked)
- `role="region" aria-label="results_region"` wrapping the "No records
  found" text, for `MEMBER_NOT_FOUND` detection

## What's genuinely different (the variable under test)

- Company name/branding: "Northbrook Community Credit Union" vs. tenant
  A's "First Meridian Trust & Savings"
- Visual design: flexbox/card layout, green palette, Segoe UI, rounded
  corners — vs. tenant A's beige nested-table 1990s-intranet look
- DOM shape: `<header>`/`<aside>`/`<main>`/`<section>` semantic
  containers instead of tenant A's `<table class="pageshell">` nesting
  (the results list is a `<ul>`, not a `<table>`)
- Class names: `cu-*` prefix throughout, no overlap with tenant A's
  `frmOuter`/`dtlCell`/`c1`/`c2` naming
- Separate SQLite DB (`members_b.db`), separate member roster (`20001`
  Alice Nguyen, distinct from tenant A's `10001`–`10003`)

## What's deliberately NOT built here

No app-layer fault injection (no slow-load/interstitial IDs) — this
fixture only needs a happy path and a not-found case to test locator
resilience against styling/markup drift. Out of scope for this pass;
see CLAUDE.md.

## Run it

```bash
python -m target_app_tenant_b.seed      # creates and populates members_b.db
python -m target_app_tenant_b.server    # serves http://localhost:8001/members
```

Binds to its own port, `TENANT_B_PORT` (default 8001) — separate from
`target_app`'s 8000 — so both apps run concurrently; also brought up as
its own `docker-compose.yml` service (`target_app_tenant_b`, published
on 8001) alongside `target_app`.

Because the compiled artifact's guardrail origin check is derived from
the artifact's own `target.entry_point`, replaying
`member_balance_lookup.compiled.json` *unmodified* against tenant B
means pointing at a copy of that artifact with `target.entry_point` (and
`target.app`) repointed at `localhost:8001` — the mechanical
steps/locators/checkpoints are otherwise byte-identical to the tenant-A
artifact. See `BUILD_LOG.md` for exactly how the cross-tenant replay
test does this.

## Seeded member IDs

| Member ID | Behavior |
|---|---|
| `20001` | Happy path: Alice Nguyen, $8390.55 |
| any other ID (e.g. `29999`) | Genuine `SELECT` returns zero rows -> "No records found" (`MEMBER_NOT_FOUND`) |

## Result of the cross-tenant replay test

See the root `REPORT.md` Heterogeneity section and `BUILD_LOG.md` for
the actual result of replaying `member_balance_lookup.compiled.json`
against `20001` here, unmodified.
