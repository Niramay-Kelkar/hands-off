# MERIDIAN CORE Adaptation — Technical Report

## Overview

The original take-home built a computer-use automation core: an LLM discovers a
task once against a live UI, that trajectory is compiled into a typed, versioned
capability artifact, and the artifact is later replayed deterministically with no
LLM in the decision loop, with per-step checkpoints, data-driven expected
outcomes, safety guardrails, and human escalation that transfers control of the
*same* live browser session. This adaptation points that same core at a new,
unfamiliar target — MERIDIAN CORE (`web-sample.interface-hiring.com`), a
credit-union member-servicing console — to demonstrate that the core is reusable
as a config/adapter surface rather than something that has to be re-written per
target. Scope: six required capabilities (Sign On; Member Balance/Inquiry,
combined per the take-home's own precedent; Funds Transfer; Place Account Hold;
Open New Share; Update Member Information), all against the live remote target,
plus three new surfaces on top of the core — an HTTP capability API, a
tool-calling chatbot front-end, and a read-only evidence dashboard. The demo runs
end-to-end from a single local page (`localhost:8200/app`, Chat and Dashboard
tabs): a request in plain English is routed to a capability, executed against the
live target, and the resulting run — every step, timing, DOM snapshot, and
screenshot — is inspected in the dashboard.

## Adaptation Changes (Core Loop Only — No Rewrites)

### New config and strategies (additive, not rewrites)

- **`label_proximity` locator strategy** (`agent/locators.py`). MERIDIAN's
  form inputs have no accessible name and no `<label for>` association — worse
  markup than the take-home's `target_app`. The new strategy scopes to the
  `<tr>` containing a label-bearing cell and targets the first
  `input/select/textarea` in that row, reusing the label→sibling-value
  convention redaction/extraction already relied on, applied to *filling*
  instead of reading. `LocatorStrategyModel` is already `extra="allow"`, so no
  schema change. Verified purely additive: `target_app` replay
  (`member_id=10001`) still passes unchanged. (PROMPT_LOG Phase 0/1.)
- **Column-header redaction path** (`agent/redaction.py`). Share ID on MERIDIAN
  is a *column header* over a ~27-row table, not a row `label:value` pair, so
  the existing detector silently found zero matches. Added a second detection
  path: match a header cell against `redact_fields`, then mask every row's cell
  at that column position, emitting the same `SensitiveField` dataclass so
  `mask_text()` and the screenshot `mask=` pipeline are untouched. A later
  regression (this path structurally mistaking a 2-column form table for a data
  grid) was found during re-verification and fixed by requiring 3+ header
  columns — real "safety fixes need their own regression tests" material.
  (PROMPT_LOG Phase 1, Phase 2.)
- **Money-pattern locator fallback in the compiler** (`_money_locator_for()`,
  `agent/compiler.py`). When an extract field has no resolvable label but its
  discovered value is money-shaped, the compiler now emits a
  `name_matches: "^\$[\d,]+\.\d{2}$"` locator instead of pinning the literal
  dollar figure. See bug #2.
- **`<select>` fallback in `agent/actions.py`**. `_select` now tries an exact
  (normalized) option-label match first, then a ranked partial match
  (exact → prefix → substring) against the live `<option>` list — needed
  because MERIDIAN share dropdowns are keyed by full visible label
  (`"100234-MMKT-9 - Money Market"`), not the bare share ID. (PROMPT_LOG
  Phase 5.)
- **Recursive catalog glob** (`agent/capability_api.py`). `GET /capabilities`
  scanned only `schema/capabilities/*.json`; the MERIDIAN artifacts live in
  `schema/capabilities/meridian/`, so all six were invisible. Fixed to also
  scan one level of subdirectory. `schema/example_artifact.json` stays excluded
  by directory boundary. (commit `30d9831`.)
- **Approval gating**. Created `*.approval.json` (`status: "approved"`) for all
  six MERIDIAN capabilities — none existed, so all six defaulted to draft and
  returned `403` from `POST /invoke`. (commit `f7e8b41`.)

### What stayed fixed (re-verified, not assumed)

