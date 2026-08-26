"""ACTION_REGISTRY: one function per step action type.

`type` and `click` act on the step-level ResolvedLocator the engine
already resolved. `navigate` ignores it and calls page.goto(). `extract`
ignores it too (its step has no top-level `locator` — each output field
carries its own strategy list) and resolves each field's locator itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from agent.locators import ResolvedLocator, resolve_locator
from agent.models import ActionModel, Capability
from agent.registry import Registry
from agent.template import substitute

if TYPE_CHECKING:
    from agent.context import RunContext

ActionFn = Callable[[ActionModel, "ResolvedLocator | None", "RunContext"], None]
ACTION_REGISTRY: Registry[ActionFn] = Registry("action")


@ACTION_REGISTRY.register("type")
def _type(action: ActionModel, resolved: ResolvedLocator | None, ctx: "RunContext") -> None:
    if resolved is None:
        raise ValueError("type action requires a step-level locator")
    value = substitute(action.value or "", ctx.params)
    resolved.locator.fill(value)
    ctx.log.event("action", type="type", value=value)


@ACTION_REGISTRY.register("click")
def _click(action: ActionModel, resolved: ResolvedLocator | None, ctx: "RunContext") -> None:
    if resolved is None:
        raise ValueError("click action requires a step-level locator")
    resolved.locator.click()
    ctx.log.event("action", type="click")


@ACTION_REGISTRY.register("navigate")
def _navigate(action: ActionModel, resolved: ResolvedLocator | None, ctx: "RunContext") -> None:
    url = substitute(action.value or "", ctx.params)
    ctx.page.goto(url)
    ctx.log.event("action", type="navigate", url=url)


@ACTION_REGISTRY.register("extract")
def _extract(action: ActionModel, resolved: ResolvedLocator | None, ctx: "RunContext") -> None:
    for extract_field in action.fields or []:
        field_locator = resolve_locator(extract_field.locator.strategies, ctx)
        raw = _read_extracted_text(field_locator)
        ctx.outputs[extract_field.output] = _coerce_output(ctx.artifact, extract_field.output, raw)
        ctx.log.event(
            "action", type="extract", output=extract_field.output, strategy_kind=field_locator.strategy_kind
        )


def _read_extracted_text(field_locator: ResolvedLocator) -> str:
    """A resolved cell whose own text ends in ':' is a label anchor (e.g.
    "Name:") in this app's label/value table layout — the value lives in
    the next sibling cell. Same signal agent.redaction's label<->value scan
    uses; label cells carry the same role as value cells here (both
    "cell"), so role alone can't tell them apart. A locator that resolves
    directly to a value cell (e.g. a css fallback) returns its own text."""
    own_text = field_locator.locator.inner_text().strip()
    if own_text.endswith(":"):
        sibling = field_locator.locator.locator("xpath=following-sibling::*[1]")
        if sibling.count() > 0:
            return sibling.inner_text().strip()
    return own_text


def _coerce_output(artifact: Capability, name: str, raw: str) -> object:
    spec = next((o for o in artifact.outputs if o.name == name), None)
    if spec is not None and spec.type == "money":
        cleaned = raw.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return raw
    return raw
