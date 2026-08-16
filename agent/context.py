"""RunContext — the single object threaded through locator resolution,
checkpoint evaluation, and action execution for one replay run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from agent.evidence import StepLogWriter
from agent.models import Capability


@dataclass
class RunContext:
    page: Page
    artifact: Capability
    params: dict[str, str]
    run_id: str
    evidence_dir: Path
    log: StepLogWriter
    outputs: dict[str, Any] = field(default_factory=dict)