- **`engine.py` risk-gate logic** — `risk_class != "read_only" OR
  requires_confirmation` — byte-identical to the take-home (`git diff` empty).
  One prompt mistakenly changed this to key off `requires_confirmation` alone;
  it was flagged as weakening defense-in-depth and reverted. All four mutating
  capabilities were then re-verified pausing identically at step 0
  (`pause_reason: risk_confirmation_required`, `owner: human`), captured live
  from `sessions.db` *before* resuming. (PROMPT_LOG Phase 2, "Engine.py
  revert".)
- **Escalation lifecycle** — pause → `sessions.db` paused row + screenshot →
  human takes over the *same* live browser → resume via the unmodified
  `agent.operator_console` `/resume/<run_id>` endpoint. Zero changes for the
  new target. Verified with a real permission escalation (see Demo 3).
- **Redaction detection/masking** — the three layered paths (perception-time
  masking at source, `StepLogWriter` re-scan as defense in depth, native
  Playwright screenshot `mask=`) are preserved; the new target only added
  detection *inputs*, not new masking mechanics.
- **Evidence capture and `sessions.db` run tracking** — unchanged; the
  dashboard reads this existing evidence, it does not produce it.

## Bugs Found & Fixed (6 real findings)

1. **Discovery-time redaction gap.** Discovery-time redaction used a hardcoded
   field list independent of a capability's own `redact_fields` (discovery
   necessarily runs before any policy exists), leaking email/phone/address/Share
   IDs into discovery evidence. *Root cause:* one fixed list, no parameter.
   *Fix pattern:* additive `--redact-fields` flag, merged with — not replacing —
   the hardcoded baseline. Verified against specific named values across
   `trajectory.json`, `log.jsonl`, and the saved screenshot, including
   deliberately checking the *first* Share ID row to rule out a
   dict-collapse-style bug. (PROMPT_LOG Phase 1; commit `05d0ce6`.)
2. **Stale literal-value locator on balance.** `meridian_member_balance_inquiry`
   compiled the balance output as `role="cell", name="$1,500.00"` — the exact
   value seen at discovery time. The live balance drifted to `$2,499.00` on the
   shared external site, so the locator could never resolve: every invoke
   hard-failed → escalated → resume re-hit the identical broken locator →
   escalated again, indefinitely. *Root cause:* `label_for_value()` only
   resolves the 2-column `label:`/sibling convention; the balance sits in a
   column-header data table, so it correctly returns `None`, and the compiler
   had no fallback but the literal value. *Fix pattern:* `_money_locator_for()`
   emits a money-shaped regex locator for label-less money values; `.first`
   reproduces "whichever cell discovery read" without pinning the number.
   Recompiled, invoked live, returned the current balance with no hang.
   **This is the second independent occurrence of the pinned-literal-instead-of-label
   bug class** (the first was `member_name` in Phase 1) — a known edge in the
   label-detection convention (it does not cover column-header tables), not a
   one-off. An audit of the other five artifacts found no third occurrence.
   (PROMPT_LOG Phase 4; commit `b17f64d`.)
3. **Typed-secret leakage in evidence.** The literal typed password had been
   leaking unmasked into every capability's trajectory/log evidence since the
   first capability — structurally different from every prior redaction fix,
   which masked values *read from* the page; this is a value the model itself
   *typed*, echoed back as a tool-call argument. *Fix pattern:* mask
   `type`/`select` tool-call values by their target-field label, and track
   typed secrets so later free text (e.g. a `done` summary) is also masked.
   (PROMPT_LOG Phase 2, "Open New Share + Update Member Information".)
4. **Per-field placeholder collision.** With multiple maskable fields in one
   run, the compiler's exact-match templatizing broke on a bare `"[REDACTED]"`
   (every redacted param looked identical). *Fix pattern:* per-field
   placeholders (`[REDACTED:password]`, etc.), now a required `--param`
   convention at compile time. Confirmed the three earlier artifacts predate the
   typed-secret masking entirely, so the collision never applied to them.
   (PROMPT_LOG Phase 2, close-out.)
5. **Missing post-step escalation override.** `open_new_share.policy.json`
   lacked the post-step `escalation_override` (retry: 0, then: escalate) that
   `funds_transfer` and `place_account_hold` carry for timeout handling after
   the point of no return. *Fix pattern:* added to match the pattern,
   recompiled, diffed, re-verified. (PROMPT_LOG Phase 3; commit `9962419`.)
