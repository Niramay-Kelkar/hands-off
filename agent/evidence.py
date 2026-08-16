"""Per-run evidence: structured step log (JSONL, redacted), screenshots,
and the run directory layout under evidence/runs/<run_id>/.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVIDENCE_ROOT = Path("evidence")
RUNS_ROOT = EVIDENCE_ROOT / "runs"


def new_run_id(capability_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{capability_id}_{ts}_{uuid.uuid4().hex[:6]}"


def run_dir(run_id: str) -> Path:
    d = RUNS_ROOT / run_id
    (d / "screenshots").mkdir(parents=True, exist_ok=True)
    return d


def redact(data: Any, redact_fields: set[str]) -> Any:
    if isinstance(data, dict):
        return {
            k: ("[REDACTED]" if k.lower() in redact_fields else redact(v, redact_fields))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact(v, redact_fields) for v in data]
    return data


class StepLogWriter:
    """Appends one JSON object per line, redacting known-sensitive field
    names on the way out per the artifact's evidence_policy.redact_fields —
    a structural redaction pass, not something callers have to remember."""

    def __init__(self, path: Path, redact_fields: list[str]):
        self._path = path
        self._redact_fields = {f.lower() for f in redact_fields}

    def event(self, kind: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **redact(fields, self._redact_fields),
        }
        with self._path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")


def save_screenshot(page, run_dir: Path, step_id: int, reason: str) -> str:
    path = run_dir / "screenshots" / f"step_{step_id}_{reason}.png"
    page.screenshot(path=str(path))
    return str(path)
