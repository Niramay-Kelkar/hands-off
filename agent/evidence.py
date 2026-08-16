"""Per-run evidence: structured step log (JSONL, redacted), screenshots,
and the run directory layout under evidence/runs/<run_id>/.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import redaction

EVIDENCE_ROOT = Path("evidence")
RUNS_ROOT = EVIDENCE_ROOT / "runs"


def new_run_id(capability_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{capability_id}_{ts}_{uuid.uuid4().hex[:6]}"


def run_dir(run_id: str) -> Path:
    d = RUNS_ROOT / run_id
    (d / "screenshots").mkdir(parents=True, exist_ok=True)
    return d


def redact(data: Any, redact_fields: set[str], sensitive_values: dict[str, str] | None = None) -> Any:
    """Two passes: known-sensitive dict KEYS get masked outright (e.g. a
    literal {"password": "..."} field), and any string containing a
    known-sensitive VALUE (found live on the page — see agent.redaction)
    gets that substring masked, even embedded in free text like an
    accessibility-tree dump or an error message."""
    sensitive_values = sensitive_values or {}
    if isinstance(data, dict):
        return {
            k: ("[REDACTED]" if k.lower() in redact_fields else redact(v, redact_fields, sensitive_values))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact(v, redact_fields, sensitive_values) for v in data]
    if isinstance(data, str):
        for value in sensitive_values.values():
            if value:
                data = data.replace(value, "[REDACTED]")
        return data
    return data


class StepLogWriter:
    """Appends one JSON object per line, redacting known-sensitive field
    names AND known-sensitive values on the way out per the artifact's
    evidence_policy.redact_fields — a structural redaction pass, not
    something callers have to remember. If a page is attached, every
    event rescans it for currently-visible sensitive fields (cheap at
    this scale) so this covers every existing log.event() call site
    without those call sites needing to know about redaction at all."""

    def __init__(self, path: Path, redact_fields: list[str], page: Any = None):
        self._path = path
        self._redact_fields = {f.lower() for f in redact_fields}
        self._redact_fields_list = list(redact_fields)
        self._page = page

    def attach_page(self, page: Any) -> None:
        self._page = page

    def event(self, kind: str, **fields: Any) -> None:
        sensitive_values: dict[str, str] = {}
        if self._page is not None and self._redact_fields_list:
            for f in redaction.find_sensitive_fields(self._page, self._redact_fields_list):
                sensitive_values[f.field_name] = f.value

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            **redact(fields, self._redact_fields, sensitive_values),
        }
        with self._path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")


def save_screenshot(page, run_dir: Path, step_id: int, reason: str, mask_locators: list | None = None) -> str:
    path = run_dir / "screenshots" / f"step_{step_id}_{reason}.png"
    page.screenshot(path=str(path), mask=mask_locators or [], mask_color="#000000")
    return str(path)
