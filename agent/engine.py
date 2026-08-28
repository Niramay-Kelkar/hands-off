"""The replay engine: given a Capability artifact and input params,
executes its steps against the live UI with no LLM in the loop, and
resolves to exactly one of the three ReplayResult shapes (see
agent.models) — business outcomes and hard failures must never be
conflated. A recoverable condition that resolves via retry never shows
up in the return value at all, only in the structured log.

Each step is split into three phases with separate retry handling:

- ACT (resolve the step's locator, perform the action) — retrying this
  phase means redoing the action, which is only safe because nothing
  has happened yet if this phase itself failed. Once the action call
  itself returns without raising, it is never retried again for this
  step, no matter what fails afterward.
- SETTLE (the post-action route guardrail check, then the optional
  settle-wait) — everything that can only fail *after* the action has
  already fired. Retrying this phase re-checks/re-waits on the page,
  never re-invokes the action. This split exists because a settle-wait
  timeout (e.g. a slow post-click page load) used to be indistinguishable
  from an ACT-phase failure, so retrying it redid the click that had
  already registered — exactly the double-submission risk the ACT/CHECK
  split was meant to prevent, just not fully closed until ACT and SETTLE
  were separated. See BUILD_LOG.md.
- CHECK (unexpected-dialog interrupt, then checkpoint evaluation) —
  retrying this phase means re-checking current page state, never
  re-running the action. This matters for two reasons: an automatic
  retry must not double-click a button that already registered, and a
  human who resumes an escalated run has typically just fixed the page
  in place (e.g. dismissed a dialog) — re-evaluating what's on screen
  now is correct, blindly repeating the original click is not.
"""

from __future__ import annotations

import os
import re
from typing import Callable

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from agent import escalation, evidence, guardrails, redaction
from agent.actions import ACTION_REGISTRY
from agent.checkpoints import evaluate_checkpoint
from agent.context import RunContext
from agent.evidence import StepLogWriter
from agent.locators import LocatorResolutionError, ResolvedLocator, resolve_locator
from agent.models import (
    BusinessOutcomeResult,
    Capability,
    HardFailureResult,
    ReplayResult,
    Step,
    SuccessResult,
)


class InputValidationError(ValueError):
    pass


def validate_inputs(artifact: Capability, raw_params: dict[str, str]) -> dict[str, str]:
    """Checked before the browser is even launched."""
    errors: list[str] = []
    result: dict[str, str] = {}
    for spec in artifact.inputs:
        value = raw_params.get(spec.name)
        if value is None:
            if spec.required:
                errors.append(f"missing required input {spec.name!r}")
            continue
        if spec.pattern and not re.fullmatch(spec.pattern, value):
            errors.append(f"input {spec.name!r}={value!r} does not match pattern {spec.pattern!r}")
        result[spec.name] = value

    unknown = set(raw_params) - {spec.name for spec in artifact.inputs}
    if unknown:
        errors.append(f"unknown input(s): {sorted(unknown)}")

    if errors:
        raise InputValidationError("; ".join(errors))
    return result


