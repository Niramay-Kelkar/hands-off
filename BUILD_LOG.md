# Build log

Append-only, one entry per work session. Covers what was built, what was
verified and against which seeded `target_app` cases, bugs found and how
they were fixed, and what got committed. This is session narrative/history
— for the current architectural state and the reasoning behind it, see
`CLAUDE.md`, which does not accumulate this kind of entry going forward.

---

## 2026-08-15 — Target app build

**Built:** local "legacy bank" demo app (`target_app/`) — Flask server,
SQLite-backed member records, search -> results -> detail flow, hostile
nested-table markup with an unrelated iframe and non-semantic classes,
but correct ARIA roles/accessible names throughout. Five seeded member
IDs: `10001` (happy path), `10002` (DB `access_denied=1`), `10003`
(app-layer slow-load fault injection), `10004` (app-layer interstitial
dialog fault injection, synthetic fixture not in the DB), any other
well-formed 5-digit ID (genuine zero-row `SELECT` -> not found).

**Verified:** exercised all five paths directly via `curl` against the
running dev server — happy-path search+detail markup, access-denied
banner, not-found message, slow-load timing (~3.5s), interstitial modal
markup, direct-hit 404s on IDs not reachable via the normal flow, iframe
present, accessible label/button markup on the search form.

**Bugs found & fixed:** none functional; caught along the way that the
repo's ignore file was named `gitignore` (no leading dot) and so was
never actually being read by git — renamed to `.gitignore`.

**Committed:** `878fc9c` "Add local target app for member-lookup flow"
(via GitHub Desktop).

---

## 2026-08-15 — Replay engine build

**Built:** `agent/` — Pydantic artifact models + three-shape
`ReplayResult` contract (`models.py`), generic function registry
(`registry.py`), locator/checkpoint/action registries (`locators.py` /
`checkpoints.py` / `actions.py`), guardrail enforcement
(`guardrails.py`), SQLite-backed escalation session store
(`escalation.py`) plus a minimal Flask operator console
(`operator_console.py`), an evidence/log writer with redaction
(`evidence.py`), the orchestration loop (`engine.py`), and the CLI
(`replay.py`). Also added `role="region" aria-label="results_region"`
to target_app's results region and documented the scope contract in
`target_app/README.md`, needed for the `text_present` checkpoint.

**Verified:** ran `python -m agent.replay` against the live target_app
for all five seeded member IDs — `10001` success with correct
extraction (`Jane Doe`, `$4521.10`); `99999`/`10002` business outcomes
(`MEMBER_NOT_FOUND`/`ACCESS_DENIED`) returned as normal results, not
errors; `10003` slow-load silently absorbed by the `network_idle` wait;
`10004` escalates and pauses correctly. Escalation/handoff proven
end-to-end by attaching a second Playwright process to the *same* live
browser over CDP (standing in for a human physically using the real
headed window), dismissing the interstitial, and confirming the paused
run's check-phase picked up the fix. Also verified: input validation
rejects missing/malformed/unknown params before the browser launches
(exit code 2), guardrail violations raise correctly for out-of-allowlist
routes and action types, and a broken entry-point URL returns a clean
`HardFailureResult` instead of crashing.

**Bugs found & fixed:**
- The interstitial modal didn't actually block Playwright's checkpoint
  queries (a semi-transparent overlay doesn't fail a plain visibility
  check) — dialog detection became a proactive interrupt checked before
  checkpoint evaluation each step, not derived from checkpoint failure.
- Resuming an escalated run was redoing the entire step from scratch,
  including re-clicking a link that no longer existed on the
  post-navigation page. Fixed by splitting every step into an ACT phase
  (resolve + act) and a CHECK phase (dialog check + checkpoint), with
  retries — automatic or human-resumed — only ever redoing CHECK, never
  re-running the action.
- `operator_console.py`'s screenshot route resolved relative paths
  against Flask's `root_path` (the `agent/` package dir) instead of the
  process's working directory, so `send_file` looked in the wrong
  place; fixed by resolving to an absolute path first.
- A failed initial navigation to `target.entry_point` propagated as an
  uncaught exception instead of a `HardFailureResult`, breaking the
  "always exactly one of three shapes" contract before step 1 even ran.

**Committed:** `9d724d0` "Add replay engine" (via GitHub Desktop).

---

## 2026-08-16 — Security gap: `check_route` ignored origin, only ever checked path

**Found while:** building the discovery agent's `navigate` tool, which
was the first thing in the codebase that could ask the browser to go to
an arbitrary, LLM-chosen URL.

**The gap:** `agent/guardrails.py`'s `check_route(url, artifact)` did
`path = urlparse(url).path` and fnmatched *only the path* against
`allowlist_routes` — it never looked at scheme/host/port. A URL like
`http://evil.example/members` would pass the check, because `/members`
matches the allowlist pattern regardless of which host it's actually on.

