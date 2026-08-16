# Evidence index

A curated, labeled subset of what discovery and replay produce on every
run (the full raw output lives in the gitignored `evidence/runs/` and
`evidence/sessions/`, recreated by re-running the commands in the root
`README.md`). Each section below is what it is and which specific
requirement it demonstrates. Full reasoning and every bug found along
the way — including two found while assembling this evidence — is in
`BUILD_LOG.md` at the repo root.

## `discovery/`

The one genuine, LLM-driven discovery run required by the brief — this
cannot be faked, described, or mocked. Claude Sonnet 5, driving a real
headless Chromium via Playwright, given only the goal *"Look up member
10001 and read their name and savings balance"* and a URL, with no
scripted steps. `trajectory.json` is its raw output (the exact artifact
`schema/capabilities/member_balance_lookup.compiled.json` was compiled
from — see `source_run_id`); `log.jsonl` is the structured per-event log;
`screenshots/` is one masked screenshot per step. This run also doubles
as the redaction proof below, since it's the run that reaches the
member detail page.

## `artifacts/`

`member_balance_lookup.hand_authored.json` (the schema design written
before any code existed) and `member_balance_lookup.compiled.json` (the
same capability, produced mechanically from `discovery/trajectory.json`
by `agent/compile.py`) side by side. The artifact schema is called out
in the brief as a focal point of the evaluation — this pair is what
lets a reviewer compare the intended design against what a real
discovery run actually produces without cross-referencing two
directories.

## `replays/`

Four replays of the same compiled capability against four different
inputs, run with the LLM completely out of the decision loop — this is
the "replay artifact deterministically" half of the assignment, and
each one lands in a different branch of the three-way outcome
taxonomy the brief calls out as the most common design mistake to get
wrong.

- **`success_10001/`** — golden path. `result.json`'s `status: "success"`
  with real extracted outputs (`member_name`, `savings_balance`).
- **`business_outcome_access_denied_10002/`** — a legitimate, expected
  non-happy-path result (`outcome_code: "ACCESS_DENIED"`), returned as a
  normal successful result, not an error. Proves `expected_outcomes`
  detection works, not generic try/catch.
- **`business_outcome_not_found_99999/`** — same taxonomy branch, a
  different code (`outcome_code: "MEMBER_NOT_FOUND"`), from a genuine
  zero-row SQL lookup rather than a fixed ID list.
- **`escalation_10004/`** — the recoverable/escalation branch, and the
  human-handoff proof. Member `10004` triggers an unexpected in-page
  dialog; the run pauses (`log.jsonl`: `escalate`,
  `trigger: "on_unrecognized_dialog"`), `operator_console_pause.jpg` is
  the actual operator console mid-pause (screenshot embedded, Resume
  button live, redacted account number already visible), a human
  dismisses the dialog *in that same live browser session* (not a fresh
  one — same URL, same DOM, same login state) and clicks Resume, and the
  run completes for real: `result.json`'s `status: "success"`,
  `member_name: "Pat Whitfield"`. This is the human-in-the-loop
  escalation requirement — control transfers over the same live session,
  not a TODO.

## `redaction/`

Proof that `evidence_policy.redact_fields` is enforced against a real
sensitive value on a real page, not a guessed pattern. `target_app`'s
member detail page carries a genuine account number that is never a
declared capability output, specifically to exercise this.
`masked_screenshot.png` shows a solid black block over the Account
Number row while Name and Savings Balance stay fully legible.
`trajectory_excerpt.json` and `log_excerpt.jsonl` show `[REDACTED]`
in place of the real value in the exact text the model saw and the
exact text that got persisted — masking happens at the observation
source (`agent/perception.py`), not only at a later logging step. The
real value appears in neither file (spot-checked with `grep` against
both before curation).

## `risk_gating/`

Proof that a capability's `risk_class` gates execution before its first
action runs, using a synthetic in-memory `risk_class: "mutating"`
fixture (not a shipped capability — constructed directly via the
Pydantic models to exercise the gate in isolation). `log.jsonl` shows
`risk_gate_pause` before any `locator_resolved`/`action` event exists —
proof the pause happens before the first action, not just before its
result — then `risk_gate_resumed` followed by the action actually
executing. `operator_console_pause.jpg` is the live console showing the
real target page (navigation happens before the gate, so the operator
reviews real context, not a blank browser) with the confirmation still
pending. `result.txt` is the final `success` after resume.
`member_balance_lookup` (`risk_class: "read_only"`) is unaffected by
this gate — see `replays/success_10001/log.jsonl`, which has no
`risk_gate_pause` event at all.

## `capability_api/`

Evidence for the one stretch goal taken on: the agent-facing HTTP
interface (`agent/capability_api.py`). `catalog.json` is a real
`GET /capabilities` response listing `member_balance_lookup` by scanning
`schema/capabilities/`. `invoke_10001.json` and `invoke_10002.json` are
real `POST /capabilities/member_balance_lookup/invoke` responses — the
success and business-outcome cases — returned as the exact
`ReplayResult` JSON body engine.py produces, no translation layer.
Deliberately demonstration-scale: no auth, no queueing, one synchronous
headed-browser run per invoke (see CLAUDE.md's Cuts notes and
REPORT.md).
