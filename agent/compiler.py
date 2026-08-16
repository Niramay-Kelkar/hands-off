"""Compiles a raw discovery Trajectory + an authored PolicySpec into a
final Capability artifact.

The mechanical layer — steps, locators, checkpoints, inputs, outputs,
target — is derived entirely from the trajectory (and --param). The
policy layer — expected_outcomes, guardrails, escalation_policy,
risk_class, capability_id/description/version — comes entirely from
--policy. Neither ever overrides the other's fields; see CLAUDE.md for
why (a single successful trajectory can't honestly produce the policy
layer, the same way it can't honestly produce a css locator fallback).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from agent.models import (
    ActionModel,
    Capability,
    ConditionSpec,
    ExtractField,
    InputSpec,
    LocatorSpec,
    LocatorStrategyModel,
    OutputSpec,
    Step,
    Target,
)
from agent.policy import PolicySpec
from agent.template import templatize
from agent.trajectory import Trajectory, TrajectoryStep

SCHEMA_VERSION = "1.0"
ACTIONABLE_TOOLS = {"click", "type", "navigate", "extract"}
_MONEY_PATTERN = re.compile(r"^\$[\d,]+\.\d{2}$")


class CompilationError(ValueError):
    pass


def compile_trajectory(
    trajectory: Trajectory,
    policy: PolicySpec,
    params: dict[str, str],
    *,
    app: str | None = None,
    surface_type: str = "web_legacy",
) -> Capability:
    if trajectory.status != "done":
        raise CompilationError(
            f"cannot compile a trajectory with status {trajectory.status!r} — "
            "only a 'done' trajectory represents a proven successful path"
        )
    if not trajectory.final_outputs:
        raise CompilationError("trajectory has no final_outputs to compile into artifact outputs")

    output_names = list(trajectory.final_outputs.keys())

    actions = [s for s in trajectory.steps if s.tool_call["name"] in ACTIONABLE_TOOLS and s.tool_result.get("ok")]
    if not actions:
        raise CompilationError("trajectory has no successful actions to compile")

    # last successful extract per output_name wins, in case of a re-extract
    extract_by_output: dict[str, TrajectoryStep] = {}
    for a in actions:
        if a.tool_call["name"] == "extract":
            out_name = a.tool_call["input"]["output_name"]
            if out_name in output_names:
                extract_by_output[out_name] = a
    missing = [name for name in output_names if name not in extract_by_output]
    if missing:
        raise CompilationError(f"final_outputs {missing} have no corresponding successful extract call in the trajectory")
    extract_actions = [extract_by_output[name] for name in output_names]

    outcome_codes = [o.code for o in policy.expected_outcomes]

    steps: list[Step] = []
    step_id = 1
    for idx, action in enumerate(actions):
        if action.tool_call["name"] == "extract":
            continue  # merged into one step below
        next_action = actions[idx + 1] if idx + 1 < len(actions) else None
        checkpoint = _checkpoint_for(next_action, output_names, params, outcome_codes)
        steps.append(_build_action_step(step_id, action, checkpoint, params))
        step_id += 1

    steps.append(_build_extract_step(step_id, extract_actions, output_names, params))

    outputs = [_build_output(name, trajectory) for name in output_names]
    inputs = [
        InputSpec(
            name=name,
            type="string",
            pattern=None,
            required=True,
            description=f"Parameter '{name}' (from discovery run {trajectory.run_id})",
        )
        for name in params
    ]

    _validate_params_used(steps, params)

    target = Target(
        app=app or urlparse(trajectory.target.entry_point).netloc,
        entry_point=trajectory.target.entry_point,
        surface_type=surface_type,
    )

    return Capability(
        schema_version=SCHEMA_VERSION,
        capability_id=policy.capability_id,
        version=policy.version,
        description=policy.description,
        created_at=datetime.now(timezone.utc),
        created_by="agent.compile",
        source_run_id=trajectory.run_id,
        target=target,
        risk_class=policy.risk_class,
        inputs=inputs,
        outputs=outputs,
        expected_outcomes=policy.expected_outcomes,
        steps=steps,
        guardrails=policy.guardrails,
        evidence_policy=policy.evidence_policy,
        escalation_policy=policy.escalation_policy,
    )


def _checkpoint_for(
    next_action: TrajectoryStep | None, output_names: list[str], params: dict[str, str], outcome_codes: list[str]
) -> ConditionSpec:
    if next_action is None:
        return ConditionSpec(type="outputs_non_empty", fields=output_names)
    inp = next_action.tool_call["input"]
    if next_action.tool_call["name"] == "navigate":
        raise CompilationError(
            "a step immediately followed by a 'navigate' action has no element to check "
            "visibility on — not yet supported by the compiler"
        )
    role = inp["role"]
    name = templatize(inp["name"], params)
    element_check = ConditionSpec(type="element_visible", role=role, name=name)

    if not outcome_codes:
        return element_check

    # expected_outcomes are given by policy, not derived — a single successful
    # trajectory never observes them, but referencing codes policy already
    # declares isn't guessing, and without this every intermediate checkpoint
    # would treat a legitimate business outcome as an unrecognized failure.
    return ConditionSpec(
        type="any_of",
        conditions=[element_check, ConditionSpec(type="outcome_match", outcome_codes=outcome_codes)],
    )


def _locator_for(role: str, name: str) -> LocatorSpec:
    return LocatorSpec(
        strategies=[
            LocatorStrategyModel(kind="accessibility", priority=1, role=role, name=name),
            LocatorStrategyModel(kind="text_label", priority=2, label=name),
        ]
    )


def _build_action_step(step_id: int, action: TrajectoryStep, checkpoint: ConditionSpec, params: dict[str, str]) -> Step:
    tc = action.tool_call
    name = tc["name"]
    inp = tc["input"]

    if name == "navigate":
        url = templatize(inp["url"], params)
        return Step(
            step_id=step_id,
            description=f"navigate to {url}",
            action=ActionModel(type="navigate", value=url),
            locator=None,
            checkpoint=checkpoint,
        )

    role = inp["role"]
    target_name = templatize(inp["name"], params)
    locator = _locator_for(role, target_name)

    if name == "type":
        value = templatize(inp["value"], params)
        action_model = ActionModel(type="type", value=value)
        description = f"Type into role={role!r} name={target_name!r}"
    else:  # click
        action_model = ActionModel(type="click")
        description = f"Click role={role!r} name={target_name!r}"

    return Step(step_id=step_id, description=description, action=action_model, locator=locator, checkpoint=checkpoint)


def _build_extract_step(
    step_id: int, extract_actions: list[TrajectoryStep], output_names: list[str], params: dict[str, str]
) -> Step:
    fields = []
    for a in extract_actions:
        inp = a.tool_call["input"]
        role = inp["role"]
        target_name = templatize(inp["name"], params)
        fields.append(ExtractField(output=inp["output_name"], locator=_locator_for(role, target_name)))

    return Step(
        step_id=step_id,
        description="Read final outputs",
        action=ActionModel(type="extract", fields=fields),
        locator=None,
        checkpoint=ConditionSpec(type="outputs_non_empty", fields=output_names),
    )


def _build_output(output_name: str, trajectory: Trajectory) -> OutputSpec:
    value = trajectory.final_outputs.get(output_name, "") if trajectory.final_outputs else ""
    output_type = "money" if _MONEY_PATTERN.match(value) else "string"
    return OutputSpec(name=output_name, type=output_type, description=f"Value captured via extract as '{output_name}'")


def _validate_params_used(steps: list[Step], params: dict[str, str]) -> None:
    steps_json = json.dumps([s.model_dump() for s in steps], default=str)
    unused = [name for name in params if "{{" + name + "}}" not in steps_json]
    if unused:
        raise CompilationError(
            f"declared param(s) {unused} were never templatized into any compiled step — "
            "check the --param values exactly match a literal value discovery actually used"
        )
