# Builds the runtime for target_app and operator_console only — the two
# genuinely browser-free Flask services (see docker-compose.yml and
# CLAUDE.md's Docker section). discover.py, replay.py, and
# capability_api.py all drive a headed Playwright browser via
# agent.engine.run_capability() and are intentionally not containerized
# here; they run on the host against these containerized services.
#
# Source code is not copied into the image — docker-compose.yml bind-mounts
# the repo root at /app at runtime, so the container always runs whatever
# is on disk and shares evidence/sessions/sessions.db with host-run
# processes without any path translation.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
