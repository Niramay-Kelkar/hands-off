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

---

## 2026-08-16 — Agent-facing capability API (stretch goal)

**Built:** `agent/capability_api.py` — the one stretch goal taken on
this project, per the brief's "agent-facing capability catalog"
description. `GET /capabilities` scans `schema/capabilities/` (new
directory — `schema/example_artifact.json` stays where it is,
untouched, as schema documentation, excluded by directory boundary
rather than any filename inference) and returns a catalog entry per
compiled artifact. `POST /capabilities/<id>/invoke` is a thin wrapper
around the unmodified `engine.run_capability()` — same three-shape
`ReplayResult` contract, no new envelope, mapped to `200`
(success/business_outcome) or `500` (hard_failure). Demonstration-scale
only, by design: no auth, no queueing, one synchronous headed run per
invoke — noted in CLAUDE.md as a REPORT.md Cuts item.

Moved `schema/member_balance_lookup.compiled.json` into the new
`schema/capabilities/` directory, which is now the conventional home
for compiled artifacts meant to be served.

**Verified:** started target_app + `capability_api` together.
`GET /capabilities` returned exactly one entry (`member_balance_lookup`)
sourced only from `schema/capabilities/`, confirming
`schema/example_artifact.json`'s identical `capability_id` doesn't leak
into the catalog. `POST .../invoke` with `member_id=10001` → `200`,
`status: "success"`, correct outputs. Same route with `member_id=10002`
→ `200` (not `500`), `status: "business_outcome"`,
`outcome_code: "ACCESS_DENIED"` — confirming business outcomes map to
`200` as designed, not treated as errors at the HTTP layer either.
Also spot-checked the error paths: missing required param → `400` with
a clear message, unknown capability_id → `404`. All three request/
response pairs (catalog + both invokes) saved as curated evidence under
`evidence/capability_api/`.

**Bugs found & fixed:** none — worked as designed on the first
implementation.

**Committed:** pending.

---

## 2026-08-16 — Safety pass: redaction and risk gating, closed not just documented

Both items below were previously documented as design stances in
CLAUDE.md but never actually exercised against real data or enforced by
the engine — this phase closed both gaps for real.

**Redaction.** Added a genuine sensitive field to `target_app`: an
unmasked `account_number` column, shown on the member detail page like
a real teller-facing app would, seeded with realistic values (`10001` ->
`4471882203`), never a declared output of `member_balance_lookup`. Built
`agent/redaction.py` (`find_sensitive_fields`, DOM-scan based — matches
live label cells against `redact_fields`, returns the real value *and* a
Playwright `Locator` on it) and `mask_text`. Wired in three places:
`agent/perception.py`'s `observe()` masks discovery's full
accessibility-tree dump at the source (so the live model conversation
and everything persisted from it share one masked copy — a later
addition to the original plan, at effectively zero cost since nothing
in this capability needs the real value); `agent/evidence.py`'s
`StepLogWriter` gained an optional attached `page` and rescans on every
`log.jsonl` event as defense-in-depth, covering every existing
`log.event()` call site with no changes to `actions.py`/`checkpoints.py`/
`locators.py`; `evidence.save_screenshot()` gained `mask_locators`,
using Playwright's native `mask=`/`mask_color=` screenshot parameters
(confirmed working before committing to the approach) rather than
manual `bounding_box()` compositing — same visual effect, no image
library, correctly handles scroll/DPI.

**Verified against real data, not just console output**: ran discovery
against `member_id=10001` (reaches the detail page) and checked all
three persistence points directly — `grep`'d the raw account number
`4471882203` in the trajectory JSON (0 matches) and `log.jsonl` (0
matches), confirmed `[REDACTED]` present in both (3 matches each), and
visually confirmed the screenshot shows a solid black block over the
account number row while Name/Savings Balance stay fully legible.

**Bug found and fixed during verification**: the first discovery run
crashed with `Event loop is closed! Is Playwright already stopped?`.
Cause: `run_discovery`'s final `log.event("run_end", ...)` call happens
*after* the `with sync_playwright()` block exits (browser and driver
both stopped), but `StepLogWriter` still held the now-dead `page`
reference from `attach_page()` and tried to rescan it. Fixed by calling
`log.attach_page(None)` in the `finally` block right before
`browser.close()`. `engine.py` doesn't have the equivalent bug — every
`log.event()` call there happens before its `browser.close()`, checked
directly by reading the call order rather than assuming.

**Risk gating.** Confirmed `engine.py` never checked `risk_class` or
`guardrails.requires_confirmation` anywhere — a real gap between what
the schema declares and what the engine enforces. Fixed: `run_capability`
now checks, right after entry navigation succeeds and before the step
loop starts, whether `risk_class != "read_only"` or
`requires_confirmation` is true, and if so calls the *existing*
`escalation.pause_for_escalation()` (step `0`, reason
`"risk_confirmation_required"`) — reusing the same operator-console
mechanism already built for mid-run escalation, just triggered
pre-emptively.

**Verified** with a synthetic in-memory `Capability` (not shipped code —
`risk_class: "mutating"`, one trivial click step, constructed directly
via the Pydantic models) run through the real `engine.run_capability()`:
confirmed via `escalation.latest_paused_run()` and the run's own
`log.jsonl` that it paused at step `0` with `risk_confirmation_required`
*before* any `locator_resolved`/`action` event existed in the log (i.e.
truly before the first action, not just before its result), resumed it,
and confirmed the click then executed and the run completed
(`log.jsonl`: `risk_gate_pause` -> `risk_gate_resumed` ->
`locator_resolved` -> `action` -> `checkpoint` -> `run_end`). Re-ran
`member_balance_lookup` (`read_only`) immediately after — succeeded with
zero pause and zero behavior change, confirming the gate only engages
where it should.

**Committed:** `6e92787` — "Close redaction and risk-gating safety gaps".

---

## 2026-08-16 — Evidence curation surfaces a real compiler bug: mechanical checkpoints/extract locators overfit to the discovery run's literal values

Regenerating evidence for `/evidence/` (discovery run, four replay
outcomes, redaction, risk gating — see the next entry) required a
genuine escalation replay against `member_id=10004`. That run is what
caught this; `10001`/`10002`/`99999` never could have, because `10001`
is the exact member the shipped compiled artifact was originally
discovered against, and `10002`/`99999` both resolve through the
`outcome_match` branch before ever reaching the step whose checkpoint
was broken. The bug was structural, not incidental — the compiler's
mechanical layer had one path that happened to only ever get exercised
by the one member it was compiled from.

