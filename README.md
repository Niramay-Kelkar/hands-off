# hands-off

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
4. **Escalate.** If the agent gets stuck during discovery, or replay hits
   a condition it cannot recover from, control of the live session is
   handed to a human operator, who can act and then hand control back.

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

## Demo path

Run the agent on a goal (discovery):

```bash
python -m agent.discover \
  --goal "look up member 12345 and read their current savings balance" \
  --target http://localhost:8000/members \
  --out schema/member_balance_lookup.json
```

Replay the resulting artifact with new inputs:

```bash
python -m agent.replay \
  --capability schema/member_balance_lookup.json \
  --input member_id=67890
```

Replay against a seeded failure case, to see outcome/error handling:

```bash
python -m agent.replay \
  --capability schema/member_balance_lookup.json \
  --input member_id=99999   # seeded as "not found" in the target app
```

Logs and screenshots from both runs are written to `/evidence`.

## Running without live services

The discovery step requires a live LLM API key. Replay does not call the
LLM at all and can run entirely offline against the local target app, once
an artifact has already been recorded. If you want to inspect the system
without any API key, run the target app locally and use `agent.replay`
against the example artifact in `schema/example_artifact.json`.

## Project structure

```
hands-off/
  agent/          discovery loop, replay engine, artifact schema/models
  target_app/     the local demo bank application being automated
  schema/         saved capability artifacts (JSON)
  evidence/       logs and screenshots from discovery and replay runs
  tests/
  CLAUDE.md       project context and design decisions
  REPORT.md       design write-up (architecture, schema, error handling, etc.)
  README.md       this file
```

## Design write-up

See [REPORT.md](./REPORT.md) for architecture, the artifact schema
rationale, determinism and error handling, heterogeneity and multi-tenant
design, escalation and handoff, safety, and what was cut and why.
