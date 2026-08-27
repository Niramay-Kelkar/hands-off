"""Discovery agent loop: LLM-driven observe -> decide -> act against a
live browser, run once per capability, producing a Trajectory (see
agent.trajectory) — raw material for the artifact compiler, not a
compiled artifact itself. The LLM never sees free-form parsing of its
intent: it must call exactly one tool per turn (Messages API
tool_choice="any", parallel tool use disabled), and every tool result
fed back to it carries a concrete detail message, success or failure.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from playwright.sync_api import sync_playwright

from agent import evidence, guardrails, perception, redaction
from agent.context import DiscoveryContext
from agent.discovery_tools import DISCOVERY_TOOL_REGISTRY, TOOL_SCHEMAS, ToolOutcome
from agent.evidence import StepLogWriter
from agent.guardrails import GuardrailViolation
from agent.trajectory import Trajectory, TrajectoryStep, TrajectoryTarget

DEFAULT_MAX_STEPS = 25
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_ALLOWLIST_ROUTES = ["/*"]
ALLOWED_ACTION_TYPES = ["click", "type", "select", "navigate", "extract"]
DEFAULT_REDACT_FIELDS = ["password", "ssn", "account_number", "token", "secret"]

SYSTEM_PROMPT = """You are a discovery agent. You are shown a goal and, each turn, a text \
description of the current page's accessibility tree: the role and accessible name of \
every element, with enough structure to tell elements apart. There is no screenshot; \
reason only from this text.

You must call exactly one tool every turn. Available actions: click, type, select, \
navigate, extract. Use select for a dropdown/combobox, giving the visible option text \
as value. Use extract to record any value the goal asks you to read, with a clear \
output_name — extract independently re-reads the value from the live page, so it will \
fail if your role/name don't uniquely identify one element. When the goal has been \
fully achieved, call done with the output_names of everything you extracted (not the \
values themselves) and a short summary. Do not call done referencing an output you \
have not successfully extracted yet — extract it first.

