"""Raw discovery output — one full trajectory per `agent.discover` run.

Deliberately separate from agent.models: this is not the artifact
schema. The compiler (next build-order piece) reads a Trajectory and
produces a Capability.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class TrajectoryTarget(BaseModel):
    entry_point: str
    allowlist_origin: str
    allowlist_routes: list[str]


class TrajectoryStep(BaseModel):
    step_index: int
    observation: str
    screenshot_path: str | None = None
    model_text: str = ""
    tool_call: dict[str, Any]
    tool_result: dict[str, Any]


TrajectoryStatus = Literal["running", "done", "max_steps_reached", "hard_failure"]


class Trajectory(BaseModel):
    run_id: str
    goal: str
    target: TrajectoryTarget
    model: str
    started_at: datetime
    ended_at: datetime | None = None
    status: TrajectoryStatus = "running"
    final_outputs: dict[str, str] | None = None
    final_summary: str | None = None
    steps: list[TrajectoryStep] = Field(default_factory=list)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2))