**Bug 1 — step 3's checkpoint hardcoded `"Jane Doe"`.**
`compiler.py`'s `_checkpoint_for` builds a step's checkpoint from *the
next action's* locator. For the step that clicks into the member detail
page, the next action in the trajectory is the `extract` call for
`member_name` — and an extract's locator *is* the literal value it
reads (that's what makes extraction possible). Templatizing that value
into the checkpoint baked one specific discovery run's answer
("Jane Doe") into every future replay, so replaying for `10004`
("Pat Whitfield") correctly resolved the locator, correctly landed on
the right page, and then failed its checkpoint anyway — not a false
negative on a bad element, a checkpoint that could structurally never
match anyone but member `10001`.

**Bug 2 — the same overfit lived one step deeper, in extraction itself.**
Fixing bug 1 (checkpoint) exposed that step 4's own extract locators had
the identical flaw: compiled as `role="cell", name="Jane Doe"` /
`name="$4521.10"` — literal values, unable to resolve on any other
member's page by construction. Retrying `10004` after the checkpoint fix
got further (correctly past step 3) and then hit a clean `hard_failure`
at extraction — the right *category* of outcome, correctly diagnosed,
but still not a working capability for any member besides the one it
was compiled from.

**The real fix: extract locators compiled from the field's *label*, not
its value.** `agent/redaction.py` already had exactly this pattern for a
different purpose — `find_sensitive_fields` walks label cell -> sibling
value cell to redact known-sensitive fields. Added the reverse walk,
`label_for_value(locator)` (value cell -> preceding-sibling label cell,
e.g. `"Savings Balance:"`), reusing the same xpath-sibling technique, not
reimplementing it. Wired discovery's `extract` tool
(`discovery_tools.py`) to resolve this label alongside the value on every
extract call and carry it on `TrajectoryStep.extract_label`
(`trajectory.py`) — the model-facing tool schema is unchanged; it still
points at the value it sees, exactly as before. The compiler now prefers
a label-based locator (`_label_locator_for`) for both `member_name` and
`savings_balance` — this was applied to both fields, not just the one
that surfaced the bug, since `savings_balance`'s locator had the exact
same structural flaw and would have failed identically for any member
whose balance differs from `10001`'s. Step 3's checkpoint is now derived
from that same label (`next_action.extract_label`) instead of a second,
separate hand-rolled rule — one source of truth, not two fixes.

**Bug 3, found while implementing bug 2's fix — the label/value split was
never keyed on a role that actually exists.** The first version of the
label-locator fix used `role="text"`, matching the convention already
written by hand in `schema/example_artifact.json`. Replaying against it
failed immediately, on `10001` — the golden path, not even the escalation
run. Dumping this app's real `aria_snapshot()` showed why: label cells
and value cells both render as role `"cell"` — `role="text"` isn't a
role Playwright's accessibility tree ever assigns here, and never was.
The hand-authored example was written before `target_app` existed and
was aspirational, not verified against a live page — and
`agent/actions.py`'s `_read_extracted_text`, which had a `role == "text"`
special case for exactly this scenario since the replay-engine phase,
had never actually been exercised by any prior verified replay (every
compiled artifact prior to this fix always matched the value directly,
never through a label). Fixed by dropping the role check entirely:
`_read_extracted_text` now detects "this resolved to a label" from the
resolved element's own text ending in `:` — the same signal
`redaction.py` already keys on — rather than a role that this app never
renders. `compiler.py`'s `_label_locator_for` and step 3's checkpoint
both changed from `role="text"` to `role="cell"` to match. Corrected
`schema/example_artifact.json`'s two extract-field locators the same
way, in its own separate commit — a hand-authored assumption, disproven
by live evidence, corrected, not silently folded into the compiler fix.
That correction only touches the `role` field on those two locators;
`PolicySpec` (`extra="ignore"`) never parses a policy file's `steps` at
all, so it has zero effect on the already-compiled
`member_balance_lookup.compiled.json`.

**Verified**: re-ran discovery for `member_id=10001` (captures
`extract_label` for both fields), recompiled, and re-ran the full
matrix — `10001` success, `10002` `ACCESS_DENIED`, `99999`
`MEMBER_NOT_FOUND`, all clean, no retries. `10004` now escalates on the
interstitial, pauses, and — after a human dismisses the dialog in the
*same* live session and resumes via the operator console — completes
with a genuine `success`: `member_name: "Pat Whitfield"`,
`savings_balance: 2310.0`. This is the first time this capability has
round-tripped a member other than the one it was discovered against.

**Committed:** `8c78221` "Fix compiler overfitting extract locators and
checkpoints to discovery's literal values".

---

## 2026-08-16 — Containerize target_app + operator_console (Docker)

**Built:** `Dockerfile` (single image, `python:3.11-slim` + `pip install
-r requirements.txt`, no source baked in) and `docker-compose.yml`
(repo root), bringing up `target_app` and `agent.operator_console` as
containers. Both bind-mount the repo root at `/app` instead of `COPY`ing
source, so they always run whatever's on disk and share
`target_app/members.db` / `evidence/sessions/sessions.db` with
host-run processes via identical relative paths — no path translation.
Also added `.dockerignore` (venv/, .git/, __pycache__/, evidence
runs/sessions/media) to keep the build context small.

`agent.discover`, `agent.replay`, and `agent.capability_api` were
deliberately left out — all three drive a real Playwright browser via
`agent.engine.run_capability()`, and containerizing a headed browser
needs X11/VNC forwarding, ruled out as not worth the complexity for
this project up front (see CLAUDE.md).

**Bug caught before it shipped: `capability_api` isn't actually
browser-free.** Initially scoped (by the user, correctly caught in
review before any Dockerfile was written) as a third "headless Flask
process" to containerize alongside `target_app`/`operator_console`.
Checked `agent/capability_api.py` directly: `/invoke` calls
`run_capability(capability, params)` with no `headed` override, and
`agent.engine.run_capability` defaults `headed=True` — so every invoke
launches a real, visible Chromium, identical to `replay.py`. Corrected
scope to two services, not three, before writing any container config.
Resolved by exposing `CAPABILITY_API_HEADED` (env var, default
`"true"`) so the behavior is an explicit, documented setting rather
than an accidental default now that it'd been noticed — not containerized
itself, since forcing it headless purely to fit in a container would
have been a behavior change no one asked for, and running it headed
inside a container reintroduces the exact X11 complexity ruled out for
`discover.py`/`replay.py`.

**A second, smaller bug found while first testing the containers**:
`target_app/server.py` and `agent/operator_console.py`'s `app.run()`
calls had no explicit `host`, so Flask's default (loopback-only) meant
the containers' published ports (`8000`, `8100`) would be unreachable
from the host despite `docker compose up` reporting both as started.
Fixed by reading `host=os.environ.get("FLASK_RUN_HOST", "127.0.0.1")`
in both, with `docker-compose.yml` setting `FLASK_RUN_HOST=0.0.0.0` for
both services — host-run behavior (no env var set) is unchanged.

**Verified end to end, not just "containers started":**
- `docker compose up -d --build`: both images built and both containers
  reported running.
- `curl http://localhost:8000/members?member_id=10001` from the host —
  200, real member data in the response (containerized target_app is
  actually reachable and serving, not just alive).
- `curl http://localhost:8100/` — 200, and the page showed a *real*
  paused run (`member_balance_lookup_..._7e33b2`, step 3,
  `on_checkpoint_failure`) left over in `evidence/sessions/sessions.db`
  from earlier host-side testing — proof the containerized
  `operator_console` reads the exact same shared session state a host
  process writes, not a fresh/empty one. Its screenshot route also
  returned 200, confirming the bind-mounted screenshot path resolved
  correctly across the host/container boundary.
- `python -m agent.replay --capability
  schema/capabilities/member_balance_lookup.compiled.json --input
  member_id=10001 --headless` from the host, against the *containerized*
  `target_app` — `status: "success"`, correct outputs. The replay engine
  doesn't know or care whether its target is containerized.
- `docker compose run --rm target_app python -m target_app.seed` —
  exercises the README's documented first-time-setup path; confirmed it
  seeds `target_app/members.db` on the host filesystem (via the bind
  mount), matching what `python -m target_app.seed` does when run
  directly.
- `CAPABILITY_API_HEADED=false python -m agent.capability_api`, invoked
  against `member_id=10001` — `200`, `status: "success"`, ran headless
  with no other behavior change.

**Also updated:** README.md gained a "Quick start via Docker" section
(alongside, not replacing, the venv instructions) covering `docker
compose up`, the bind-mount/shared-state model, and first-time seeding.

**Committed:** pending.

---

## 2026-08-16 — `--repeat N` stability flag on `agent.replay`

**Built:** `--repeat N` on `agent/replay.py` (default `1`, existing
single-result behavior unchanged). For `N > 1`, reruns the same
capability + params N times — each a fully independent
`run_capability()` call, fresh browser, no shared state — and prints a
per-run line (`status`, `outcome_code` if present, duration) followed
by a stability summary: counts by `status`, counts by `outcome_code`,
and min/max/avg duration. Exit code is nonzero only if any run hard-failed.
`--repeat 0` (or negative) is rejected with a clear message before any
browser launches, same posture as existing input validation. Exists to
demonstrate, not just assert, the brief's own claim that
record-once/replay-many is only viable because the target UI is stable
across runs.

**Verified:**
- `--repeat 5` against `member_balance_lookup` / `member_id=10001`
  (headless, against a locally-running `target_app`) — `5/5 success`,
  durations `5.65s`–`6.41s`.
- `--repeat 3` against `member_id=99999` — confirms the outcome-code
  breakdown works for the business-outcome branch too:
  `3/3 business_outcome`, all `MEMBER_NOT_FOUND`.
- `--repeat 0` — rejected (`--repeat must be >= 1`, exit code 2),
  before touching the browser.
- `--repeat 1` (default) — output and exit-code behavior byte-for-byte
  unchanged from before this change (single JSON result to stdout,
  `{"success": 0, "business_outcome": 0, "hard_failure": 1}` mapping).

**Evidence captured**: `--repeat 8` against `member_balance_lookup` /
`member_id=10001`, saved to
`evidence/stability/member_balance_lookup_10001.txt` — `8/8 success`,
durations `5.54s`–`6.65s`, avg `5.75s`. Added as its own entry in
`evidence/README.md`.

**Committed:** pending.

---

## 2026-08-16 — Real bug found via REPORT.md fact-checking: ACT-phase escalations still redid the action on retry/resume

REPORT.md's own escalation sequence diagram and Section 3's "any retry,
automatic or human-resumed, only ever re-verifies, never re-acts" claim
were only true for the one escalation path actually exercised before now
— `on_unrecognized_dialog`, a **CHECK**-phase trigger (`10004`'s
interstitial). They were never true for **ACT**-phase triggers.

**The gap.** `agent/engine.py`'s `_act_once` did three things inside one
try block: resolve the locator, perform the action
(`ACTION_REGISTRY.get(...)(...)`), then — still inside the same
try/except — check the post-action route guardrail and, if configured,
run the settle-wait (`page.wait_for_load_state("networkidle", ...)`). Any
exception from *any* of those three raised the same `on_step_timeout` or
`on_hard_failure` trigger, and `_run_with_escalation`'s retry/escalate
path always re-called the *whole* `_act_once` — meaning a settle-wait
timeout that happened strictly *after* the action had already fired and
registered still caused the action to be redone, both by an automatic
retry and, if that also failed, by a human resuming an escalated run.
This affects exactly the two triggers `_act_once` can raise:
`on_step_timeout` and `on_hard_failure`. Both are configured
`then: "escalate"` in the shipped `member_balance_lookup` policy
(`on_step_timeout: {retry: 1, then: escalate}`,
`on_hard_failure: {retry: 0, then: escalate}`), so this wasn't a
theoretical edge case — it was live in the shipped artifact's own policy,
just never exercised by any prior test, because every prior escalation
test (`10004`) happened to hit the dialog-detection path in `_check_once`
instead, which was never affected by this bug in the first place.

**The fix.** Split `_act_once` into two independently-retried phases,
extending the existing ACT/CHECK split rather than replacing it:
- `_act_once` now does exactly two things — resolve the locator, perform
  the action — and returns `(resolved, before_url)`. Once
  `ACTION_REGISTRY`'s call returns without raising, this function is
  never called again for this step, no matter what fails afterward.
- `_settle_once` (new) does the post-action route check and the optional
  settle-wait, given the `(resolved, before_url)` `_act_once` produced.
  It never touches `ACTION_REGISTRY`. `_execute_step` now runs three
  `_run_with_escalation`-wrapped phases per step — ACT, SETTLE, CHECK —
  instead of two; a SETTLE-phase retry or escalation-resume only
  re-runs `_settle_once`.

The `on_step_timeout`/`on_hard_failure` trigger strings, and the
escalation policy's retry counts for them, are unchanged — only *what*
gets re-invoked on retry changed.

**Verified with a new standalone fixture** (not shipped code, same
pattern as the earlier synthetic `risk_class: "mutating"` fixture) —
a `Capability` built directly via the Pydantic models with a single step:
a custom `counter_click` action (registered ad hoc into
`ACTION_REGISTRY`, not part of the shipped action set) that appends to a
file-backed counter — observable independently of any page state — then
clicks a harmless, non-navigating element, then fires a background,
non-awaited `fetch()` against `target_app`'s seeded slow-load route
(`member_id=10003`, a real 3.5s server-side sleep) via `page.evaluate()`
with no returned promise, so the click itself returns immediately but the
page has genuine pending network activity afterward. The step's
settle-wait is set to `timeout_ms: 500`, and `escalation_policy` mirrors
the shipped policy's `on_step_timeout: {retry: 1, then: escalate}`
exactly.

Run through the real `engine.run_capability()`, in a background thread so
the main script could poll `escalation.latest_paused_run()` and resume
through the real `agent.operator_console` HTTP endpoint (not
in-process):
1. The run escalated via `on_step_timeout`, as configured.
2. Counter was `1` after the initial attempt *and* the automatic retry —
   the retry did not re-invoke the action.
3. Resumed via a real `POST /resume/<run_id>` against the running
   `operator_console` process, after waiting out the background fetch's
   3.5s window (same as a real operator would, just by not being
   instantaneous).
4. Counter was still `1` after resume, and the run reached a clean
   `status: "success"`.

**Confirmed this is a real regression test, not just a passing script**:
stashed the `agent/engine.py` fix (`git stash push -- agent/engine.py`),
re-ran the identical fixture against the pre-fix code, and it failed
exactly as predicted — counter was `2` after only the initial attempt
and one automatic retry (i.e. the retry re-clicked). Restored the fix
(`git stash pop`) and re-ran clean. Also re-ran the full existing replay
matrix (`10001` success, `10002` `ACCESS_DENIED`, `99999`
`MEMBER_NOT_FOUND`) against the fixed code to confirm zero behavior
change for the CHECK-phase and non-escalating paths.

**Secondary fix, same pass**: `agent/escalation.py`'s
`latest_paused_run()` was the only way `operator_console.py` looked up a
run — an unscoped `SELECT ... ORDER BY updated_at DESC LIMIT 1`, fine
for one run paused at a time but not actually "this run's state" if more
than one were ever paused concurrently. Added `escalation.get_run(run_id)`
(scoped `SELECT ... WHERE run_id=?`); `operator_console.py`'s `/` route
now accepts an optional `?run_id=` and uses `get_run` when present,
falling back to `latest_paused_run()` with no `run_id` (preserves the
existing "walk up to the console cold" behavior exactly). Also fixed
`/screenshot/<run_id>` to use the new scoped lookup directly instead of
re-fetching "latest" and comparing — it was silently 404-ing for any
run's screenshot other than the single most-recently-paused one, which
this change also happens to fix. Verified: `/?run_id=<unknown>` correctly
renders "no run" rather than falling back to an unrelated paused run.

**Committed:** pending.

## 2026-08-25 — Cross-tenant reuse: converted the "untested bet" into a tested result

REPORT.md's Heterogeneity section previously stated the multi-tenant
locator-resilience claim was "a bet, not a proof — it was not tested
against a second, differently-configured instance of the same
application." Built `target_app_tenant_b/` to close that gap on a
single-session time budget.

**What was built**: a second Flask app (`target_app_tenant_b/`), same
search → results → detail flow as `target_app`, genuinely different
branding/markup/CSS ("Northbrook Community Credit Union", flexbox/card
layout, green palette, `<ul>` results instead of a `<table>`, `cu-*`
class names with zero overlap with tenant A's `frmOuter`/`dtlCell`/`c1`/
`c2`) but the exact same accessible role + name on every interactive
element the compiled artifact locates: `<label>Member ID</label>`,
`Search` button, results link named for the bare member ID, detail-page
cells `Name:` / `Savings Balance:`. Separate `members_b.db`, one
happy-path member (`20001`, Alice Nguyen) and reliance on a genuine
zero-row SELECT for the not-found case (`29999`) — deliberately not the
full five-outcome matrix tenant A has (no slow-load/interstitial
re-seeded on tenant B), per the scoped instructions for this pass.

**Constraint discovered while wiring up the test**: `agent/engine.py`
derives `allowed_origin` from the artifact's own `target.entry_point`,
not from wherever the browser actually navigates — so replaying the
artifact *unmodified* requires it to be reachable at that same origin.
Originally worked around by running tenant B on port 8000, in place of
`target_app`, one at a time. Revised on request: `target_app_tenant_b`
now takes its own port via `TENANT_B_PORT` (default 8001, read in
`target_app_tenant_b/server.py`), and `docker-compose.yml` gained a
`target_app_tenant_b` service (published `8001:8001`) alongside
`target_app` (`8000:8000`) so both run concurrently — same
bind-mount-the-repo-root pattern as the existing services. To still
replay the tenant-A-recorded artifact unmodified against tenant B now
that they're on different origins, the test loads
`member_balance_lookup.compiled.json`, writes an in-memory copy with
only `target.app`/`target.entry_point` repointed at `localhost:8001`,
and replays that copy — every step/locator/checkpoint byte-identical to
the shipped artifact; `schema/capabilities/member_balance_lookup.compiled.json`
itself stays untouched, still pointed at tenant A.

**Test**, both apps up simultaneously: `python -m target_app.server`
(8000) and `python -m target_app_tenant_b.server` (8001, after
`python -m target_app_tenant_b.seed`), then replayed the port-8001
artifact copy:

```
python -m agent.replay --capability <tenant-b-port-copy> --input member_id=20001 --headless
{"status": "success", "outputs": {"member_name": "Alice Nguyen", "savings_balance": 8390.55}}

python -m agent.replay --capability <tenant-b-port-copy> --input member_id=29999 --headless
{"status": "business_outcome", "outcome_code": "MEMBER_NOT_FOUND", "step_id": 2}
```

**Result: identical to the original (port-8000-shared) test.** Same two
outcomes, same values. `log.jsonl` for both runs shows every locator
resolution — 5 on the `20001` run, 2 on the `29999` run — hitting the
primary `accessibility` strategy; zero fallback to `text_label`, exactly
as before. Concurrent operation changed nothing about the result, as
expected — it only removed the "run one or the other" constraint.

**Evidence requirement, explicit this time — did the screenshot pipeline
actually save screenshots?** Checked directly rather than assumed: both
raw run directories' `screenshots/` came back empty. This is correct
behavior, not a gap — `evidence_policy.screenshot_on` is
`[checkpoint_failure, hard_failure]` only, and neither run hit either
condition. Confirmed this is the existing, intentional pattern, not
something specific to tenant B: `evidence/replays/success_10001/` and
`evidence/replays/business_outcome_not_found_99999/` (both tenant A,
saved back on 2026-08-16) also have no `screenshots/` contents, for the
identical reason. Nothing to backfill in the automatic pipeline's own
output — re-saved `evidence/replays/cross_tenant_b_success_20001/` and
`evidence/replays/cross_tenant_b_not_found_29999/` from the new
port-8001 runs regardless, so the evidence reflects the final concurrent
setup rather than the superseded shared-port one.

**What the automatic pipeline can't produce on its own — a clean success
screenshot — was captured explicitly**, since screenshot-on-failure-only
means no run in this whole evidence set has ever shown what a *working*
page looks like. A short standalone Playwright script (not part of
`agent/`, run directly against both live apps) navigated to tenant A's
`/members/10001` and tenant B's `/members/20001` and took a full-page
screenshot of each, masking the account-number cell the same way
`agent/evidence.py`'s `save_screenshot` does (Playwright's native
`mask=`), consistent with the rest of the repo's redaction discipline
even though this script runs outside the replay engine. One bug on the
way: the first attempt located the mask target via
`page.locator("tr", has_text="Account Number:")`, which on tenant A's
genuinely nested `<table>` markup matches every ancestor `<tr>` that
contains that text too (the outer content-cell row, not just the
specific label/value row) — chaining `.locator("td").nth(1)` off that
multi-element match grabbed the wrong `<td>` and blacked out most of the
page. Fixed by locating the label cell itself via
`get_by_role("cell", name="Account Number:", exact=True)` and taking its
`xpath=following-sibling::td[1]` — unambiguous regardless of nesting.
Saved as `evidence/replays/cross_tenant_b_20001/tenant_a_detail_10001.png`
and `.../tenant_b_detail_20001.png` — same artifact, two visually
distinct tenants, side by side.