def run_capability(
    artifact: Capability, raw_params: dict[str, str], *, headed: bool = True, inject: str | None = None
) -> ReplayResult:
    params = validate_inputs(artifact, raw_params)

    run_id = evidence.new_run_id(artifact.capability_id)
    rdir = evidence.run_dir(run_id)
    log = StepLogWriter(rdir / "log.jsonl", artifact.evidence_policy.redact_fields)
    log.event("run_start", capability_id=artifact.capability_id, version=artifact.version, params=params)
    escalation.open_session(run_id, artifact.capability_id)
    allowed_origin = guardrails.derive_origin(artifact.target.entry_point)

    with sync_playwright() as pw:
        # AGENT_CDP_PORT (unset by default, no behavior change): exposes this
        # run's browser over CDP on a fixed port so a second Playwright
        # process can attach to the SAME live browser — the mechanism a
        # human operator's escalation ultimately relies on, driven here
        # programmatically for evidence capture. See BUILD_LOG.md.
        launch_args = []
        cdp_port = os.environ.get("AGENT_CDP_PORT")
        if cdp_port:
            launch_args.append(f"--remote-debugging-port={cdp_port}")
        browser = pw.chromium.launch(headless=not headed, args=launch_args)
        page = browser.new_page()
        log.attach_page(page)
        ctx = RunContext(
            page=page, artifact=artifact, params=params, run_id=run_id, evidence_dir=rdir, log=log, allowed_origin=allowed_origin
        )

        try:
            try:
                entry_url = f"{artifact.target.entry_point}?inject={inject}" if inject else artifact.target.entry_point
                page.goto(entry_url)
                guardrails.check_route(page.url, ctx.allowed_origin, artifact.guardrails.allowlist_routes)
            except Exception as exc:
                log.event("entry_navigation_failed", detail=str(exc)[:300])
                screenshot_path = None
                if "hard_failure" in artifact.evidence_policy.screenshot_on:
                    sensitive = redaction.find_sensitive_fields(page, artifact.evidence_policy.redact_fields)
                    screenshot_path = evidence.save_screenshot(
                        page, rdir, 0, "hard_failure", mask_locators=[f.locator for f in sensitive]
                    )
                result = HardFailureResult(
                    step_id=0,
                    expected=f"navigate to entry_point {artifact.target.entry_point!r}",
                    observed=str(exc)[:300],
                    screenshot_path=screenshot_path,
                )
                log.event("run_end", status=result.status)
                escalation.complete_session(run_id, result.status)
                return result

            # Risk gate: a capability that isn't declared read_only, or that
            # explicitly requires confirmation, must not run unattended —
            # route straight to the same escalation/operator-console path
            # used mid-run, but before the first action executes. Navigation
            # already happened so the operator has real page context to
            # review, not a blank browser.
            if artifact.risk_class != "read_only" or artifact.guardrails.requires_confirmation:
                sensitive = redaction.find_sensitive_fields(page, artifact.evidence_policy.redact_fields)
                screenshot_path = evidence.save_screenshot(
                    page, rdir, 0, "risk_confirmation_required", mask_locators=[f.locator for f in sensitive]
                )
                log.event(
                    "risk_gate_pause",
                    risk_class=artifact.risk_class,
                    requires_confirmation=artifact.guardrails.requires_confirmation,
                    screenshot=screenshot_path,
                )
                escalation.pause_for_escalation(run_id, 0, "risk_confirmation_required", screenshot_path)
                log.event("risk_gate_resumed")

            for step in artifact.steps:
                result = _execute_step(step, ctx)
                if result is not None:
                    log.event("run_end", status=result.status)
                    escalation.complete_session(run_id, result.status)
                    return result

            outputs = {o.name: ctx.outputs.get(o.name) for o in artifact.outputs}
            result = SuccessResult(outputs=outputs)
            log.event("run_end", status=result.status)
            escalation.complete_session(run_id, result.status)
            return result
        finally:
            browser.close()


def _execute_step(step: Step, ctx: RunContext) -> ReplayResult | None:
    """Returns a terminal ReplayResult, or None to continue to the next step."""
    overrides = step.escalation_override or {}

    ok, act_result, terminal = _run_with_escalation(step, ctx, overrides, lambda: _act_once(step, ctx))
    if not ok:
        return terminal
    resolved, before_url = act_result

    ok, resolved, terminal = _run_with_escalation(
        step, ctx, overrides, lambda: _settle_once(step, ctx, resolved, before_url)
    )
    if not ok:
        return terminal

    ok, check_outcome, terminal = _run_with_escalation(step, ctx, overrides, lambda: _check_once(step, ctx, resolved))
    if not ok:
        return terminal
    return check_outcome  # None (continue) or BusinessOutcomeResult


def _run_with_escalation(
    step: Step, ctx: RunContext, overrides: dict, attempt_fn: Callable[[], tuple]
) -> tuple[bool, object, ReplayResult | None]:
    """Calls attempt_fn() repeatedly per the step's escalation policy.
    attempt_fn() returns ("ok", value) or ("failure", trigger, detail).
    On escalation, attempt_fn() is called again after resume — for the
    ACT phase that's a redo, since ACT hasn't succeeded yet if it's
    retrying at all; for the SETTLE and CHECK phases it's always a
    re-check of current state, never a redo of the action, since by the
    time either phase runs the action has already fired."""
    attempt = 0
    while True:
        outcome = attempt_fn()
        if outcome[0] == "ok":
            return True, outcome[1], None

        _, trigger, detail = outcome
        rule = overrides.get(trigger) or ctx.artifact.escalation_policy.default.get(trigger)
        attempt += 1
        if attempt <= rule.retry:
            ctx.log.event("retry", step_id=step.step_id, trigger=trigger, attempt=attempt, detail=detail)
            continue

        if rule.then == "escalate":
            sensitive = redaction.find_sensitive_fields(ctx.page, ctx.artifact.evidence_policy.redact_fields)
            screenshot_path = evidence.save_screenshot(
                ctx.page, ctx.evidence_dir, step.step_id, trigger, mask_locators=[f.locator for f in sensitive]
            )
            ctx.log.event(
                "escalate", step_id=step.step_id, trigger=trigger, detail=detail, screenshot=screenshot_path
            )
            escalation.pause_for_escalation(ctx.run_id, step.step_id, trigger, screenshot_path)
            ctx.log.event("escalation_resumed", step_id=step.step_id)
            attempt = 0  # resumed run gets a fresh retry budget for this phase
            continue

        screenshot_path = None
        if "hard_failure" in ctx.artifact.evidence_policy.screenshot_on:
            sensitive = redaction.find_sensitive_fields(ctx.page, ctx.artifact.evidence_policy.redact_fields)
            screenshot_path = evidence.save_screenshot(
                ctx.page, ctx.evidence_dir, step.step_id, "hard_failure", mask_locators=[f.locator for f in sensitive]
            )
        result = HardFailureResult(
            step_id=step.step_id,
            expected=step.description,
            observed=detail or trigger,
            screenshot_path=screenshot_path,
        )
        return False, None, result


