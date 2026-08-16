"""Typed, versioned artifact schema (Pydantic) mirroring schema/example_artifact.json,
and the replay engine's three-shape result contract.

`ConditionSpec`, `LocatorStrategyModel`, and `ActionModel` are deliberately
loose (`extra="allow"`): the `type`/`kind` string is the only fixed field,
looked up in the relevant registry (agent.checkpoints / agent.locators /
agent.actions) at evaluation time. Adding a new condition, locator, or
action type never requires touching these models — only registering a new
function. Use `get_field()` below to read type-specific values off these
loose models, since attribute access on extra fields is not guaranteed
across Pydantic versions.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


def get_field(spec: BaseModel, name: str, default: Any = None) -> Any:
    """Read a type-specific value off a loose (extra="allow") model."""
    value = getattr(spec, name, None)
    if value is not None:
        return value
    extra = spec.model_extra or {}
    return extra.get(name, default)


class LooseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


# --- Target / inputs / outputs ----------------------------------------------

class Target(BaseModel):
    app: str
    entry_point: str
    surface_type: str


class InputSpec(BaseModel):
    name: str
    type: str
    pattern: str | None = None
    required: bool = True
    description: str = ""


class OutputSpec(BaseModel):
    name: str
    type: str
    description: str = ""


# --- Conditions (checkpoints + expected_outcomes.detection share a registry) --

class ConditionSpec(LooseModel):
    type: str
    # explicitly typed so any_of's nested conditions parse recursively
    # instead of staying as raw dicts under extra="allow"
    conditions: list["ConditionSpec"] | None = None


ConditionSpec.model_rebuild()


class ExpectedOutcome(BaseModel):
    code: str
    description: str = ""
    detection: ConditionSpec


# --- Locators -----------------------------------------------------------------

class LocatorStrategyModel(LooseModel):
    kind: str
    priority: int


class LocatorSpec(BaseModel):
    strategies: list[LocatorStrategyModel]


# --- Actions --------------------------------------------------------------------

class ExtractField(BaseModel):
    output: str
    locator: LocatorSpec


class ActionModel(LooseModel):
    type: str
    value: str | None = None
    fields: list[ExtractField] | None = None


class Wait(BaseModel):
    type: str
    timeout_ms: int = 3000


# --- Escalation -------------------------------------------------------------------

EscalationAction = Literal["escalate", "hard_fail"]


class EscalationRule(BaseModel):
    retry: int = 0
    then: EscalationAction = "escalate"

    @model_validator(mode="before")
    @classmethod
    def _normalize_bare_string(cls, value: Any) -> Any:
        # the schema allows a bare "escalate" as shorthand for {retry: 0, then: "escalate"}
        if isinstance(value, str):
            return {"retry": 0, "then": value}
        return value


class EscalationPolicySet(BaseModel):
    on_checkpoint_failure: EscalationRule
    on_unrecognized_dialog: EscalationRule
    on_step_timeout: EscalationRule
    on_hard_failure: EscalationRule

    def get(self, trigger: str) -> EscalationRule:
        return getattr(self, trigger)


class EscalationPolicy(BaseModel):
    default: EscalationPolicySet


# --- Steps ------------------------------------------------------------------------

class Step(BaseModel):
    step_id: int
    description: str = ""
    action: ActionModel
    locator: LocatorSpec | None = None
    wait: Wait | None = None
    checkpoint: ConditionSpec
    escalation_override: dict[str, EscalationRule] | None = None


# --- Guardrails / evidence ----------------------------------------------------------

class Guardrails(BaseModel):
    allowlist_routes: list[str]
    allowed_action_types: list[str]
    requires_confirmation: bool = False


class EvidencePolicy(BaseModel):
    screenshot_on: list[str] = Field(default_factory=list)
    redact_fields: list[str] = Field(default_factory=list)


# --- Capability (the artifact itself) -------------------------------------------------

class Capability(BaseModel):
    schema_version: str
    capability_id: str
    version: int
    description: str = ""
    created_at: datetime | None = None
    created_by: str = ""
    source_run_id: str = ""
    target: Target
    risk_class: str
    inputs: list[InputSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)
    expected_outcomes: list[ExpectedOutcome] = Field(default_factory=list)
    steps: list[Step]
    guardrails: Guardrails
    evidence_policy: EvidencePolicy
    escalation_policy: EscalationPolicy

    def expected_outcome(self, code: str) -> ExpectedOutcome | None:
        return next((o for o in self.expected_outcomes if o.code == code), None)

    @classmethod
    def load(cls, path: str | Path) -> "Capability":
        return cls.model_validate_json(Path(path).read_text())


# --- Replay result contract (exactly one of these three shapes) -------------------------

class SuccessResult(BaseModel):
    status: Literal["success"] = "success"
    outputs: dict[str, Any]


class BusinessOutcomeResult(BaseModel):
    status: Literal["business_outcome"] = "business_outcome"
    outcome_code: str
    step_id: int


class HardFailureResult(BaseModel):
    status: Literal["hard_failure"] = "hard_failure"
    step_id: int
    expected: str
    observed: str
    screenshot_path: str | None = None


ReplayResult = Union[SuccessResult, BusinessOutcomeResult, HardFailureResult]
