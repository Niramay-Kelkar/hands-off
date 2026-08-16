"""LOCATOR_REGISTRY: one small function per locator "kind", tried in
priority order by resolve_locator(). Every attempt (success or failure)
is logged — this is the drift-detection signal described in CLAUDE.md:
a step that keeps falling through to its lower-priority strategy is an
early warning of UI change before it becomes an outright failure.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable

from playwright.sync_api import Locator, Page

from agent.models import LocatorStrategyModel, get_field
from agent.registry import Registry
from agent.template import substitute

if TYPE_CHECKING:
    from agent.context import RunContext

LocatorStrategyFn = Callable[[Page, LocatorStrategyModel, dict[str, str]], Locator]
LOCATOR_REGISTRY: Registry[LocatorStrategyFn] = Registry("locator")


class LocatorResolutionError(RuntimeError):
    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        super().__init__(f"every locator strategy failed: {attempts}")


class ResolvedLocator:
    def __init__(self, locator: Locator, strategy_kind: str, priority: int, role: str | None = None):
        self.locator = locator
        self.strategy_kind = strategy_kind
        self.priority = priority
        self.role = role


@LOCATOR_REGISTRY.register("accessibility")
def _accessibility(scope: Page, strategy: LocatorStrategyModel, params: dict[str, str]) -> Locator:
    role = get_field(strategy, "role")
    kwargs: dict = {}
    name = get_field(strategy, "name")
    name_matches = get_field(strategy, "name_matches")
    name_contains = get_field(strategy, "name_contains")
    if name:
        # exact=True: an artifact compiled from a discovery trajectory was
        # verified against exact-match resolution (see discovery_tools.py)
        # — replay must resolve the same `name` locator the same way, or a
        # capability could behave differently at replay time than what was
        # actually proven during discovery.
        kwargs["name"] = substitute(name, params)
        kwargs["exact"] = True
    elif name_matches:
        kwargs["name"] = re.compile(substitute(name_matches, params))
    elif name_contains:
        kwargs["name"] = substitute(name_contains, params)
        kwargs["exact"] = False
    return scope.get_by_role(role, **kwargs)


@LOCATOR_REGISTRY.register("text_label")
def _text_label(scope: Page, strategy: LocatorStrategyModel, params: dict[str, str]) -> Locator:
    label = substitute(get_field(strategy, "label"), params)
    return scope.get_by_label(label)


@LOCATOR_REGISTRY.register("css")
def _css(scope: Page, strategy: LocatorStrategyModel, params: dict[str, str]) -> Locator:
    selector = substitute(get_field(strategy, "selector"), params)
    return scope.locator(selector)


def resolve_locator(
    strategies: list[LocatorStrategyModel], ctx: "RunContext", *, timeout_ms: int = 3000
) -> ResolvedLocator:
    attempts: list[dict] = []
    for strat in sorted(strategies, key=lambda s: s.priority):
        fn = LOCATOR_REGISTRY.get(strat.kind)
        try:
            locator = fn(ctx.page, strat, ctx.params).first
            locator.wait_for(state="attached", timeout=timeout_ms)
            attempts.append({"kind": strat.kind, "priority": strat.priority, "success": True})
            ctx.log.event("locator_resolved", strategy_kind=strat.kind, priority=strat.priority, attempts=attempts)
            return ResolvedLocator(
                locator=locator, strategy_kind=strat.kind, priority=strat.priority, role=get_field(strat, "role")
            )
        except Exception as exc:
            attempts.append(
                {"kind": strat.kind, "priority": strat.priority, "success": False, "error": str(exc)[:200]}
            )

    ctx.log.event("locator_resolution_failed", attempts=attempts)
    raise LocatorResolutionError(attempts)
