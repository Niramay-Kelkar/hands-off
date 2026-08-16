"""Discovery tool vocabulary: matches agent.actions.ACTION_REGISTRY
(click/type/navigate/extract) plus `done`, which the replay engine
doesn't need.

Every tool call resolves to a ToolOutcome with a populated `detail` —
never a bare ok/fail — so the model always gets something concrete to
act on: a success confirmation, an ambiguous/zero-match error asking it
to narrow its description, or a guardrail-block explanation. Uncaught
exceptions never reach the model as a crash; they're caught by the
caller in agent.discovery and turned into the same ToolOutcome shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from playwright.sync_api import Locator, TimeoutError as PWTimeoutError

from agent.context import DiscoveryContext
from agent.guardrails import GuardrailViolation, check_route
from agent.registry import Registry

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "click",
        "description": (
            "Click an element on the current page, identified by its accessibility role and "
            "accessible name as shown in the current observation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "ARIA role of the target element, e.g. 'button', 'link'."},
                "name": {
                    "type": "string",
                    "description": "Accessible name of the target element, exactly as shown in the observation.",
                },
            },
            "required": ["role", "name"],
        },
    },
    {
        "name": "type",
        "description": "Type a value into a text input field, identified by its accessibility role and accessible name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "ARIA role of the target field, typically 'textbox'."},
                "name": {
                    "type": "string",
                    "description": "Accessible name of the target field, exactly as shown in the observation.",
                },
                "value": {
                    "type": "string",
                    "description": "The literal text to type — the real value from the goal, not a placeholder.",
                },
            },
            "required": ["role", "name", "value"],
        },
    },
    {
        "name": "navigate",
        "description": (
            "Navigate directly to a URL. Prefer clicking elements on the page; use this only when "
            "direct navigation is genuinely the right step."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The URL to navigate to."}},
            "required": ["url"],
        },
    },
    {
        "name": "extract",
        "description": (
            "Record a value visible on the current page as a named output. The element is re-read "
            "directly from the live page, not trusted from your description of it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "ARIA role of the element holding the value, e.g. 'cell', 'text'.",
                },
                "name": {
                    "type": "string",
                    "description": "Accessible name of the element holding the value, exactly as shown in the observation.",
                },
                "output_name": {
                    "type": "string",
                    "description": "Name to store this value under, e.g. 'member_name'.",
                },
            },
            "required": ["role", "name", "output_name"],
        },
    },
    {
        "name": "done",
        "description": (
            "Call once the goal has been fully achieved. Reference outputs already captured via "
            "successful extract calls by name — do not restate their values here; they are pulled "
            "from what extract actually verified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "output_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Names of outputs already captured via successful extract calls, "
                        "e.g. ['member_name', 'savings_balance']."
                    ),
                },
                "summary": {"type": "string", "description": "Brief description of what was accomplished."},
            },
            "required": ["output_names", "summary"],
        },
    },
]


@dataclass
class ToolOutcome:
    ok: bool
    detail: str
    ended: bool = False
    outputs: dict[str, str] | None = None


DiscoveryToolFn = Callable[[dict[str, Any], DiscoveryContext], ToolOutcome]
DISCOVERY_TOOL_REGISTRY: Registry[DiscoveryToolFn] = Registry("discovery_tool")


def _resolve_unique(page, role: str, name: str) -> tuple[Locator | None, str | None]:
    """0 matches or >1 matches comes back as a clean, actionable message —
    never an uncaught exception that crashes the run."""
    # exact=True: this app's nested-table cells compute compound accessible
    # names by concatenating descendant text, so a leaf cell's exact name
    # (e.g. "Jane Doe") is always a substring of its ancestor wrapper
    # cells' names too — substring matching would make every leaf value
    # structurally ambiguous against its own wrappers, no matter how the
    # model refines it.
    locator = page.get_by_role(role, name=name, exact=True)
    try:
        locator.first.wait_for(state="attached", timeout=3000)
    except PWTimeoutError:
        return None, f"no element found with role={role!r} name={name!r}; re-check the current observation and try again"
    count = locator.count()
    if count > 1:
        return None, (
            f"{count} elements matched role={role!r} name={name!r}; "
            "make the name more specific so it uniquely identifies one element"
        )
    return locator.first, None


def _post_action_route_check(ctx: DiscoveryContext, before_url: str, success_detail: str) -> ToolOutcome:
    """Mirrors the replay engine's post-action route check, but discovery
    additionally reverts the navigation on violation — replay treats an
    off-allowlist route as an anomaly worth stopping for, but discovery is
    open-ended exploration, so containing and continuing (rather than
    aborting the whole run over one guardrail slip) is the more useful
    behavior here."""
    if ctx.page.url == before_url:
        return ToolOutcome(ok=True, detail=success_detail)
    try:
        check_route(ctx.page.url, ctx.allowed_origin, ctx.allowlist_routes)
    except GuardrailViolation as exc:
        blocked_url = ctx.page.url
        ctx.page.goto(before_url)
        return ToolOutcome(
            ok=False,
            detail=(
                f"{success_detail} — but this navigated to {blocked_url!r}, which is blocked by "
                f"guardrails ({exc}); reverted to {before_url!r}"
            ),
        )
    return ToolOutcome(ok=True, detail=success_detail)


@DISCOVERY_TOOL_REGISTRY.register("click")
def _click(tool_input: dict[str, Any], ctx: DiscoveryContext) -> ToolOutcome:
    role, name = tool_input["role"], tool_input["name"]
    locator, err = _resolve_unique(ctx.page, role, name)
    if err:
        return ToolOutcome(ok=False, detail=err)
    before_url = ctx.page.url
    locator.click()
    return _post_action_route_check(ctx, before_url, f"clicked role={role!r} name={name!r}")


@DISCOVERY_TOOL_REGISTRY.register("type")
def _type(tool_input: dict[str, Any], ctx: DiscoveryContext) -> ToolOutcome:
    role, name, value = tool_input["role"], tool_input["name"], tool_input["value"]
    locator, err = _resolve_unique(ctx.page, role, name)
    if err:
        return ToolOutcome(ok=False, detail=err)
    locator.fill(value)
    return ToolOutcome(ok=True, detail=f"typed {value!r} into role={role!r} name={name!r}")


@DISCOVERY_TOOL_REGISTRY.register("navigate")
def _navigate(tool_input: dict[str, Any], ctx: DiscoveryContext) -> ToolOutcome:
    url = tool_input["url"]
    try:
        check_route(url, ctx.allowed_origin, ctx.allowlist_routes)
    except GuardrailViolation as exc:
        return ToolOutcome(ok=False, detail=f"navigation to {url!r} blocked by guardrails: {exc}")
    ctx.page.goto(url)
    return ToolOutcome(ok=True, detail=f"navigated to {url}")


@DISCOVERY_TOOL_REGISTRY.register("extract")
def _extract(tool_input: dict[str, Any], ctx: DiscoveryContext) -> ToolOutcome:
    role, name, output_name = tool_input["role"], tool_input["name"], tool_input["output_name"]
    locator, err = _resolve_unique(ctx.page, role, name)
    if err:
        return ToolOutcome(ok=False, detail=err)
    value = locator.inner_text().strip()
    ctx.outputs[output_name] = value
    return ToolOutcome(ok=True, detail=f"extracted {output_name}={value!r}")


@DISCOVERY_TOOL_REGISTRY.register("done")
def _done(tool_input: dict[str, Any], ctx: DiscoveryContext) -> ToolOutcome:
    output_names = tool_input.get("output_names") or []
    summary = tool_input.get("summary") or ""

    missing = [name for name in output_names if name not in ctx.outputs]
    if missing:
        return ToolOutcome(
            ok=False,
            detail=(
                f"output_names {missing} were never successfully extracted in this run; "
                "call extract for each of them first, then call done again"
            ),
        )

    outputs = {name: ctx.outputs[name] for name in output_names}
    return ToolOutcome(ok=True, detail=summary, ended=True, outputs=outputs)