`evidence/README.md` updated to document all three `cross_tenant_b_*`
evidence directories (previously only referenced in REPORT.md/this log,
not indexed there) and to frame the paired screenshots explicitly as the
visual proof of the claim, with the log-based fallback data as the
mechanism proof underneath it. `target_app_tenant_b/README.md` updated
for the new port and the artifact-copy mechanism.

**Committed:** pending.

## 2026-08-26 — Cross-tenant reuse, extended: the full escalation lifecycle, not just locator targeting

The prior cross-tenant round proved locator resilience (happy path +
not-found) but left the escalation/handoff mechanism — arguably the
more load-bearing half of the system, per the brief's own weighting —
untested against a second tenant. Closed that gap with one new seeded
ID, reusing tenant A's interstitial pattern and CDP-attach verification
method directly rather than designing anything new.

**What was built**: `target_app_tenant_b/server.py` gained
`INTERSTITIAL_ID = "20004"` and `INTERSTITIAL_FIXTURE` (Marcus Webb,
$3175.20), a line-for-line mirror of `target_app`'s `10004`/
`INTERSTITIAL_FIXTURE` pattern — fabricated search result and detail
record, never touching `members_b.db`. `templates/detail.html` gained
the same modal block tenant A's `detail.html` has
(`role="dialog" aria-modal="true" aria-label="Notice"`, a "Continue"
button that hides the overlay), styled with new `cu-modal*` classes in
`cu.css` for visual consistency with the rest of tenant B's branding.
`agent/engine.py`'s dialog detection (`_has_unexpected_dialog`, any
visible `role="dialog"`/`"alertdialog"`) needed zero changes — it was
already generic, not tied to tenant A's specific markup or text.

