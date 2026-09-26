"""Agent adapter interface: bounded, non-authoritative workers.

Adapters perform bounded work and return structured results. They never
advance lifecycle state directly; only the orchestrator does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from harness.orchestrator.model import State


@dataclass(frozen=True)
class AgentContext:
    run_id: str
    task_id: str
    phase: State  # PLAN, IMPLEMENT, or REPAIR
    attempt: int
    workspace: Path  # isolated working copy
    manifest: dict
    context_digest: str
    permissions: frozenset[str]  # effective grants for this phase
    seed: int


@dataclass(frozen=True)
class AgentResult:
    run_id: str
    phase: State
    attempt: int
    status: str  # "succeeded" | "failed"
    artifact_digest: str = ""
    changed_paths: tuple[str, ...] = ()
    capabilities_used: tuple[str, ...] = ()
    requested_capabilities: tuple[str, ...] = ()
    summary: str = ""
    error: str = ""


class AgentTimeout(Exception):
    """Raised when an adapter exceeds its execution deadline."""


class AgentAdapter(Protocol):
    adapter_id: str

    def execute(self, context: AgentContext) -> AgentResult: ...