**Why it was latent, not exploited:** replay's browser only ever acts on
elements already on an allowlisted page, and `member_balance_lookup`'s
`allowed_action_types` doesn't even include `navigate` — so the gap was
real but structurally unreachable until an LLM with a `navigate` tool
entered the picture and could ask to go anywhere.

**Fix:** `check_route` now takes an explicit `allowed_origin` and
rejects any URL whose scheme+host+port doesn't match it, in addition to
the existing path-pattern check. Signatures for `check_route` and
`check_action_type` were also changed to take primitives
(`allowed_origin`/`allowlist_routes`/`allowed_action_types`) instead of
a whole `Capability`, so both replay (sourcing them from
`artifact.guardrails`) and discovery (sourcing them from CLI defaults —
no artifact exists yet) enforce through the same code path. `engine.py`
updated accordingly; replay's existing test matrix (10001/99999/10002)
re-run and confirmed unaffected.

**Verified:** direct calls against the live target_app confirmed a
cross-origin `navigate` is rejected with a clear detail message, and
that a `click` which happens to trigger off-allowlist navigation is
detected *after the fact*, reverted (`page.goto()` back to the prior
URL), and reported — not just logged as a fait accompli.

**Committed:** pending, together with the discovery agent build below.

---

## 2026-08-16 — Discovery agent build