def _act_once(step: Step, ctx: RunContext) -> tuple:
    """Resolves the step's locator and performs the action — nothing else.
    Once ACTION_REGISTRY's call returns without raising, the action has
    fired; any failure from here on (see _settle_once) must never cause
    this function to be called again for this step."""
    try:
        resolved: ResolvedLocator | None = None
        if step.locator is not None:
            resolved = resolve_locator(step.locator.strategies, ctx)

        guardrails.check_action_type(step.action.type, ctx.artifact.guardrails.allowed_action_types)
        before_url = ctx.page.url
        ACTION_REGISTRY.get(step.action.type)(step.action, resolved, ctx)
        return ("ok", (resolved, before_url))
    except PWTimeoutError as exc:
        return ("failure", "on_step_timeout", str(exc)[:300])
    except (LocatorResolutionError, guardrails.GuardrailViolation) as exc:
        return ("failure", "on_hard_failure", str(exc)[:300])
    except Exception as exc:  # genuinely unexpected — hard failure trigger
        return ("failure", "on_hard_failure", f"{type(exc).__name__}: {exc}"[:300])


def _settle_once(step: Step, ctx: RunContext, resolved: ResolvedLocator | None, before_url: str) -> tuple:
    """Everything that can only fail *after* the action already fired: the
    post-action route guardrail check, then the optional settle-wait.
    Never touches ACTION_REGISTRY — a retry or escalation-resume here only
    re-checks the route and re-waits, it never redoes the action."""
    try:
        if ctx.page.url != before_url:
            guardrails.check_route(ctx.page.url, ctx.allowed_origin, ctx.artifact.guardrails.allowlist_routes)

        if step.wait is not None and step.wait.type == "network_idle":
            ctx.page.wait_for_load_state("networkidle", timeout=step.wait.timeout_ms)

        return ("ok", resolved)
    except PWTimeoutError as exc:
        return ("failure", "on_step_timeout", str(exc)[:300])
    except guardrails.GuardrailViolation as exc:
        return ("failure", "on_hard_failure", str(exc)[:300])
    except Exception as exc:  # genuinely unexpected — hard failure trigger
        return ("failure", "on_hard_failure", f"{type(exc).__name__}: {exc}"[:300])


def _check_once(step: Step, ctx: RunContext, resolved: ResolvedLocator | None) -> tuple:
    try:
        # Proactive interrupt: an unexpected dialog is itself the anomaly,
        # even if the page underneath happens to still satisfy the literal
        # checkpoint query (a modal overlay doesn't reliably fail a plain
        # visibility check) — checked before, not derived from, checkpoint
        # failure.
        if _has_unexpected_dialog(ctx):
            ctx.log.event("unrecognized_dialog", step_id=step.step_id)
            return ("failure", "on_unrecognized_dialog", "unexpected dialog visible")

        checkpoint_timeout = step.wait.timeout_ms if (step.wait and step.wait.type == "visible") else 2000
        check = evaluate_checkpoint(step.checkpoint, ctx, resolved, timeout_ms=checkpoint_timeout)
        ctx.log.event(
            "checkpoint", step_id=step.step_id, matched=check.matched, outcome_code=check.outcome_code, detail=check.detail
        )

        if check.matched and check.outcome_code:
            return ("ok", BusinessOutcomeResult(outcome_code=check.outcome_code, step_id=step.step_id))
        if check.matched:
            return ("ok", None)
        return ("failure", "on_checkpoint_failure", check.detail)
    except PWTimeoutError as exc:
        return ("failure", "on_step_timeout", str(exc)[:300])
    except Exception as exc:
        return ("failure", "on_hard_failure", f"{type(exc).__name__}: {exc}"[:300])


def _has_unexpected_dialog(ctx: RunContext) -> bool:
    for role in ("dialog", "alertdialog"):
        try:
            ctx.page.get_by_role(role).first.wait_for(state="visible", timeout=500)
            return True
        except PWTimeoutError:
            continue
    return False