If a tool call fails — element not found, an ambiguous match, or blocked by guardrails \
— you will get a specific explanation in the result. Adjust your next tool call \
accordingly; do not repeat the exact same call."""


def run_discovery(
    goal: str,
    target_url: str,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    model: str = DEFAULT_MODEL,
    headed: bool = True,
    allowlist_routes: list[str] | None = None,
    redact_fields: list[str] | None = None,
    out_path: str | Path | None = None,
) -> Trajectory:
    allowed_origin = guardrails.derive_origin(target_url)
    allowlist_routes = allowlist_routes or list(DEFAULT_ALLOWLIST_ROUTES)
    # Union, never replace: DEFAULT_REDACT_FIELDS is the baseline every
    # discovery run must always mask regardless of what a target-specific
    # policy adds on top (e.g. MERIDIAN's "E-mail"/"Phone"/"Address"/
    # "Share ID", which password/ssn/account_number/token/secret alone
    # don't cover).
    redact_fields = list(dict.fromkeys(DEFAULT_REDACT_FIELDS + list(redact_fields or [])))
    redact_labels = {redaction.normalize_label(f) for f in redact_fields}

    run_id = evidence.new_run_id("discover")
    rdir = evidence.run_dir(run_id)
    log = StepLogWriter(rdir / "log.jsonl", redact_fields)
    log.event("run_start", goal=goal, target=target_url, model=model)

    client = anthropic.Anthropic()

    trajectory = Trajectory(
        run_id=run_id,
        goal=goal,
        target=TrajectoryTarget(entry_point=target_url, allowlist_origin=allowed_origin, allowlist_routes=allowlist_routes),
        model=model,
        started_at=datetime.now(timezone.utc),
        status="running",
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        page = browser.new_page()
        log.attach_page(page)
        ctx = DiscoveryContext(
            page=page,
            run_id=run_id,
            evidence_dir=rdir,
            log=log,
            allowed_origin=allowed_origin,
            allowlist_routes=allowlist_routes,
            allowed_action_types=ALLOWED_ACTION_TYPES,
        )

        try:
            page.goto(target_url)
        except Exception as exc:
            trajectory.status = "hard_failure"
            trajectory.final_summary = f"failed to navigate to entry point: {exc}"
            trajectory.ended_at = datetime.now(timezone.utc)
            log.event("run_end", status=trajectory.status, detail=str(exc)[:300])
            browser.close()
            if out_path is not None:
                trajectory.save(out_path)
            return trajectory

        observation = perception.observe(page, redact_fields=redact_fields)
        log.event("observation", step_index=0, observation=observation)

        messages: list[dict] = [{"role": "user", "content": f"Goal: {goal}\n\nCurrent page:\n{observation}"}]
        typed_secrets: list[redaction.SensitiveField] = []

        try:
            for step_index in range(1, max_steps + 1):
                response = client.messages.create(
                    model=model,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=TOOL_SCHEMAS,
                    tool_choice={"type": "any", "disable_parallel_tool_use": True},
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": response.content})

                tool_use = next((b for b in response.content if b.type == "tool_use"), None)
                model_text = "\n".join(b.text for b in response.content if b.type == "text")

                # `sensitive` only ever covers values ALREADY visible on the
                # page before this turn's action runs; `typed_secrets`
                # (accumulated below, across the whole run) additionally
                # covers values the model typed into a redact_fields-labelled
                # field on an EARLIER step -- e.g. a `done` summary or later
                # model_text that narrates back an e-mail/phone it just set,
                # which `sensitive` alone can't catch since that value never
                # existed on the page until the model itself put it there.
                sensitive = redaction.find_sensitive_fields(page, redact_fields)
                mask_set = sensitive + typed_secrets
                model_text = redaction.mask_text(model_text, mask_set)
                screenshot_path = evidence.save_screenshot(
                    page, rdir, step_index, "step", mask_locators=[f.locator for f in sensitive]
                )

                if tool_use is None:
                    # shouldn't happen with tool_choice="any", but guard anyway
                    log.event("no_tool_call", step_index=step_index, model_text=model_text)
                    trajectory.status = "hard_failure"
                    trajectory.final_summary = "model turn produced no tool call"
                    break

                # Masked with the same `mask_set` used for model_text/screenshots
                # above, and applied here (once, before either persistence path) rather
                # than relying on StepLogWriter's own value-redaction pass in
                # agent/evidence.py -- that pass keys its lookup by field_name, so when
                # several distinct values share one name (e.g. 27 different "Share ID"
                # rows), only the last one survives and everything else logs raw. A
                # field like `done`'s free-text summary is the one place the model can
                # echo back page content it just read, the same risk model_text already
                # covers -- click/navigate results are otherwise fixed template strings.
                masked_input = _mask_tool_input(tool_use.input, mask_set)
                # Mutates typed_secrets in place with THIS step's own raw
                # value (if any) BEFORE outcome.detail is masked below --
                # e.g. the `type` action's own result template
                # ("typed 'ada.lovelace@example.com' into ...") echoes back
                # the exact literal value tool_use.input carried, and that
                # value cannot be in `mask_set` from the top of this loop
                # iteration, since it didn't exist anywhere until this call.
                masked_input = _mask_typed_value(masked_input, tool_use.name, redact_labels, typed_secrets)
                log.event("tool_call", step_index=step_index, name=tool_use.name, input=masked_input)
                outcome = _dispatch(tool_use.name, tool_use.input, ctx)
                masked_detail = redaction.mask_text(outcome.detail, sensitive + typed_secrets)
                log.event("tool_result", step_index=step_index, ok=outcome.ok, detail=masked_detail)

                trajectory.steps.append(
                    TrajectoryStep(
                        step_index=step_index,
                        observation=observation,
                        screenshot_path=screenshot_path,
                        model_text=model_text,
                        tool_call={"name": tool_use.name, "input": masked_input},
                        tool_result={"ok": outcome.ok, "detail": masked_detail},
                        extract_label=outcome.label,
                        resolved_via=outcome.resolved_via,
                    )
                )
                if out_path is not None:
                    trajectory.save(out_path)

                if outcome.ended:
                    trajectory.status = "done"
                    trajectory.final_outputs = outcome.outputs
                    trajectory.final_summary = masked_detail
                    break

                observation = perception.observe(page, redact_fields=redact_fields)
                log.event("observation", step_index=step_index, observation=observation)
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.id,
                                "content": f"{outcome.detail}\n\nCurrent page:\n{observation}",
                                "is_error": not outcome.ok,
                            }
                        ],
                    }
                )
            else:
                trajectory.status = "max_steps_reached"
                trajectory.final_summary = f"stopped after {max_steps} steps without calling done"
        finally:
            log.attach_page(None)  # about to go dead — later log.event() calls must not try to scan it
            browser.close()

    trajectory.ended_at = datetime.now(timezone.utc)
    log.event("run_end", status=trajectory.status)
    if out_path is not None:
        trajectory.save(out_path)
    return trajectory


def _mask_tool_input(value: Any, fields: list) -> Any:
    """Recursively applies redaction.mask_text to every string leaf of a
    tool call's input (e.g. `done`'s free-text summary) before it's logged
    or persisted — see the comment at its call site for why this can't be
    left to agent.evidence.StepLogWriter's own redaction pass alone."""
    if isinstance(value, str):
        return redaction.mask_text(value, fields)
    if isinstance(value, dict):
        return {k: _mask_tool_input(v, fields) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_tool_input(v, fields) for v in value]
    return value