6. **Label normalization mismatch.** The row-based redaction path joined the
   page label with underscores before comparison but never normalized
   `redact_fields` entries the same way, so any multi-word entry (e.g. the
   confirmation page's actual label, "New Share ID:") could never match —
   invisible until a multi-word label first appeared. *Fix pattern:* a shared
   `normalize_label()` applied consistently to both sides. (PROMPT_LOG Phase 2,
   "Open New Share".)

## Known Constraints & Decisions

- **`?inject=<kind>` is architecturally unreachable for post-sign-on stages.**
  The parameter fires only on the specific request that carries it and does not
  persist through redirects or the session. All six capabilities navigate to
  the entry point once (`engine.py`'s initial `page.goto()`), then operate
  entirely via UI clicks — so `?inject=validation/timeout/maintenance/server`
  reaches only the sign-on page. Verified live: `/signon?inject=validation`
  completes cleanly with no downstream effect. Correct policy config for these
  states is in place, but proving them live would require either `engine.py`
  changes or in-flow templatized navigate steps — not defensible this close to
  the demo for states that are not the ones that matter. The states that *do*
  matter — permission escalation and the natural business outcomes — are
  verified live (below).
- **Idle-session timeout** is open for the same reason: a genuine idle wait
  cannot be simulated, and `?inject=timeout` only models an already-expired
  session on the entry request.
- **UI badges are not server truth.** MERIDIAN's OPEN/HOLD share badges do not
  reliably predict server-side eligibility (two OPEN-badged shares were rejected
  as HOLD). Decision: a policy-authoring rule — detect outcomes only from
  post-attempt server response text, never from pre-attempt UI state — not a
  code change. `SHARE_ON_HOLD` is detected via `text_present` against the real
  rejection message.

## Safety & Escalation (Phase 7 Guarantee)

- **Risk-gate enforcement by construction.** Every surface — CLI, `POST
  /invoke`, and the chatbot — routes through the single `run_capability()`
  entry point, so the pre-action pause on any non-`read_only` capability cannot
  be bypassed by adding a new front-end. Re-verified on all four mutating
  MERIDIAN capabilities after the `engine.py` revert.
- **Two-path pause handling — the integrity safeguard.** The chatbot
  distinguishes two pause types and never conflates them:
  - *Proactive risk-gate pause* (`pause_reason: risk_confirmation_required`):
    the bot asks the user in plain language, summarising exactly what will
    happen; an explicit "yes" drives the *real* `operator_console`
    `/resume` endpoint — the same call a human operator would click.
  - *Reactive escalation* (any other pause reason, e.g. a supervisor-only
    permission wall): the bot reports the rejection and stops. It never resumes
    a reactive escalation itself and points the user to the operator console.
- **Redaction survives the new read-only surface.** Passwords render as
  `[REDACTED]` in evidence viewed through the dashboard — verified live.
- **Escalation/handoff is reachable through the chatbot**, not only the CLI —
  demonstrated end-to-end (Demo 3).

## Demo Flow (How It All Integrates)

Single entry point: `localhost:8200/app` — unified page, Chat and Dashboard tabs.

- **Demo 1 — read-only.** "What's the balance for member 100234?" → the chatbot
  replies with name and balance. Switch to the Dashboard tab → the run appears
  at the top of Run history → drill in: ~16 events, per-event `+Δs` timings, DOM
  snapshots, final outcome.
- **Demo 2 — mutating + inline confirmation.** "Open a new share for member
  100234." → proactive risk-gate pause → the bot asks "confirm?" with a summary
  → user types "yes" → resume via `operator_console` → completes with a real
  confirmation number.
- **Demo 3 — permission escalation.** "Place a hold on a share as a
  non-supervisor." → proactive pause → confirm → resume → steps 1–11 run clean
  → step 12 hits a real `403` supervisor-override wall → the bot reports the
  rejection, does **not** attempt to resolve it, and directs the user to the
  operator console, where a supervisor can sign on within the same live session
  and complete the hold.

## What Was Deliberately Cut

- Live testing of idle-session timeout.
- Live testing of the unreachable injected states
  (validation/timeout/maintenance/server) — policy is authored and ready.
- Dashboard persistence, auth, and any write operations — it is read-only
  forensics, deliberately separate from `operator_console` (live session
  control).
- Chatbot streaming and multi-user/shared state — in-memory per-session history
  only.

## What's Next

If this were headed to production (it is a proof of concept):

- Remote escalation notification (page/Slack/email on pause) rather than a
  human watching a local console.
- Per-session audit logging — who approved which invocation, against which
  confidence snapshot, when.
- Per-tenant PII policy customization, and narrowing `allowlist_routes` to the
  routes actually visited by each successful trajectory.
- Multi-operator coordination for concurrent escalations, and separating the
  browser process from the orchestrator so a paused session survives an
  orchestrator restart.
