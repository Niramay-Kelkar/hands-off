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
ACTIONABLE_TOOLS = {"click", "type", "select", "navigate", "extract"}
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

    _apply_step_overrides(steps, policy.step_overrides)

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
    if next_action.tool_call["name"] == "extract":
        # extract's locator name is the exact value it reads off the page (e.g. a
        # member's name) — that's discovery-run-specific data, not a stable landmark,
        # so templatizing it would bake this one trajectory's literal output into
        # every future replay's checkpoint. The extract step's own label (e.g.
        # "Name:") is stable across every member, so check that instead — the
        # same signal _build_extract_step below compiles the extract locator from.
        if next_action.extract_label:
            element_check = ConditionSpec(type="element_visible", role="cell", name=next_action.extract_label)
        else:
            # No label-cell/value-cell pair to fall back on (e.g. a standalone
            # heading, not a labeled data field) -- nulling the name here
            # would make this an "any element with this role is visible"
            # check, which is trivially true on almost every page and would
            # silently defeat the checkpoint's actual job. Unlike a data
            # value (a member's name/balance, which varies per input and
            # per run), a landmark like a page heading is stable and safe to
            # check for directly, the same as any non-extract step's target.
            name = templatize(inp["name"], params)
            element_check = ConditionSpec(type="element_visible", role=role, name=name)
    elif next_action.resolved_via == "label_proximity":
        # element_visible resolves role+name via a plain accessibility lookup
        # (agent/checkpoints.py) -- it has no label_proximity-aware fallback,
        # and adding one there is out of scope for this compiler-side change.
        # A hostile form's field carries no accessible name at all, so that
        # lookup can never match. The field's label text, though, is real
        # visible page text (it's what let label_proximity find the field by
        # row in the first place) -- check for that instead, which needs
        # nothing beyond the already-supported text_present checkpoint.
        name = templatize(inp["name"], params)
        # A leading "* " on the label is often a CSS ::before required-field
        # marker (e.g. MERIDIAN's `.req:before { content: "* "; }`) -- part
        # of the computed ACCESSIBLE name label_proximity's own locator
        # correctly matched against, but invisible to text_present's plain
        # innerText scan, which only ever sees the real label text itself.
        # Stripping it here is a no-op for any label that never had one.
        visible_name = re.sub(r"^\*\s*", "", name)
        element_check = ConditionSpec(type="text_present", value=f"{visible_name}:", scope="page")
    else:
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


def _label_proximity_locator_for(name: str) -> LocatorSpec:
    # No accessibility/text_label fallback here, same reasoning as the "no
    # css fallback ever synthesized" rule below: discovery only verified
    # label_proximity resolution for this field (its accessible name is
    # blank), so accessibility/text_label would just fail identically at
    # replay -- emitting them would misrepresent what was actually proven.
    return LocatorSpec(strategies=[LocatorStrategyModel(kind="label_proximity", priority=1, label=name)])


def _label_locator_for(label: str) -> LocatorSpec:
    # Label cells carry the same role ("cell") as value cells in this app's
    # markup — there's no distinct ARIA role to key on, so agent.actions'
    # _read_extracted_text detects "this resolved to a label, not a value" from
    # the resolved element's own text ending in ':' at replay time, not from role.
    # No text_label fallback here — get_by_label only resolves form-control
    # labels, which these table cells aren't.
    return LocatorSpec(
        strategies=[
            LocatorStrategyModel(kind="accessibility", priority=1, role="cell", name_matches="^" + re.escape(label)),
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
    locator = (
        _label_proximity_locator_for(target_name)
        if action.resolved_via == "label_proximity"
        else _locator_for(role, target_name)
    )

    if name == "type":
        value = templatize(inp["value"], params)
        action_model = ActionModel(type="type", value=value)
        description = f"Type into role={role!r} name={target_name!r}"
    elif name == "select":
        value = templatize(inp["value"], params)
        action_model = ActionModel(type="select", value=value)
        description = f"Select {value!r} in role={role!r} name={target_name!r}"
    else:  # click
        action_model = ActionModel(type="click")
        description = f"Click role={role!r} name={target_name!r}"

    return Step(step_id=step_id, description=description, action=action_model, locator=locator, checkpoint=checkpoint)


def _money_locator_for() -> LocatorSpec:
    # label_for_value's label-cell/sibling-value convention only covers a
    # 2-column label:value row (e.g. "Name:" / "Lovelace, Ada") -- it finds
    # nothing for a value sitting in a column-header table (e.g. a shares
    # table with "Share ID | Type | Balance | Status" headers, no per-cell
    # "Balance:" label), so extract_label comes back None there even though
    # the value is real. Falling through to a literal-value locator in that
    # case (the old behavior) pins replay to the exact dollar figure seen at
    # discovery/compile time, which breaks the moment the live balance
    # changes -- discovered as a real bug in meridian_member_balance_inquiry,
    # see BUILD_LOG.md. A money-shaped value gets a money-pattern locator
    # instead: still untied to the literal figure, and .first (in
    # resolve_locator) reproduces "whichever cell discovery actually read"
    # since it's the first such cell in DOM order, same as discovery saw.
    return LocatorSpec(
        strategies=[
            LocatorStrategyModel(kind="accessibility", priority=1, role="cell", name_matches=r"^\$[\d,]+\.\d{2}$"),
        ]
    )


def _build_extract_step(
    step_id: int, extract_actions: list[TrajectoryStep], output_names: list[str], params: dict[str, str]
) -> Step:
    fields = []
    for a in extract_actions:
        inp = a.tool_call["input"]
        if a.extract_label:
            locator = _label_locator_for(a.extract_label)
        elif _MONEY_PATTERN.match(inp["name"]):
            locator = _money_locator_for()
        else:
            role = inp["role"]
            target_name = templatize(inp["name"], params)
            locator = _locator_for(role, target_name)
        fields.append(ExtractField(output=inp["output_name"], locator=locator))

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


def _apply_step_overrides(steps: list[Step], step_overrides: dict[int, dict]) -> None:
    if not step_overrides:
        return
    by_id = {s.step_id: s for s in steps}
    unknown = [step_id for step_id in step_overrides if step_id not in by_id]
    if unknown:
        raise CompilationError(
            f"policy step_overrides references step_id(s) {unknown} not present in the compiled "
            f"output (valid step_ids: 1-{len(steps)}) — check the policy against this trajectory's "
            "actual compiled step order, which can shift if the discovery run changes"
        )
    for step_id, override in step_overrides.items():
        by_id[step_id].escalation_override = override
