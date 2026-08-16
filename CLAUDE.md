# interface.ai take-home — Computer-Use Automation System

This file is a running design log for this project: the decisions made and the reasoning behind them, captured as they were made, starting before any code existed. It doubles as project context for AI-assisted development (Claude Code) and as supporting evidence for the reasoning behind REPORT.md.

For Claude Code specifically: treat this as the source of truth for "why," not just "what." Read this in full before making architectural changes.

## The assignment, in one sentence

Build the layer that gives an AI agent hands inside legacy bank software with no
API: an LLM discovers how to complete a task once by driving the real UI, that
discovery is compiled into a typed, versioned, replayable "capability" artifact,
and that artifact is later replayed deterministically — no LLM in the decision
loop — with proper error handling, safety guardrails, and human escalation.

Full brief: see the original PDF (Assignment_A — Computer-Use Automation System).
Do not deviate from their required deliverable paths and structure (see below).

## The core mental model

Two separate programs share one data structure:

- **Discovery** (`agent.discover`): LLM-driven observe → decide → act loop
  against a live browser. Slow, non-deterministic, run once per capability.
  Produces a typed artifact.
- **Replay** (`agent.replay`): given a saved artifact + input params, executes
  the recorded steps against the live UI with the LLM completely out of the
  decision loop. Fast, deterministic, run on every production invocation.

The artifact is the product. It's a capability contract (like a function
signature), not a macro recording — typed inputs, typed outputs, ordered steps
with ranked locator fallbacks, per-step checkpoints, declared expected business
outcomes, a risk classification, guardrails, and an escalation policy.

## What's actually being graded (in weighted order)

1. System design — the artifact schema and replay contract are explicitly
   called out as "a focal point of the evaluation." Do not treat this as
   boilerplate.
2. Correctness of the core loop — agent completes a real goal; artifact
   replays deterministically and verifies success.
3. Robustness & error handling — see the three-way outcome taxonomy below.
   This is where most submissions will be weak; do not be one of them.
4. Human-in-the-loop escalation — must transfer control of the SAME live
   session, not a fresh one. "Not just a TODO" is a direct quote from the brief.
5. Generalization to the real environment (design-only, in REPORT.md) —
   heterogeneous surfaces (web/legacy web/desktop) and multi-tenant reuse.
6. Safety & data handling — allowlist enforcement, risky/irreversible action
   handling, redaction of regulated financial data.
7. Code quality, then Communication (the REPORT.md write-up).

They explicitly do NOT reward feature breadth, framework name-dropping, or
building scaling infrastructure (queues, clusters, multi-tenant plumbing).
Depth on the load-bearing pieces beats breadth across everything.

## Key design decisions already made

### Target application: custom-built, deliberately hostile local bank app

