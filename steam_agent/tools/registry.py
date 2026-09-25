from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable


ToolMap = dict[str, Callable[..., Any]]


class FixtureToolRegistry:
    """Deterministic tool responses for real-model Agent evaluation."""

    def __init__(self, payload: dict[str, Any]):
        self._responses = deepcopy(payload.get("tools", {}))
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_path(cls, path: Path) -> "FixtureToolRegistry":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def as_tool_map(self, tool_names: list[str]) -> ToolMap:
        return {name: self._callable(name) for name in tool_names}

    def _callable(self, name: str):
        def call(**kwargs):
            self.calls.append({"tool": name, "arguments": kwargs})
            configured = self._responses.get(name)
            if configured is None:
                return {"error": f"fixture_missing:{name}"}
            if isinstance(configured, list):
                if not configured:
                    return {"error": f"fixture_exhausted:{name}"}
                if len(configured) == 1:
                    return deepcopy(configured[0])
                return deepcopy(configured.pop(0))
            return deepcopy(_resolve_route(configured, kwargs))

        call.__name__ = name
        return call


def _resolve_route(configured: Any, arguments: dict[str, Any]) -> Any:
    if not isinstance(configured, dict) or "routes" not in configured:
        return configured
    for route in configured.get("routes", []):
        if _matches(route.get("when", {}), arguments):
            return route.get("result")
    return configured.get("default", {"results": []})


def _matches(expected: dict[str, Any], arguments: dict[str, Any]) -> bool:
    for key, value in expected.items():
        if key.endswith("_contains_any"):
            argument = str(arguments.get(key.removesuffix("_contains_any"), "")).lower()
            if not any(str(item).lower() in argument for item in value):
                return False
        elif arguments.get(key) != value:
            return False
    return True
