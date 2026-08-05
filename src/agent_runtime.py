"""Config-driven registry and dependency graph for the multi-agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .schemas import ContractError


@dataclass(frozen=True)
class AgentSpec:
    name: str
    module: str
    callable_name: str
    stage: str
    depends_on: tuple[str, ...]


class AgentRegistry:
    """Loads agent identity and dependency metadata without hard-coded modules."""

    def __init__(self, specs: tuple[AgentSpec, ...]) -> None:
        self._specs = specs
        self._by_name = {spec.name: spec for spec in specs}
        if len(self._by_name) != len(specs):
            raise ContractError("Agent registry contains duplicate names")
        for spec in specs:
            unknown = set(spec.depends_on).difference(self._by_name)
            if unknown:
                raise ContractError(f"Agent {spec.name} has unknown dependencies: {unknown}")

    @classmethod
    def from_file(cls, path: Path) -> "AgentRegistry":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"Cannot load agent registry {path}: {exc}") from exc
        raw_agents = data.get("agents")
        if not isinstance(raw_agents, list) or not raw_agents:
            raise ContractError("Agent registry must contain an agents list")
        specs: list[AgentSpec] = []
        for raw in raw_agents:
            if not isinstance(raw, Mapping):
                raise ContractError("Each registry agent must be an object")
            name = raw.get("name")
            module = raw.get("module")
            callable_name = raw.get("callable")
            stage = raw.get("stage")
            depends_on = raw.get("depends_on", [])
            if not all(isinstance(value, str) and value for value in (name, module, callable_name, stage)):
                raise ContractError("Agent registry has a missing string field")
            if not isinstance(depends_on, list) or not all(isinstance(item, str) for item in depends_on):
                raise ContractError(f"Agent {name} has invalid depends_on")
            specs.append(AgentSpec(name, module, callable_name, stage, tuple(depends_on)))
        return cls(tuple(specs))

    @property
    def specs(self) -> tuple[AgentSpec, ...]:
        return self._specs

    def resolve(self, spec: AgentSpec) -> Callable[..., Any]:
        try:
            module = importlib.import_module(spec.module)
        except ModuleNotFoundError as exc:
            raise ContractError(f"Unable to import registered agent {spec.module}") from exc
        function = getattr(module, spec.callable_name, None)
        if not callable(function):
            raise ContractError(
                f"Registered agent {spec.name} lacks {spec.callable_name}(...)"
            )
        return function


class TaskGraph:
    """A dependency DAG that exposes parallel layers for audit and scheduling."""

    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def layers(self) -> tuple[tuple[AgentSpec, ...], ...]:
        pending = {spec.name: spec for spec in self._registry.specs}
        completed: set[str] = set()
        layers: list[tuple[AgentSpec, ...]] = []
        while pending:
            ready = tuple(
                spec
                for spec in pending.values()
                if set(spec.depends_on).issubset(completed)
            )
            if not ready:
                raise ContractError("Agent registry dependency graph contains a cycle")
            layers.append(ready)
            completed.update(spec.name for spec in ready)
            for spec in ready:
                del pending[spec.name]
        return tuple(layers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layers": [
                [
                    {
                        "agent_name": spec.name,
                        "stage": spec.stage,
                        "depends_on": list(spec.depends_on),
                    }
                    for spec in layer
                ]
                for layer in self.layers()
            ]
        }
