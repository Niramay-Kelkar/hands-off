"""Discovery agent loop: LLM-driven observe -> decide -> act against a
live browser, run once per capability, producing a Trajectory (see
agent.trajectory) — raw material for the artifact compiler, not a
compiled artifact itself. The LLM never sees free-form parsing of its
intent: it must call exactly one tool per turn (Messages API
tool_choice="any", parallel tool use disabled), and every tool result
fed back to it carries a concrete detail message, success or failure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
ALLOWED_ACTION_TYPES = ["click", "type", "navigate", "extract"]
DEFAULT_REDACT_FIELDS = ["password", "ssn", "account_number", "token", "secret"]

SYSTEM_PROMPT = """You are a discovery agent. You are shown a goal and, each turn, a text \
description of the current page's accessibility tree: the role and accessible name of \
every element, with enough structure to tell elements apart. There is no screenshot; \
reason only from this text.

You must call exactly one tool every turn. Available actions: click, type, navigate, \
extract. Use extract to record any value the goal asks you to read, with a clear \
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
    out_path: str | Path | None = None,
) -> Trajectory:
    allowed_origin = guardrails.derive_origin(target_url)
    allowlist_routes = allowlist_routes or list(DEFAULT_ALLOWLIST_ROUTES)

    run_id = evidence.new_run_id("discover")
    rdir = evidence.run_dir(run_id)
    log = StepLogWriter(rdir / "log.jsonl", DEFAULT_REDACT_FIELDS)
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

        observation = perception.observe(page, redact_fields=DEFAULT_REDACT_FIELDS)
        log.event("observation", step_index=0, observation=observation)

        messages: list[dict] = [{"role": "user", "content": f"Goal: {goal}\n\nCurrent page:\n{observation}"}]

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

                sensitive = redaction.find_sensitive_fields(page, DEFAULT_REDACT_FIELDS)
                model_text = redaction.mask_text(model_text, sensitive)
                screenshot_path = evidence.save_screenshot(
                    page, rdir, step_index, "step", mask_locators=[f.locator for f in sensitive]
                )

                if tool_use is None:
                    # shouldn't happen with tool_choice="any", but guard anyway
                    log.event("no_tool_call", step_index=step_index, model_text=model_text)
                    trajectory.status = "hard_failure"
                    trajectory.final_summary = "model turn produced no tool call"
                    break

                log.event("tool_call", step_index=step_index, name=tool_use.name, input=tool_use.input)
                outcome = _dispatch(tool_use.name, tool_use.input, ctx)
                log.event("tool_result", step_index=step_index, ok=outcome.ok, detail=outcome.detail)

                trajectory.steps.append(
                    TrajectoryStep(
                        step_index=step_index,
                        observation=observation,
                        screenshot_path=screenshot_path,
                        model_text=model_text,
                        tool_call={"name": tool_use.name, "input": tool_use.input},
                        tool_result={"ok": outcome.ok, "detail": outcome.detail},
                        extract_label=outcome.label,
                    )
                )
                if out_path is not None:
                    trajectory.save(out_path)

                if outcome.ended:
                    trajectory.status = "done"
                    trajectory.final_outputs = outcome.outputs
                    trajectory.final_summary = outcome.detail
                    break

                observation = perception.observe(page, redact_fields=DEFAULT_REDACT_FIELDS)
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
