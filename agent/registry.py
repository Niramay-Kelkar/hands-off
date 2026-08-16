"""Generic string-keyed function registry.

Used by agent.locators, agent.checkpoints, and agent.actions so that
adding a new locator kind / checkpoint type / action type means
registering one small function, never editing a central dispatcher.
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

F = TypeVar("F")


class Registry(Generic[F]):
    def __init__(self, name: str):
        self._name = name
        self._items: dict[str, F] = {}

    def register(self, key: str) -> Callable[[F], F]:
        def decorator(fn: F) -> F:
            if key in self._items:
                raise ValueError(f"{self._name} registry already has a handler for {key!r}")
            self._items[key] = fn
            return fn

        return decorator

    def get(self, key: str) -> F:
        try:
            return self._items[key]
        except KeyError:
            raise KeyError(
                f"no {self._name} handler registered for {key!r}; known: {sorted(self._items)}"
            ) from None

    def keys(self) -> list[str]:
        return sorted(self._items)