**Constraint discovered while wiring up the reproduction**: BUILD_LOG's
own 2026-08-15 entry describes the original escalation proof as
"attaching a second Playwright process to the *same* live browser over
CDP," but that script was never committed — `agent/engine.py` launched
Chromium with no `--remote-debugging-port`, so there was nothing for an
external process to attach to. Rather than reinvent the verification
method, added the minimal seam needed to actually reuse it: `AGENT_CDP_PORT`
(unset by default, zero behavior change), read once in
`run_capability()` and passed as `--remote-debugging-port=<port>` to
`pw.chromium.launch()` — same spirit as the existing `CAPABILITY_API_HEADED`
seam.

**Test**: tenant A (8000), tenant B (8001), and `agent.operator_console`
(8100) all running concurrently. A driver script set `AGENT_CDP_PORT=9223`,
ran `run_capability()` in a background thread (headed, the port-8001
copy of `member_balance_lookup.compiled.json`, `member_id=20004`),
polled `agent.escalation` for a newly-paused run (had to exclude
pre-existing stale paused rows in `sessions.db` from earlier sessions —
`latest_paused_run()` returns whatever's most recently updated
system-wide, not scoped to this run), confirmed `pause_reason ==
"on_unrecognized_dialog"`, screenshotted the live `operator_console`
view of the pause, connected a second Playwright process via
`connect_over_cdp("http://localhost:9223")` to the SAME browser and
clicked "Continue" (standing in for a human — the identical technique
from the original 2026-08-15 verification), then issued a real
`POST http://localhost:8100/resume/<run_id>` via `urllib.request` (no
new dependency) against the live operator console process, not an
in-process call.

