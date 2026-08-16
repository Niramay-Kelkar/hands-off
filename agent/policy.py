"""The compiler's --policy input: everything a Capability needs that a
single successful discovery trajectory structurally cannot supply.

A trajectory can only ever show one successful path — it never observes
failure states, so expected_outcomes/escalation_policy/guardrails/
risk_class can't be honestly derived from it, the same way a css
locator fallback can't be honestly synthesized without re-visiting the
live page. These are authored by a human instead.

extra="ignore" so a full Capability JSON (e.g. schema/example_artifact.json)
can be passed directly as --policy — the trajectory-derived fields it
also happens to contain (target/inputs/outputs/steps/schema_version/
created_*/source_run_id) are simply not read.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent.models import EscalationPolicy, EvidencePolicy, ExpectedOutcome, Guardrails


class PolicySpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capability_id: str
    description: str = ""
    version: int
    risk_class: str
    expected_outcomes: list[ExpectedOutcome] = []
    guardrails: Guardrails
    evidence_policy: EvidencePolicy
    escalation_policy: EscalationPolicy

    @classmethod
    def load(cls, path: str | Path) -> "PolicySpec":
        return cls.model_validate_json(Path(path).read_text())