def _mask_typed_value(
    masked_input: Any, tool_name: str, redact_labels: set[str], typed_secrets: list
) -> Any:
    """Masks a `type`/`select` tool call's own `value` when its target
    field's `name` (accessible label) matches a redact_fields entry --
    independent of `_mask_tool_input`/`sensitive`, which can only mask a
    value that was ALREADY visible on the page BEFORE this action runs.

    Found live: `sensitive` (agent.redaction.find_sensitive_fields) is
    computed from the page's PRE-action state, so for a brand-new value
    the model is about to type into a field for the first time -- e.g.
    the literal "password" typed into every capability's Password field,
    or a new e-mail/phone typed into Update Member Information's form --
    there was nothing yet on the page for `sensitive` to have found, so
    `_mask_tool_input`'s substring replace had nothing to match and the
    raw value went straight into the trajectory. This masks by LABEL
    instead of by previously-observed VALUE, which is the only thing
    that can catch a value that has never appeared on the page before.

    Before masking, the raw value is appended to `typed_secrets` (mutated
    in place) so later steps' model_text/tool_result/`done` summary --
    which might narrate this same value back in free text -- can also
    catch it via the same value-substring masking `sensitive` already
    gets, even though it never appeared as a `find_sensitive_fields`
    page-scan result.
    """
    if tool_name not in ("type", "select") or not isinstance(masked_input, dict):
        return masked_input
    field_label = masked_input.get("name")
    raw_value = masked_input.get("value")
    if not isinstance(field_label, str) or not isinstance(raw_value, str) or not raw_value:
        return masked_input
    label = field_label.strip()
    if label.startswith("*"):
        label = label[1:].strip()
    if label.endswith(":"):
        label = label[:-1].strip()
    normalized = redaction.normalize_label(label)
    if normalized in redact_labels:
        field_name = re.sub(r"\s+", "_", normalized)
        typed_secrets.append(redaction.SensitiveField(field_name=field_name, value=raw_value, locator=None))
        # Field-specific placeholder, not a bare "[REDACTED]" -- a bare
        # placeholder would collapse every redacted field (e.g. password
        # AND a freshly-typed e-mail AND phone) to the identical literal
        # string, which breaks agent.compiler's templatize() (exact-value
        # match against --param): with several distinct params all equal
        # to the same placeholder, the first one in dict-iteration order
        # silently wins every match, and the rest report as "never
        # templatized". Tagging the placeholder with which field it is
        # keeps each one distinguishable for a `--param name="[REDACTED:name]"`
        # compile-time match, while still being unambiguously a
        # placeholder, not real data, to a human reading the trajectory.
        return {**masked_input, "value": f"[REDACTED:{field_name}]"}
    return masked_input


def _dispatch(name: str, tool_input: dict, ctx: DiscoveryContext) -> ToolOutcome:
    if name != "done":
        try:
            guardrails.check_action_type(name, ctx.allowed_action_types)
        except GuardrailViolation as exc:
            return ToolOutcome(ok=False, detail=f"blocked by guardrails: {exc}")
    try:
        fn = DISCOVERY_TOOL_REGISTRY.get(name)
    except KeyError as exc:
        return ToolOutcome(ok=False, detail=str(exc))
    try:
        return fn(tool_input, ctx)
    except Exception as exc:  # never let a bad tool call crash the run
        return ToolOutcome(ok=False, detail=f"{type(exc).__name__}: {exc}")