**Result: identical mechanism, identical outcome, different tenant.**
`log.jsonl` for the new run shows the exact same event sequence as
tenant A's `escalation_10004/`: `unrecognized_dialog` (step 3) ->
`escalate`, `trigger: "on_unrecognized_dialog"` -> `escalation_resumed`
-> `run_end`, `status: "success"`. Final result:
`{"status": "success", "outputs": {"member_name": "Marcus Webb",
"savings_balance": 3175.2}}`. The engine's own pause screenshot
(`screenshots/step_3_on_unrecognized_dialog.png`) has the account number
correctly redacted, same as every other escalation screenshot in this
repo; spot-checked `log.jsonl` for the raw account number value —
absent, as expected (never reached, since `extract` only ever runs for
`member_name`/`savings_balance`).

Evidence saved: `evidence/replays/cross_tenant_b_escalation_20004/`
(`result.json`, `log.jsonl`, `operator_console_pause.jpg`,
`screenshots/step_3_on_unrecognized_dialog.png`), same four-file shape
as `evidence/replays/escalation_10004/`. `evidence/README.md` and
REPORT.md's Heterogeneity section updated to state the stronger claim:
not just locator targeting generalizes to a second tenant, but the full
checkpoint/outcome/escalation lifecycle does too, with zero changes to
the artifact or the escalation mechanism itself.

**Committed:** pending.

## 2026-08-26 — Confidence & approval gate (stretch goal, tightly scoped)

**Built**: `agent/approval.py` — `approval_status(capability_id)` reads
`schema/capabilities/<capability_id>.approval.json`
(`{"status": "draft"|"approved", "approved_by": null, "approved_at": null}`),
defaulting to `"draft"` on a missing file, a parse failure, or any
`status` value other than the literal string `"approved"` — fail closed
in every failure mode, not just the happy "file exists and says draft"
case. `agent/capability_api.py`'s `POST /invoke` calls `is_approved()`
right after the 404 check and before `run_capability()`, returning
`403` with a message naming the exact file to edit. `agent/replay.py`
gained `--update-confidence`: reuses `_run_stability()`'s existing
aggregation (success rate as `(N - hard_failures) / N`, `by_status`,
`by_outcome_code`, duration min/max/avg) unchanged, only adding a
`Path.write_text()` of that same data to
`<capability_id>.confidence.json`. `--update-confidence` alone (without
an explicit `--repeat`) still routes through the stability path so a
single-run confidence file can be produced deliberately, rather than
only mattering when combined with a large `--repeat`.

**Deliberate scope boundary**: `agent.replay`'s CLI is not gated by
approval status at all — only `capability_api`'s `/invoke` checks it.
The brief's own phrasing is "gate unattended replay"; a person running
`agent.replay` directly from a terminal already is the human in the
loop, so gating that path would be gating something that isn't actually
unattended. The gate exists for the one path in this system where an
agent could invoke a capability with nobody watching.

**Verified live**, both directions, against `member_balance_lookup`
(`target_app` on 8000, `agent.capability_api` on 8200, headless via
`CAPABILITY_API_HEADED=false`):

