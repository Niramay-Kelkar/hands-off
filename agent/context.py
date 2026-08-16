"""Per-run context objects threaded through locator resolution, checkpoint
evaluation, and action execution — RunContext for replay, DiscoveryContext
for the discovery agent loop."""

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
    allowed_origin: str
    outputs: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveryContext:
    page: Page
    run_id: str
    evidence_dir: Path
    log: StepLogWriter
    allowed_origin: str
    allowlist_routes: list[str]
    allowed_action_types: list[str]
    outputs: dict[str, Any] = field(default_factory=dict)