**Built:** `agent/discover.py` (CLI), `agent/discovery.py` (the
observe -> decide -> act loop against the Anthropic Messages API, Claude
Sonnet 5, `tool_choice: {"type": "any", "disable_parallel_tool_use":
true}` so the model must call exactly one tool per turn — never
free-form text parsed for intent), `agent/discovery_tools.py` (tool
schemas for click/type/navigate/extract/done + a `Registry` of executor
functions, mirroring `agent.actions.ACTION_REGISTRY`'s pattern),
`agent/perception.py` (`observe(page)` via Playwright's
`locator("body").aria_snapshot()` — a text role+accessible-name tree,
not a screenshot, so the model reasons over the same representation the
replay engine's locators are built from), `agent/trajectory.py`
(Pydantic `Trajectory`/`TrajectoryStep` models — the raw output of a
discovery run, deliberately distinct from the artifact schema in
`agent/models.py`; the compiler, not yet built, turns one into the
other). Also added `anthropic` + `python-dotenv` to `requirements.txt`
and a `.env.example`.

**Verified without a live API key** (none available in this
environment): confirmed `aria_snapshot()` produces a legible role+name
tree even through target_app's hostile nested-table markup and
correctly excludes iframe content structurally; confirmed the CLI fails
cleanly with exit code 2 before touching the browser when
`ANTHROPIC_API_KEY` is unset; direct calls into
`discovery_tools.DISCOVERY_TOOL_REGISTRY` against the live target_app
confirmed zero-match, exactly-one-match, and ambiguous-N-match
resolution for `click`/`type`/`extract` all return a clean, specific
`ToolOutcome.detail` rather than raising; confirmed a cross-origin
`navigate` is blocked and a `click` that triggers off-allowlist
navigation is detected, reverted, and reported.

**Four live Claude Sonnet 5 runs, same goal** ("Search for member 10001
and read their name and current savings balance", entry point
`http://localhost:8000/members`) **— three superseded, one canonical:**

**Run 1 (`discover_20260816T014624Z_c5f7c6`, superseded) — found the
self-report bypass.** `done`'s original tool schema took `outputs`
directly. Step 4's `extract(role="cell", name="Jane Doe")` correctly
came back `ok: false` ("3 elements matched" — a real ambiguity, not a
false positive: this app's nested-table layout gives outer wrapper
cells a compound accessible name that concatenates all descendant text,
e.g. `cell "Member Detail Name: Jane Doe Savings Balance: $4521.10"`, so
a substring match on "Jane Doe" always also hits that wrapper and a
second nested one). Rather than retrying, the model just called `done`
with the values restated from its own observation text — bypassing
`extract`'s ground-truth verification entirely, the exact failure mode
that verification exists to prevent. Its evidence directory was later
cleared as routine local test cleanup; the run is preserved here in
prose only, not as retained files.

**Fix 1:** `done`'s schema no longer accepts output values — only
`output_names` referencing outputs already captured via a *successful*
`extract` call, plus `summary`. The executor resolves real values from
what `extract` verified, keyed by name; referencing an unextracted name
comes back as `ok: false` with a clear detail, same pattern as
zero/ambiguous-match errors, telling the model to extract it first.

**Runs 2 and 3 (`discover_20260816T054213Z_fcc91a`,
`discover_20260816T055344Z_b19fee`, both superseded) — fix 1 confirmed
correct but exposed a second, structural issue.** Both runs hit the same
"Jane Doe"/"$4521.10" 3-way ambiguity on every attempt (`cell`, `row`,
`table`, `rowgroup` — 9 and 8 failed extracts respectively) and, unable
to bypass verification anymore, eventually settled for extracting an
entire outer row as one merged string (`"Member Detail\nName:\tJane
Doe\nSavings Balance:\t$4521.10"`) instead of two clean fields. This
was reproducible across two independent runs, not model luck: verified
directly that `page.get_by_role("cell", name="Jane Doe")` (Playwright's
default substring matching) matches 3 elements on the real detail page,
while `exact=True` matches exactly 1. Every leaf cell's exact name is
necessarily a substring of its ancestor wrapper cells' concatenated
names in this markup, so substring matching made every leaf value
structurally ambiguous against its own wrappers — no amount of
model-side retrying could fix it.

**Fix 2:** `discovery_tools.py`'s `_resolve_unique` (used by
click/type/extract) now resolves with `exact=True`. Also applied to
`agent/locators.py`'s `name`-based accessibility strategy (replay), for
a reason beyond this bug: the compiler will translate discovery's
now-exact-matched `extract(role, name)` calls into artifact locators,
and if replay resolved `name` with different matching semantics than
what discovery actually verified against, a compiled capability could
behave differently at replay time than what discovery proved — a
determinism gap. Replay's full test matrix (10001/99999/10002) re-run
and confirmed unaffected by the change.

**Run 4 (`discover_20260816T055902Z_5643e2`, canonical) — clean.** 6
steps, no failures: `type` -> `click` Search -> `click` 10001 ->
`extract(role="cell", name="Jane Doe")` -> `extract(role="cell",
name="$4521.10")` -> `done(output_names=["member_name",
"savings_balance"])`. Both outputs independently Playwright-verified,
zero ambiguity, zero retries. Full trajectory, JSONL log, and per-step
screenshots retained at `evidence/runs/discover_20260816T055902Z_5643e2/`
— this is the canonical discovery evidence going forward; runs 1–3 are
historical record only.

**Bugs found & fixed:** the `check_route` origin gap (see the standalone
entry above), the `done` self-report bypass, and the substring-match
ambiguity — all three found and fixed during this phase, the first
logged separately since it's a security finding rather than a
discovery-agent-specific one.

**Committed:** pending.

---

## 2026-08-16 — Artifact compiler build

**Built:** `agent/compiler.py` (`compile_trajectory`), `agent/policy.py`
(`PolicySpec` — the four policy fields plus capability_id/description/
version, `extra="ignore"` so a full `Capability` JSON can be passed
directly as `--policy`), `agent/compile.py` (CLI), plus a `templatize()`
helper added to `agent/template.py` (inverse of `substitute` — turns a
literal value back into `{{param_name}}`). Merges a discovery
`Trajectory` (mechanical layer: steps, locators, checkpoints, inputs,
outputs, target) with an authored `PolicySpec` (policy layer:
expected_outcomes, guardrails, escalation_policy, risk_class) into a
final `Capability`. Locators only ever get `accessibility` + `text_label`
strategies — no `css` fallback is synthesized, since that would mean
re-walking the live DOM at compile time rather than only emitting what
discovery actually verified (documented as a Cuts item in CLAUDE.md).
Checkpoints are derived mechanically: step *i*'s checkpoint checks
visibility of whatever the very next successful action in the raw
trajectory acted on; the final (merged extract) step gets
`outputs_non_empty`.

**Compiled and verified:** `evidence/runs/discover_20260816T055902Z_5643e2/trajectory.json`
+ `schema/example_artifact.json` as `--policy` + `--param member_id=10001`
-> `schema/member_balance_lookup.compiled.json`. Replayed against all
four seeded cases: `10001` succeeds with correct outputs, `10002` and
`99999` correctly resolve as `ACCESS_DENIED`/`MEMBER_NOT_FOUND` business
outcomes (not errors), `10004` escalates and pauses at step 3 — same
step, same trigger, same operator-console display as the hand-authored
artifact — and a resume via the actual operator console HTTP endpoint
correctly re-checks rather than crashing.

**Bug found and fixed mid-verification:** the first compile run "succeeded"
structurally but `10002`/`99999` replays hung forever, blocked on an
escalation nobody was there to resume. Root cause: step 2's mechanically
derived checkpoint (`element_visible` on the results link) has no way to
recognize `ACCESS_DENIED`/`MEMBER_NOT_FOUND` as legitimate outcomes,
because a single successful trajectory never observes them — so the
compiled artifact's `expected_outcomes` and its `steps` were both present
but never actually wired together. Fixed by wrapping every non-final
`element_visible` checkpoint in `any_of: [element_visible(...),
outcome_match(<all policy-declared outcome codes>)]` — referencing only
codes policy already declares, not inventing anything, so it stays
"mechanical, no guessing" while actually making the two layers work
together. Re-verified against the full four-case matrix above. Also
found two long-dead `agent.replay` processes from hours-earlier manual
escalation testing still running/blocked in the background — killed,
unrelated to this bug.

**Committed:** pending.