1. Created `member_balance_lookup.approval.json` with `status: "draft"`
   (the file didn't exist before this session). `POST /invoke
   {"member_id": "10001"}` → `403`, error message names the approval
   file. Saved as `evidence/capability_api/invoke_10001_draft_403.json`.
2. Flipped the file to `status: "approved"`. Same invoke → `200`,
   `{"status": "success", "outputs": {"member_name": "Jane Doe",
   "savings_balance": 4521.1}}`. Saved as
   `evidence/capability_api/invoke_10001_approved_200.json`.
3. Flipped back to `"draft"` and confirmed `agent.replay` (the CLI, not
   the API) against the same capability still succeeds — the gate has
   zero effect on that path, exactly as designed — then re-confirmed
   the API immediately re-blocks with `403` in that same draft state,
   before restoring the file to `"approved"` as this session's final
   state.

**Ran `--update-confidence` for real**, not fabricated: 5 headless runs
of `member_balance_lookup` against `member_id=10001` — `5/5 success`,
durations 5.58s–5.85s (avg 5.69s), written to
`schema/capabilities/member_balance_lookup.confidence.json`.

**Documentation**: CLAUDE.md gained a "Stretch goal: confidence &
approval gate" section (and the capability API section's heading no
longer claims to be "the only stretch goal taken on," since it isn't
anymore); REPORT.md's Safety section (6) gained one paragraph stating
the mechanism and what was verified, sized to match how small the
feature actually is relative to the other three safety mechanisms
already documented there.

**Committed:** pending.

---

## 2026-08-26 — CI: replay smoke test

**Built:** `.github/workflows/replay-smoke-test.yml`, triggered on both
`push` to `main` and `pull_request` (so branch protection can gate
merges on it, not just report status afterward). Scoped to exactly the
deterministic half of the system — `agent.replay` against the
already-compiled `member_balance_lookup` artifact. No discovery, no
compilation, nothing requiring `ANTHROPIC_API_KEY`, no Python version
matrix, no parallel job splitting — deliberate, see CLAUDE.md.

Steps: checkout, Python 3.11, `pip install -r requirements.txt`,
`playwright install --with-deps chromium`, seed `target_app`'s DB,
`docker compose up -d --build target_app operator_console` (reused
as-is, no new compose service), then four replay checks:

- `10001` → asserts `status: "success"`, correct outputs.
- `10002` → asserts `status: "business_outcome"`, `outcome_code:
  "ACCESS_DENIED"`.
- `99999` → asserts `status: "business_outcome"`, `outcome_code:
  "MEMBER_NOT_FOUND"`.
- `10004` — the standout check: an automated regression test for the
  escalation lifecycle itself, not just the happy path. A new script,
  `.github/scripts/check_escalation_lifecycle.py`, launches
  `agent.replay --headless` against `10004` with `AGENT_CDP_PORT` set
  (the same CDP-attach mechanism validated during the cross-tenant
  escalation testing, now driven by CI instead of by hand), polls
  `sessions.db` until the run reaches `status: "paused"`,
  `pause_reason: "on_unrecognized_dialog"`, attaches a second Playwright
  process to the SAME live browser to click the modal's "Continue"
  button (standing in for a human fixing the page in place — resuming
  without this would just re-detect the still-open dialog and
  re-escalate, since `/resume` only flips `owner`, per
  `agent/operator_console.py`), `POST`s the real
  `/resume/<run_id>` endpoint, then asserts the paused run completes
  with `status: "success"`.

**Checked replay.py's output/exit-code behavior first, per the ask,
before adding anything to it.** The single-run CLI path already prints
one clean JSON blob to stdout and returns exit code `1` on
`hard_failure`, `0` on `success`/`business_outcome` (`agent/replay.py`,
`main()`). That's sufficient for a shell step to assert on structurally
(`json.load` + field checks) — no CLI change was needed, and none was
made; the ask was explicit about avoiding screen-scraping, not about
adding flags preemptively.

**Bug found and fixed while validating the escalation script locally,
before it ever ran in CI:** the first version of
`check_escalation_lifecycle.py` picked "the most recently paused row"
in `sessions.db` with no time bound. Locally, `sessions.db` had several
stale `status: "paused"` rows left behind by earlier, unrelated manual
escalation tests from prior sessions — the script picked one of those
up instead of the run it had just launched, then correctly failed with
"replay did not complete after resume" (it was resuming a process that
no longer existed). Fixed by scoping the lookup to rows with
`updated_at` at-or-after the script's own start time (with a few
seconds of slack for clock skew). This wouldn't have surfaced in a
genuinely fresh CI checkout (`evidence/sessions/` is gitignored), but
it's a correctness gap regardless of what triggered it, so it's fixed
rather than left as a "works in CI, not reliably re-runnable locally"
footgun.

**Verified end to end on this machine**, running the exact commands the
workflow runs (`docker compose up -d --build target_app
operator_console`, then all four replay checks in sequence, including
the escalation script after clearing the stale rows described above):
all four passed. `docker compose down` afterward; no stray containers,
temp files, or uncommitted state left behind (`git status` clean aside
from the new `.github/` files).

**Documentation:** CLAUDE.md gained a "CI: replay smoke test" section.
README.md gained a status badge at the top, pointed at this workflow.

**Committed:** `74de2f2`.

**Failed on its actual first GitHub Actions run** (pushed by the user
right after the commit above), with `sqlite3.OperationalError: attempt
to write a readonly database` out of `agent/escalation.py`'s
`open_session()`. Root cause: `operator_console` runs as root inside
its container (no `USER` in `Dockerfile`); the "wait for services"
step's health-check `curl` to `:8100/` is the first thing to touch the
bind-mounted `evidence/sessions/sessions.db` — through
`escalation.latest_paused_run()` — so the file gets created root-owned
on the host. `agent.replay` then runs on the *host* as the CI runner's
non-root user and can't write to it. This never surfaced in local
verification because Docker Desktop on macOS remaps container UIDs
transparently on a bind mount; a native Linux Docker host (GitHub
Actions' runner) doesn't — exactly the kind of environment-specific gap
this workflow exists to catch, just caught one step later than ideal
(on the real run instead of local verification, because local
verification structurally couldn't have caught it).

**Fix:** a new step, "Pre-create shared session store," runs before
`docker compose up` — `mkdir -p evidence/sessions && touch
evidence/sessions/sessions.db && chmod -R 0777 evidence/sessions`. The
file already exists, host-owned and world-writable, before either the
container or the host-side `agent.replay` process ever opens it, so
whichever side gets there first no longer matters.
`evidence/sessions/sessions.db` holds only run bookkeeping (`run_id`,
`status`, `current_step_id`, `pause_reason`, `screenshot_path`,
`updated_at`) — no extracted business data or PII — so a permissive
mode on this specific, gitignored, ephemeral directory doesn't
reintroduce the class of risk `evidence_policy.redact_fields` and the
mask-on-screenshot mechanism exist to prevent.

Re-verified the full four-check sequence locally with the fix in place
(same commands the workflow runs) — all four still pass. The
UID-mismatch failure mode itself can't be reproduced on macOS for the
reason above, so the actual proof this fix works is the next GitHub
Actions run, not local verification.

**Committed:** pending.

---

## 2026-08-27 — Redaction: exact-match select/option detection

**Redaction tightening.** `_find_select_option_fields` (`agent/redaction.py`)
previously matched a `<select>`'s enclosing row label against
`redact_fields` by word overlap ("From Share" / "To Share" both contain
"share", so they matched a bare "Share ID" entry) — looser than every
other detection path in the module, which all do exact normalized-string
matching. Changed to exact match: normalize the label (lowercase, strip
trailing `:`, collapse whitespace) and require it to equal an entry in
`redact_fields` exactly. This requires the caller to declare a field
under the label it actually appears as, so
`schema/capabilities/meridian/funds_transfer.policy.json` gained
explicit `"From Share"` / `"To Share"` entries alongside the existing
`"Share ID"` (used by the column-header path on the member detail
page). Recompiled `funds_transfer.compiled.json` from its original
`source_run_id` with the updated policy — diffed against the prior
compiled artifact and confirmed the *only* change is the
`evidence_policy.redact_fields` list gaining the two new entries; every
step/locator/checkpoint is byte-identical.

This change had a real, non-obvious regression — `_find_column_fields`
started mis-firing on the very same new `redact_fields` entries,
producing a visibly broken masked screenshot. Caught in review, fixed,
and re-verified with fresh live evidence — see the next entry, which is
the actual fix and the evidence for both changes together.

**Committed:** pending.

---

## 2026-08-27 — Fix: `_find_column_fields` mis-fired on the new "From Share"/"To Share" entries

User spot-checked `evidence/meridian/funds_transfer/discovery_redaction_check/screenshots/step_15_step.png`
from the exact-match redaction change above and caught a real defect: a
huge black box covering the *label* column ("To Share:", "Amount:",
"Memo:" text itself, not their values) rather than just the "To Share"
select's value box.

**Root cause:** `_find_column_fields` (the column-header detection path,
for grids like the member detail page's "Share ID | Type | Balance |
Status") normalizes a header cell's text by stripping a trailing `:`
before matching against `redact_fields` — meant to tolerate either
header style. The Funds Transfer form's own field table is a 2-column
vertical label:value form (row 0 = `"From Share:"` / `<select>`, row 1 =
`"To Share:"` / `<select>`, etc.) — structurally a table with a "header"
row too, from this function's point of view. Adding `"From Share"` as a
`redact_fields` entry (for the *select* detection path, intentionally)
also made it an exact match for THIS function's header-normalization,
which then treated column 0 across every subsequent row as "data in the
From Share column" — i.e. grabbed `"To Share:"`, `"Amount:"`, `"Memo:"`'s
own label cells as if they were per-row sensitive values, and masked
those cells' bounding boxes instead.

**Fix:** `_find_column_fields` now skips any table whose header row has
fewer than 3 columns. Verified this cleanly separates the two real
shapes in this app: the member detail page's genuine grid has 4 columns
(`Share ID | Type | Balance | Status`); every vertical label:value form
table (Funds Transfer, Place Account Hold) has exactly 2. Re-ran
`find_sensitive_fields` directly against both live pages: Funds
Transfer now returns only `from_share`/`to_share` entries (58 = 29
options × 2 selects, all correctly positioned over the value column,
label column untouched); the member detail page's Share ID column
still returns all 27 rows correctly (unaffected, non-regressing).

**Re-verified with a fresh real discovery run**
(`evidence/meridian/funds_transfer/discovery_redaction_check/`,
replacing the broken evidence): screenshot now shows clean label text
next to two correctly-boxed select values; 117 `[REDACTED]` occurrences
(matching the count from before the select-detection path existed at
all, confirming the row-based over-masking artifact — which had
inflated the prior broken run's count to 133 — is gone).

Also spot-checked, since `place_account_hold`'s own policy adds a
`"Share"` entry for the same select-detection path: the Hold form is
also a 2-column table, so it was never at risk of this specific
mis-fire either way — confirmed directly against the live page (only
`share` entries returned, correctly positioned; a harmless redundant
row-based match on the same field, since `inner_text()` on a `<select>`
returns all option text and the label cell literally reads `"Share:"`,
masks the same value-column region, not the labels).

**Committed:** pending.

---

## 2026-08-27 — MERIDIAN Place Account Hold capability

**Explored before building, per the task.** Wrote
`scripts/recon_place_hold.py` (same throwaway, no-LLM style as
`scripts/recon_meridian.py`) and ran it for real against both `teller1`
and `super1`. Finding (full transcript + screenshots in
`evidence/meridian/place_account_hold/exploration/`): this is a HARD
REJECTION, not an in-context supervisor-override prompt. The hold form
itself is visible to a non-supervisor (with a static "RESTRICTED
FUNCTION - SUPERVISOR OVERRIDE REQUIRED" warning banner in its heading,
but no interactive override control anywhere on the page); clicking
Continue as `teller1` lands on a dedicated rejection page instead of the
review screen — "SUPERVISOR OVERRIDE REQUIRED / Operator profile
teller1 is not authorized to perform this function. A supervisor must
sign on to complete this request." (HTTP 403), whose only affordance is
"Return to previous screen." The only way forward is a real sign-off/
sign-on cycle to a supervisor operator. This directly determined the
escalation design below — a resolvable in-session privilege gap
escalates, per the existing design decision, rather than being modeled
as a business outcome.

**Built the capability.** Discovery (as `super1`, the only path that can
reach a successful `done` trajectory — `teller1` cannot complete this
flow at all) against the real Place Account Hold form: sign on, click
Place Account Hold, search/select member 100234, select `* Share` and
`* Reason Code`, type `Notes`, Continue to the `CONFIRM ACCOUNT HOLD`
review screen, Apply Hold, extract the confirmation. Real hold placed
for real during discovery itself (`CN480444`). Per the task, `Share ID`
was deliberately omitted from `--redact-fields` for this one discovery
run only (the model needs to read real share IDs/statuses to choose an
OPEN share) — `E-mail`/`Phone`/`Address` stayed redacted throughout.
The compiled policy's own `evidence_policy.redact_fields` keeps `Share
ID` *and* a new `Share` entry (needed because this form's single select
label is exactly `"Share"`, not `"From Share"`/`"To Share"`) regardless
— replay never re-observes the page, so it has none of discovery's
read-the-page-to-select-a-value tension.

`schema/capabilities/meridian/place_account_hold.policy.json`:
`risk_class: mutating`, `requires_confirmation: true`; expected_outcomes
`INVALID_CREDENTIALS` (shared sign-on rejection text) and
`VALIDATION_ERROR` (real text from `?inject=validation` against the
hold route — see below); `step_overrides["12"]` (the compiled step_id
for the "Continue" click that submits the hold form — the actual
permission-boundary step) set to `retry: 0, then: escalate` on every
trigger, so a checkpoint failure there — the only way this step can
fail — always escalates immediately rather than retrying uselessly
against a wall that a retry can't fix.

**`?inject=` recon against the hold route**
(`evidence/meridian/place_account_hold/injects/summary.txt`):
`?inject=permission` (403) is byte-identical (module the operator name)
to the real, unforced `teller1` rejection — confirmed no separate
handling is needed, since it exercises the exact same
checkpoint-failure/step_overrides path. `?inject=validation` (400,
"TRANSACTION REJECTED" / "The transaction could not be completed as
entered.") is the same shared host-level rejection text
`funds_transfer`'s `VALIDATION_ERROR` uses, added as this capability's
own `VALIDATION_ERROR` outcome. `?inject=maintenance` (503) left
unmodeled, same reasoning as `funds_transfer`'s (a genuinely recoverable
condition, not a business outcome, needing engine-level retry timing
out of scope here). `?inject=notfound` isn't reachable at any step this
capability actually executes (member selection happens earlier).

**Tested for real, all the way through `agent.replay` (not just
discovery), via a driver script mirroring
`.github/scripts/check_escalation_lifecycle.py`'s CDP-attach pattern**
(`AGENT_CDP_PORT`, a second Playwright process attached to the SAME live
browser standing in for the human operator, the real
`agent.operator_console` `/resume/<run_id>` endpoint for every resume):

- **Happy path, `super1`, share `100234-MMKT-28`:** risk-gate pause ->
  resume -> `status: "success"`, `hold_confirmation: "CN480445"` — a
  real hold, confirmed live.
- **`teller1`, share `100234-CERT-27`:** risk-gate pause -> resume ->
  step 12 executes, checkpoint fails (lands on the rejection page, not
  the review screen), escalates immediately per `step_overrides`
  (`sessions.db`: `status=paused`, `current_step_id=12`,
  `pause_reason=on_checkpoint_failure`, real screenshot saved). The
  human recovery this time is NOT a same-identity fix like Transfer's
  dialog-dismiss — it's a real sign-off (`teller1`) / sign-on (`super1`)
  cycle in the SAME live session, then re-navigating and re-filling the
  hold form up to the review screen, so step 12's ORIGINAL checkpoint
  (`Apply Hold` visible) becomes true without the engine ever re-running
  step 12's own action (per the ACT/SETTLE/CHECK split). Resumed ->
  the engine's own CHECK-phase retry sees the checkpoint now pass and
  continues automatically to step 13, where the AUTOMATION (not the
  human) clicks `Apply Hold` and completes the real POST ->
  `status: "success"`, `hold_confirmation: "CN480446"`. Full narrative
  in `evidence/meridian/place_account_hold/escalation/note.txt`.

Curated evidence under `evidence/meridian/place_account_hold/`:
`exploration/` (the recon transcript + before/after screenshots for
both operators), `discovery/` (the real successful `super1` trajectory),
`replays/success/` and `escalation/` (the two `agent.replay` runs above,
logs + screenshots + result JSON), `injects/summary.txt`.

**One thing user-flagged during review, checked and confirmed NOT a bug:**
`evidence/meridian/place_account_hold/discovery/screenshots/` showing
Share ID and Reason Code in plain text is the direct, explicit result
of the task's own instruction (c): `Share ID` was deliberately omitted
from `--redact-fields` for that one discovery run only, because the
model needs to read real share IDs/statuses to select one — the same
tradeoff already documented for `funds_transfer`'s own original
discovery evidence (also unredacted for Share ID, same reason).
Confirmed E-mail/Phone/Address (which stayed in `--redact-fields`
throughout) never appear anywhere in this trajectory at all (the Place
Hold flow never visits the member detail page), so there's nothing
silently leaking beyond the one deliberate, documented exception.
Separately confirmed the *compiled* policy's `evidence_policy.redact_fields`
(`Share ID` + `Share`, used at replay time, which never has this
discovery-time conflict) correctly masks the Hold form's `<select>` when
exercised directly against the live page — the 3-column guard above
doesn't regress it (the Hold form is also a 2-column table, so it was
never at risk of the same mis-fire; there's a harmless redundant
row-based match on the same field too, since `inner_text()` on a
`<select>` returns all option text and the label cell literally reads
`"Share:"`, but it masks the same value-column region, not the labels
— confirmed visually, no layout defect).

**Committed:** pending.

---

## 2026-08-27 — MERIDIAN Open New Share capability

Lighter build, reusing label_proximity/select/redaction/step_overrides
machinery already in place — no permission wall for `teller1` (unlike
Place Account Hold), so this is structurally closer to Funds Transfer.

**Explored first**: `* Share Type` is a 4-option generic type-code
`<select>` (`S0001 - Regular Shares` / `S0070 - Share Draft (Checking)`
/ `MMKT - Money Market` / `CERT - Certificate`) shared across every
member, not a per-member Share ID — checked directly per the task's ask
("don't assume it won't"), confirmed no redaction/discovery conflict:
the full standard redact_fields set (including `Share ID`) applied with
no exclusion needed. Real minimum-deposit rejections captured live and
found to vary by share type — Regular Shares' minimum is $5.00,
Certificate's is $500.00 (`"Certificates require a minimum opening
deposit of $500.00."` vs `"A minimum opening deposit of $5.00 is
required."`) — both share the substring `"minimum opening deposit"`,
which `MINIMUM_DEPOSIT_NOT_MET`'s detection matches on rather than
enumerating a rejection per share type.

**A real, separate redaction gap found here, distinct from the
`_find_column_fields` bug above**: the post-submit confirmation page's
label is `"New Share ID:"`, not `"Share ID:"` — a different label
entirely, so the existing `Share ID` entry never matched it via any
path. Tracing why revealed the ROW-based path's own match comparison
had a latent bug: it joined the CELL's normalized label with
underscores before comparing against `wanted`, but `wanted` itself was
never joined the same way (kept natural spaced text like `"share id"`)
— so a multi-word natural-text `redact_fields` entry could never match
via that path, regardless of what was declared. This had gone
unnoticed because every prior MERIDIAN-specific entry (`E-mail`,
`Phone`, `Address`) was a single word, where the bug is invisible.
Fixed with a shared `normalize_label()` helper (lowercase, collapse
whitespace AND underscores to one space) used consistently by all
three detection paths — `account_number`-style pre-joined entries and
natural spaced entries like `"New Share ID"` now both match either way.
Re-verified non-regressing against the member detail Share ID column,
Transfer's From/To Share selects, and Place Account Hold's Share
select. `open_new_share.policy.json`'s own `evidence_policy.redact_fields`
adds `New Share ID` alongside the task's literal 5-field baseline — a
deliberate deviation, flagged: leaving it off would leave a real
per-member account identifier unredacted on this capability's own
confirmation screen.

Discovered and replay-tested for real: happy path (`teller1`, Regular
Shares, $50.00 → `CN480004`/`CN480005` across discovery + replay);
`MINIMUM_DEPOSIT_NOT_MET` business outcome (Regular Shares, $1.00 →
`"A minimum opening deposit of $5.00 is required."`); `?inject=validation`
confirmed real, matches the shared `VALIDATION_ERROR` text.
`?inject=permission` also returns a `SUPERVISOR OVERRIDE REQUIRED` page
here, but confirmed live that `teller1` completes the ENTIRE real flow
(including the actual post) with no natural permission wall anywhere —
unlike Place Account Hold, this inject doesn't correspond to any real
restriction for this capability, so no `step_overrides`/escalation
branch was added for it.

Evidence under `evidence/meridian/open_new_share/`: `discovery/` (with
its own `summary.txt` covering the two findings above),
`replays/success/`, `replays/minimum_deposit_not_met/`,
`injects/summary.txt`.

**Committed:** pending.
