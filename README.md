# hands-off

[![Replay smoke test](https://github.com/Niramay-Kelkar/hands-off/actions/workflows/replay-smoke-test.yml/badge.svg)](https://github.com/Niramay-Kelkar/hands-off/actions/workflows/replay-smoke-test.yml)

A computer-use automation system that gives AI agents hands inside legacy
back-office software that has no API. An LLM discovers how to complete a
task once by driving a real UI, that discovery is compiled into a typed,
versioned, replayable capability artifact, and that artifact is later
replayed deterministically, with no LLM in the decision loop, complete
with error handling, safety guardrails, and human escalation.

Built for the interface.ai take-home assignment. Full design reasoning is
in [REPORT.md](./REPORT.md).

## How it works

1. **Discovery.** Give the agent a goal in plain English and a starting
   point. It observes the screen, decides on an action, performs it, and
   repeats until the goal is met. This run is slow and non-deterministic,
   and it happens once per capability.
2. **Compile.** The successful run is turned into a structured artifact:
   typed inputs and outputs, ordered steps with ranked locator strategies,
   per-step checkpoints, declared business outcomes, and a safety and
   escalation policy. See [schema/example_artifact.json](./schema/example_artifact.json)
   for a worked example.
3. **Replay.** Given the artifact and a new set of inputs, the same flow
   runs again with no model involved in any decision. Fast, cheap, and
   repeatable. It detects and reports runtime conditions such as
   validation errors, "not found" results, and permission denials as
   distinct outcomes rather than crashes.
4. **Escalate.** If replay hits a condition it cannot recover from —
   an unrecognized dialog, a checkpoint failure past its retry budget —
   control of the live session is handed to a human operator, who can
   act and then hand control back. Discovery does not currently
   escalate: if a discovery run exhausts its step budget without
   reaching the goal, it terminates with a distinct status instead of
   pausing for a human (see REPORT.md Section 5).

## Setup

### Requirements

- Python 3.11+
- An API key for the LLM used in discovery (see `.env.example`)

### Install

```bash
git clone https://github.com/<your-username>/hands-off.git
cd hands-off
python3 -m venv venv
source venv/bin/activate   # venv\Scripts\activate on Windows
pip install -r requirements.txt
playwright install chromium
cp .env.example .env       # then fill in your API key
```

### Run the target app locally

```bash
python -m target_app.server
# serves at http://localhost:8000
```

### Quick start via Docker

`target_app`, `target_app_tenant_b`, and `operator_console` are plain
Flask processes with no browser dependency, so they're the pieces that
make sense to containerize. `agent.discover`, `agent.replay`, and
`agent.capability_api` all drive a real, visible Playwright browser via
`agent.engine.run_capability()` — that needs X11/VNC forwarding to run
in a container, which isn't worth the complexity here, so those three
stay on the host (venv setup above) and talk to the containerized
services over `localhost`.

```bash
docker compose up -d --build
# target_app:          http://localhost:8000
# target_app_tenant_b: http://localhost:8001
# operator_console:    http://localhost:8100
```

`target_app_tenant_b` is a second, differently-branded app (same
search -> results -> detail flow, different company/CSS/markup) used to
test cross-tenant reuse — see `target_app_tenant_b/README.md` and
REPORT.md Section 4. It runs on its own port, concurrently with
`target_app`, not in place of it.

All three containers bind-mount the repo root, so they share
`target_app/members.db`, `target_app_tenant_b/members_b.db`, and
`evidence/sessions/sessions.db` with whatever you run on the host — no
separate seeding or config needed. If a `members.db` doesn't exist yet
(gitignored, generated data), seed it once via either environment:

```bash
docker compose run --rm target_app python -m target_app.seed
docker compose run --rm target_app_tenant_b python -m target_app_tenant_b.seed
# or, with the host venv active: python -m target_app.seed / python -m target_app_tenant_b.seed
```

Bring the containers down with `docker compose down`.

**Pointing a replay at either tenant:** `agent.replay --capability` reads
the target from the artifact file itself (`target.entry_point`), not
from a flag — the guardrail's allowed origin is derived from that same
field, so it can't be overridden on the command line. Replaying against
`target_app` (8000) needs no change:

```bash
python -m agent.replay --capability schema/capabilities/member_balance_lookup.compiled.json --input member_id=10001
```

Replaying the *same, unmodified* artifact against `target_app_tenant_b`
(8001) means pointing at a copy with only `target.entry_point`/
`target.app` repointed at `localhost:8001` (the mechanical
steps/locators/checkpoints are otherwise byte-identical); see
`BUILD_LOG.md`'s cross-tenant entries for exactly how that copy is made.
That replay — the same compiled `member_balance_lookup` artifact,
recorded once against tenant A, replayed unmodified against a
differently-branded tenant B with zero code or artifact changes and
identical locator/checkpoint/escalation behavior — is the cross-tenant
demo; see REPORT.md Section 4 for the full result.

## Demo path

With the target app running (see above), in another terminal:

Run the agent on a goal (discovery — requires `ANTHROPIC_API_KEY`, writes
the raw trajectory, not yet a replayable artifact):

```bash
python -m agent.discover \
  --goal "look up member 10001 and read their name and current savings balance" \
  --target http://localhost:8000/members \
  --out evidence/runs/demo_trajectory.json
```

Compile the trajectory into a replayable capability artifact. The
mechanical layer (steps/locators/checkpoints/inputs/outputs) comes from
the trajectory; the policy layer (risk_class, expected_outcomes,
guardrails, escalation_policy) comes from `--policy`, here the
hand-authored reference schema:

```bash
python -m agent.compile \
  --trajectory evidence/runs/demo_trajectory.json \
  --policy schema/example_artifact.json \
  --param member_id=10001 \
  --out schema/capabilities/member_balance_lookup.compiled.json
```

Replay the compiled artifact with new inputs — no LLM involved:

```bash
python -m agent.replay \
  --capability schema/capabilities/member_balance_lookup.compiled.json \
  --input member_id=67890
```

Replay against a seeded failure case, to see outcome/error handling:

```bash
python -m agent.replay \
  --capability schema/capabilities/member_balance_lookup.compiled.json \
  --input member_id=99999   # seeded as "not found" in the target app
```

To see the human escalation path, start the operator console in a third
terminal:

```bash
python -m agent.operator_console   # serves http://localhost:8100
```

then replay against `member_id=10004` (seeded interstitial dialog). The
run pauses and blocks; dismiss the dialog in the same live browser
window and click Resume in the console to let it continue.

Logs and screenshots from every run are written to `evidence/runs/` and
`evidence/sessions/`. A curated, labeled set of example output already
lives in `/evidence` — see `evidence/README.md` for what each piece
demonstrates.

### Agent-facing API (stretch goal)

```bash
python -m agent.capability_api   # serves http://localhost:8200
```

`GET /capabilities` lists compiled artifacts in `schema/capabilities/`;
`POST /capabilities/<id>/invoke` runs one with a flat JSON params body.
See CLAUDE.md for details.

## Running without live services

The discovery step requires a live LLM API key. Replay does not call the
LLM at all and can run entirely offline against the local target app, once
an artifact has already been recorded. If you want to inspect the system
without any API key, run the target app locally and use `agent.replay`
against either the hand-authored `schema/example_artifact.json` or the
already-compiled `schema/capabilities/member_balance_lookup.compiled.json`.

## Project structure

```
hands-off/
  agent/                   discovery loop, replay engine, compiler, artifact
                            schema/models, operator console, capability API
  target_app/               the local demo bank application being automated
  target_app_tenant_b/      second, differently-branded app used for the
                            cross-tenant replay test (see its README.md and
                            REPORT.md Section 4); binds the same port as
                            target_app, run one or the other, not both
  schema/
    example_artifact.json   hand-authored reference artifact (schema docs)
    capabilities/            compiled artifacts served by agent.capability_api
  evidence/                 curated example output (see evidence/README.md);
                            raw runs/sessions from your own use are gitignored
  CLAUDE.md                 project context and design decisions
  BUILD_LOG.md              session-by-session build history
  REPORT.md                 design write-up (architecture, schema, error handling, etc.)
  README.md                 this file
```

## Design write-up

See [REPORT.md](./REPORT.md) for architecture, the artifact schema
rationale, determinism and error handling, heterogeneity and multi-tenant
design, escalation and handoff, safety, and what was cut and why.