Decision: build our own small local web app rather than use a real bank
system (explicitly forbidden in the brief, Section 4/9) or a clean public
demo site (too easy, doesn't exercise the interesting problems).

The target app should include:
- A non-trivial multi-step flow: search member → results → detail →
  read balance (mirrors their own example goal exactly).
- Deliberately hostile markup: nested tables, an iframe, non-semantic class
  names, no test IDs — but WITH accessibility roles/labels present (ARIA),
  since the perception strategy below depends on the accessibility tree
  being usable, same as it would be on a real legacy enterprise app.
- Seeded failure states, reachable on demand for evidence capture:
  - a member ID that returns "not found" (business outcome)
  - a member ID that returns "access denied" (business outcome)
  - a member ID that triggers a slow/hanging load (recoverable condition)
  - a member ID that triggers a surprise interstitial/dialog (recoverable
    condition, or escalation trigger depending on step)
- Optionally, a second "Tenant B" variant (same flow, different branding/
  labels/layout) if we pursue the cross-tenant stretch goal.

Rationale for rejecting a real bank app: explicitly forbidden by the brief;
would undercut every safety argument in the write-up; real credentials/PII
would land in evidence artifacts, which is the exact thing being scored
against. This is documented, not silently assumed — restate this reasoning
in REPORT.md Section 7 (Cuts) if asked "why not real/public target."

Considered and rejected: self-hosting Apache Fineract + Mifos X Community
App (real open-source core banking software) as a middle path. Valid
alternative if the custom app proves too limited, but decision was made to
proceed with the custom app for full control over failure injection and to
directly hit every stretch goal surface. Keep this option in mind if the
custom app's timeline balloons.

### Perception mechanism: accessibility tree primary, screenshot evidence, CSS fallback

Locators are ranked lists, tried in priority order:
1. Accessibility tree (role + accessible name) — most stable, matches how
   this would extend to legacy web AND desktop apps (Windows UIA exposes
   the same abstraction), which is the direct answer to the Heterogeneity
   section of REPORT.md.
2. Text label match — human-readable fallback.
3. CSS selector — last resort, most brittle, still logged and available.

Every locator attempt (success or failure, which strategy worked) gets
logged. This becomes the drift-detection signal later: if a capability
keeps falling through to its fallback locator, that's an early warning of
UI change before it becomes an outright failure.

### Language/stack: Python

- Playwright for browser automation (exposes accessibility snapshot,
  auto-waiting).
- Pydantic for typed, versioned, JSON-serializable artifact models —
  validation comes near-free and doubles as schema documentation.
- LLM: TBD — pick based on tool-use/function-calling quality and cost for
  a bounded discovery loop (max ~25 steps).

### Artifact schema — FINALIZED, see schema/example_artifact.json

Format: JSON. Full worked example for the `member_balance_lookup`
capability is at `schema/example_artifact.json` in this repo — read it
before implementing the Pydantic models; it is the reference for field
names and nesting.

Key structural decisions and why:

- **Per-step checkpoints**, not one checkpoint at the end. Chosen so replay
  failures are debuggable at the step level ("step 4 failed: expected a
  rendered results table, got a JS error") rather than only knowing the
  whole flow didn't finish.
- **Ranked locator fallback lists per step** (see Perception section above),
  not a single locator per step.
- **`risk_class`** declared once at the artifact level (`read_only` /
  `mutating` / etc.) — drives whether guardrails require confirmation
  before the capability runs at all.
- **`expected_outcomes`** declared up front at the artifact level, each with
  its own `detection` rule, and referenced from step checkpoints via
  `outcome_match`. This is the single most important idea in the schema:
  it is how "no such member" is recognized as a legitimate result rather
  than discovered/improvised as an error at replay time. Do not collapse
  this into generic try/catch error handling in the executor — the
  expected-outcome detection must be data-driven from the artifact.
- **`guardrails`** (allowlist routes, allowed action types, confirmation
  requirement) live on the artifact itself, not only in a global config —
  defense in depth, a capability can't run outside the routes it was
  actually discovered against even if the global config is misconfigured.
- **`evidence_policy`** declares what gets screenshotted on failure and
  which fields must always be redacted — structural, not left to
  "remember to scrub it in code."
- **`escalation_policy`**: artifact-level `default` policy (retry counts,
  what triggers escalation — checkpoint failure, unrecognized dialog, step
  timeout, hard failure) with optional per-step `escalation_override` for
  steps that need different behavior (e.g. a step on a mutating capability
  where any unexpected dialog should escalate immediately with zero
  retries, since it might be an unexpected confirmation prompt for an
  irreversible action). Chosen over (a) requiring an explicit policy on
  every step — too much repetitive boilerplate on an artifact meant to be
  human-reviewed — and (b) no artifact-level modeling at all — would be
  inconsistent with `risk_class` already existing on the artifact; if the
  artifact already knows a step is risky, it needs a way to act on that
  knowledge.

Discovery does NOT need to know about escalation policy at record time —
this only matters to replay running unattended in production. Reasonable
defaults apply automatically; authors only add `escalation_override` when
a specific step's risk profile genuinely differs from the default.

## Replay engine — built (agent/)

Implements the build order's step 3, before any agent/discovery code.
Module layout: `models.py` (Pydantic artifact + ReplayResult union),
`registry.py` (generic `Registry[F]`), `locators.py` / `checkpoints.py` /
`actions.py` (one `Registry` instance each, per the Perception section
above), `guardrails.py`, `escalation.py`, `operator_console.py`,
`evidence.py`, `context.py` (`RunContext` threaded through everything),
`engine.py` (`run_capability` orchestration), `replay.py` (CLI). Run:
`python -m agent.replay --capability <path> --input k=v [...]`.

Each step splits into an ACT phase (resolve locator + perform the
action) and a CHECK phase (unrecognized-dialog interrupt, then
checkpoint eval), each with its own retry/escalate handling — retries,
automatic or human-resumed, only ever redo CHECK, never re-run the
action. `text_present`'s `scope` is a contract with the target app's
`aria-label` regions (see target_app/README.md).

Cut, for REPORT.md: one process owns the Playwright browser end to end,
headed, no RPC. Escalation is a shared SQLite `runs` table
(`evidence/sessions/sessions.db`) — the replay process writes its paused
state and blocks polling it; `operator_console.py` is a separate,
minimal Flask process (status + screenshot + one Resume button) reading
the same table. If the replay process dies while paused, the browser
goes with it; a production version would separate the browser process
from the replay/orchestration process so a paused session survives an
orchestrator restart.

See BUILD_LOG.md for build history: what was verified against which
seeded target_app cases, bugs found, and how they were fixed.

## The result-outcome taxonomy (applies to the replay engine's return type)

Every replay run must resolve to exactly one of three categories. Getting
this distinction right/wrong is called out as "the most common design
mistake" in the brief's own glossary. Do not conflate any of these:

1. **Business outcome** — the automation worked correctly and reached a
   legitimate, expected end state that isn't the happy path. E.g. "member
   not found," "access denied." Detected via the artifact's
   `expected_outcomes`. Returned as a normal successful result, just with
   a different outcome code — never as an error/exception.
2. **Recoverable condition** — a known, handleable runtime blip: dismiss a
   known interstitial, wait out a slow load, retry a transient failure.
   Handled silently per the escalation_policy retry counts, execution
   continues.
3. **Hard failure** — something genuinely unexpected. Stop, and return
   enough to debug: which step, what locator/checkpoint was expected, what
   was actually observed, plus a screenshot per `evidence_policy`.

## Human escalation / control-transfer model

Requirement: when escalating, a human must take over the SAME live browser
session the automation was using (preserves login state, navigation
history, partially-filled forms) — not a fresh session.

Model: explicit session ownership state, e.g. `owner: automation | human`.
While `owner == human`, the automation loop blocks and does nothing. The
human works in that live window via a (can be minimal/mocked) operator
surface, then signals resume (e.g. hits a "done, resume" button), flipping
ownership back to `automation`, which then continues from the step it
paused on. Preserve context/evidence across the handoff; log what the
human did.

A full real-time co-browsing console is explicitly out of scope per the
brief — a bare/mocked operator UI is fine as long as the handoff mechanism
and control-transfer model are real and well-reasoned, which they are meant
to be the focus of, not the UI polish.

## Safety guardrails (Section 3.4 of the brief) — built

- Explicit, configurable allowlist of permitted domains/routes and
  permitted action types, enforced at the point of action (in the
  executor/action functions), not just suggested via prompt. See
  `agent/guardrails.py`.
- **Risk gating, built (`agent/engine.py`, `run_capability`)**: any
  capability with `risk_class` other than `read_only`, or
  `guardrails.requires_confirmation: true`, pauses for explicit human
  confirmation via the *same* escalation/operator-console path used
  mid-run — before its first step's action executes, not just described
  as a stance. Navigation to the entry point happens first, so the
  operator reviews real page context, not a blank browser.
  `member_balance_lookup` (`read_only`) is unaffected — the check is
  skipped entirely, verified with zero behavior change. Proven against a
  synthetic in-memory `risk_class: "mutating"` fixture: paused at step 0
  before any action was logged, resumed, then executed and completed.
  See BUILD_LOG.md for the verification transcript.
- **Redaction, built and verified against real sensitive data
  (`agent/redaction.py`)**: `evidence_policy.redact_fields` is enforced
  by scanning the *live page* for label cells matching a redact_fields
  entry (same label -> sibling-value convention as extraction) and
  masking the real value found there — not a guessed pattern. Applied at
  three points: `agent/perception.py`'s `observe()` masks discovery's
  full accessibility-tree dump at the source, so both the live model
  conversation and everything persisted from it are already masked;
  `agent/evidence.py`'s `StepLogWriter` rescans the page on every
  `log.jsonl` event as defense-in-depth for any raw text path that
  doesn't go through `observe()` (e.g. a checkpoint's
  `field_value_equals` readback); screenshots use Playwright's native
  `mask=` parameter to black out the sensitive region before the PNG is
  ever written. `target_app`'s member detail page carries a genuine
  unmasked account number (never a declared `member_balance_lookup`
  output) specifically to exercise this against real data — verified
  the raw value appears in neither the trajectory JSON, `log.jsonl`, nor
  the screenshot, only `[REDACTED]`. See BUILD_LOG.md.

## Deliverables — exact required structure, do not deviate

- `/README.md` — setup instructions (keys/config needed, how to run
  without live services if applicable), and the exact demo command(s):
  run the agent on a goal, then replay the resulting artifact.
- `/REPORT.md` — 1–3 pages, exactly these seven headings in this order:
  1. Architecture
  2. Artifact schema
  3. Determinism & error handling
  4. Heterogeneity & multi-tenant
  5. Escalation & handoff
  6. Safety
  7. Cuts
- `/evidence/` — a saved example artifact, logs from a discovery run AND a
  replay run, and ideally one replay that hits an error/exceptional state
  (bad input, not-found result, or injected failure) to demonstrate the
  outcome taxonomy actually works. Screen recording optional.

At least one discovery run must be a genuine LLM-driven run against a live
surface — this cannot be faked, described, or mocked. Everything else in
Section 3 of the brief can be stubbed at a clean, documented seam if time
runs short (operator console UI, desktop surface support, multi-tenant
infrastructure) — but the seam and reasoning must be real.

## Build order (agreed sequence)

1. Build the target app first (custom hostile bank app w/ seeded failures).
2. Design/finalize artifact schema on paper before writing agent code —
   DONE, see schema/example_artifact.json.
3. Build the replay engine before the agent loop — it defines what a "step"
   and "action" vocabulary means; the agent just needs to emit into that
   vocabulary. DONE, see "Replay engine — built (agent/)" above.
4. Build the discovery agent loop (tool-use style: observe/click/type/
   navigate/extract, each action gated through the guardrail check).
   DONE, see "Discovery agent — built (agent/)" below.
5. Build the artifact compiler — turns a successful discovery trajectory
   into a parameterized artifact (detect which typed values came from the
   goal's inputs and templatize them, e.g. `{{member_id}}`). DONE, see
   "Artifact compiler — built (agent/)" below.
6. Error handling, outcome taxonomy, escalation/handoff mechanism.
7. Safety pass, evidence capture, then REPORT.md.

## Discovery agent — built (agent/)

`agent/discover.py` (CLI) drives `agent/discovery.py`'s observe -> decide
-> act loop against the Anthropic Messages API (Claude Sonnet 5), one
tool call per turn (`tool_choice: "any"`, parallel tool use disabled) —
never free-form text parsed for intent. Perception is text, not a
screenshot: `agent/perception.py` wraps Playwright's
`locator("body").aria_snapshot()`, the same role+accessible-name
representation replay's locators are built from. Tool vocabulary
(`agent/discovery_tools.py`) matches `agent.actions.ACTION_REGISTRY`
(click/type/navigate/extract) plus `done`. Guardrails are enforced the
same way as replay (`agent/guardrails.py`, now taking primitives so both
callers share one code path), with one addition: a `click` that triggers
off-allowlist navigation is reverted, not just flagged, since discovery
is open-ended exploration where containing-and-continuing beats aborting
the run. Element resolution (click/type/extract, and replay's `name`
locator strategy) matches on exact accessible name, not substring — this
app's nested-table cells compute compound names from concatenated
descendant text, so substring matching makes every leaf value
structurally ambiguous against its own ancestor wrappers. `done` only
accepts `output_names` referencing outputs already captured via a
successful `extract` — never raw values — so an unverified value can
never reach an artifact's outputs. Output: a `Trajectory`
(`agent/trajectory.py`) — raw discovery material, not a compiled
artifact; run: `python -m agent.discover --goal "..." --target <url>
--out <path>`.

See BUILD_LOG.md for build history, including a security gap found and
fixed during this phase (route allowlist checking ignored origin).

## Artifact compiler — built (agent/)

`agent/compile.py` (CLI) drives `agent/compiler.py`'s
`compile_trajectory()`, which merges a discovery `Trajectory` with an
authored `agent/policy.py` `PolicySpec` (`--policy` can just be a full
`Capability` JSON — e.g. `schema/example_artifact.json` — extra fields
are ignored) into a final `Capability`. The split is strict: the
mechanical layer (steps, locators, checkpoints, inputs, outputs,
target) comes entirely from the trajectory and `--param` flags; the
policy layer (`expected_outcomes`, `guardrails`, `escalation_policy`,
`risk_class`, `capability_id`/`description`/`version`) comes entirely
from `--policy`. Neither overrides the other. Run: `python -m
agent.compile --trajectory <path> --policy <path> --param k=v [...]
--out <path>`.

Two things worth knowing about how the mechanical layer is built:

- **Checkpoints reference policy's `expected_outcomes`, even though
  they're mechanically derived.** Every non-final step's checkpoint is
  `any_of: [element_visible(next successful action's locator),
  outcome_match(<all policy-declared outcome codes>)]` — found necessary
  when the first compiled artifact hung forever replaying `10002`/`99999`,
  because a plain `element_visible` checkpoint has no way to recognize a
  business outcome as anything but a failure. Referencing codes policy
  already declares isn't guessing; leaving them unreferenced just meant
  the two layers were merged into one file without actually being wired
  together. See BUILD_LOG.md.
- **No `css` locator strategy is ever synthesized** — only
  `accessibility` + `text_label`, exactly what discovery verified.
  Deliberate cut: a css fallback would require re-visiting the live page
  at compile time (DOM re-walk), which this compiler doesn't do. Note
  for REPORT.md's Cuts section.

Per-step `escalation_override` is never carried over from the policy
file's own steps either (steps are 100% mechanical) — a compiled
artifact relies entirely on the policy's artifact-level default for
every step, even ones a hand-authored artifact might override
differently.

## Stretch goal: agent-facing capability API — built (agent/), the only stretch goal taken on

`agent/capability_api.py` (Flask, port 8200, same pattern as
`operator_console.py`) — `GET /capabilities` scans `schema/capabilities/`
only (not `schema/example_artifact.json`, which stays put as schema
documentation — excluded by directory boundary, not inferred, so editing
it later can't silently change what the catalog serves) and returns
`capability_id`/`description`/`version`/`risk_class`/`inputs`/`outputs`
for each. `POST /capabilities/<id>/invoke` takes a flat JSON params
object, calls `engine.run_capability()` unchanged, and returns the exact
`ReplayResult` JSON body with no translation layer — `200` for
`success`/`business_outcome`, `500` for `hard_failure`, `400`/`404` for
bad input / unknown capability.

**Demonstration-scale only, deliberately**: no auth, no queueing, one
synchronous headed-browser run per invoke, no rate limiting. The brief
explicitly doesn't reward prematurely-built scaling infrastructure —
restate this in REPORT.md's Cuts section if asked why there's no queue
or worker pool here.

`schema/capabilities/` is now the conventional home for compiled
artifacts meant to be served/invoked — `agent/compile.py --out` should
target it going forward.

## Open / not yet decided

- Compiler phase: the compiled artifact's `guardrails.allowlist_routes`
  should be narrowed to the routes actually visited during the
  successful trajectory, not carried over as discovery-time's wide-open
  `"/*"` — discovery needs room to explore, but a replayable capability
  should only be allowed back onto the exact routes it was proven
  against.
- Whether to fall back to self-hosted Apache Fineract/Mifos as target app
  if the custom app's scope grows too large (see Target application above).