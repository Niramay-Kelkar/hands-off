"""CHECKPOINT_REGISTRY: one function per condition type, shared between
step `checkpoint`s, `any_of.conditions[]`, and `expected_outcomes[].detection`.

`outcome_match` and `any_of` recurse back into this same registry — this
is what keeps expected-outcome detection data-driven from the artifact
instead of hardcoded per capability (CLAUDE.md is explicit that this must
not collapse into generic try/catch in the executor).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from playwright.sync_api import TimeoutError as PWTimeoutError

from agent.locators import ResolvedLocator
from agent.models import ConditionSpec, get_field
from agent.registry import Registry
from agent.template import substitute

if TYPE_CHECKING:
    from agent.context import RunContext


@dataclass
class ConditionResult:
    matched: bool
    outcome_code: str | None = None
    detail: str | None = None


ConditionFn = Callable[[ConditionSpec, "RunContext", "ResolvedLocator | None", int], ConditionResult]
CHECKPOINT_REGISTRY: Registry[ConditionFn] = Registry("checkpoint")


def evaluate_checkpoint(
    checkpoint: ConditionSpec, ctx: "RunContext", resolved_locator: "ResolvedLocator | None", *, timeout_ms: int = 2000
) -> ConditionResult:
    fn = CHECKPOINT_REGISTRY.get(checkpoint.type)
    return fn(checkpoint, ctx, resolved_locator, timeout_ms)


@CHECKPOINT_REGISTRY.register("field_value_equals")
def _field_value_equals(spec: ConditionSpec, ctx: "RunContext", resolved_locator, timeout_ms: int) -> ConditionResult:
    if resolved_locator is None:
        raise ValueError("field_value_equals requires a step-level locator")
    expected = substitute(get_field(spec, "expected"), ctx.params)
    actual = resolved_locator.locator.input_value()
    if actual == expected:
        return ConditionResult(matched=True)
    return ConditionResult(matched=False, detail=f"expected {expected!r}, observed {actual!r}")


@CHECKPOINT_REGISTRY.register("any_of")
def _any_of(spec: ConditionSpec, ctx: "RunContext", resolved_locator, timeout_ms: int) -> ConditionResult:
    details = []
    for cond in spec.conditions or []:
        result = evaluate_checkpoint(cond, ctx, resolved_locator, timeout_ms=timeout_ms)
        if result.matched:
            return result
        details.append(result.detail or cond.type)
    return ConditionResult(matched=False, detail="; ".join(details))


@CHECKPOINT_REGISTRY.register("element_visible")
def _element_visible(spec: ConditionSpec, ctx: "RunContext", resolved_locator, timeout_ms: int) -> ConditionResult:
    role = get_field(spec, "role")
    name = get_field(spec, "name")
    name_contains = get_field(spec, "name_contains")
    kwargs: dict = {}
    if name:
        kwargs["name"] = substitute(name, ctx.params)
    elif name_contains:
        kwargs["name"] = substitute(name_contains, ctx.params)
        kwargs["exact"] = False
    locator = ctx.page.get_by_role(role, **kwargs).first
    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
        return ConditionResult(matched=True)
    except PWTimeoutError:
        return ConditionResult(matched=False, detail=f"no visible role={role!r} matching {kwargs}")


@CHECKPOINT_REGISTRY.register("outputs_non_empty")
def _outputs_non_empty(spec: ConditionSpec, ctx: "RunContext", resolved_locator, timeout_ms: int) -> ConditionResult:
    fields = get_field(spec, "fields", [])
    missing = [f for f in fields if not ctx.outputs.get(f)]
    if missing:
        return ConditionResult(matched=False, detail=f"empty outputs: {missing}")
    return ConditionResult(matched=True)


@CHECKPOINT_REGISTRY.register("text_present")
def _text_present(spec: ConditionSpec, ctx: "RunContext", resolved_locator, timeout_ms: int) -> ConditionResult:
    value = substitute(get_field(spec, "value"), ctx.params)
    scope = get_field(spec, "scope", "page")

    if scope == "page":
        # main frame only — an iframe renders a separate document, so its
        # text is never part of this document's DOM regardless of scope
        haystack = ctx.page.locator("body").inner_text()
    else:
        region = ctx.page.get_by_role("region", name=scope).first
        try:
            region.wait_for(state="visible", timeout=1000)
            haystack = region.inner_text()
        except PWTimeoutError:
            ctx.log.event("scope_region_not_found", scope=scope)
            haystack = ctx.page.locator("body").inner_text()

    if value in haystack:
        return ConditionResult(matched=True)
    return ConditionResult(matched=False, detail=f"text {value!r} not found in scope {scope!r}")


@CHECKPOINT_REGISTRY.register("outcome_match")
def _outcome_match(spec: ConditionSpec, ctx: "RunContext", resolved_locator, timeout_ms: int) -> ConditionResult:
    codes = get_field(spec, "outcome_codes", [])
    tried = []
    for code in codes:
        outcome = ctx.artifact.expected_outcome(code)
        if outcome is None:
            continue
        result = evaluate_checkpoint(outcome.detection, ctx, resolved_locator, timeout_ms=timeout_ms)
        if result.matched:
            return ConditionResult(matched=True, outcome_code=code, detail=result.detail)
        tried.append(code)
    return ConditionResult(matched=False, detail=f"none of {tried} matched")
