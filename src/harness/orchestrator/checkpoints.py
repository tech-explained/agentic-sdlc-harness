"""Human checkpoint hooks: scripted (measured runs) and console (interactive).

A checkpoint records approve / deny / override. An override requires a
non-empty rationale and a declared exception identifier; it records acceptance
of the exception while the original failed control stays visible in the log.
Overrides can never convert a failed gate into a pass.
"""

from __future__ import annotations

import uuid
from typing import Any

from harness.orchestrator.model import Run

VALID_ACTIONS = ("approve", "deny", "override")


class ScriptedCheckpoint:
    """Deterministic checkpoint for measured runs.

    decisions: {(task_id, checkpoint): (action, rationale, exception_id)}
    """

    def __init__(
        self,
        decisions: dict[tuple[str, str], tuple[str, str | None, str | None]] | None = None,
    ) -> None:
        self._decisions = decisions or {}
        self._pending: dict[str, dict[str, Any]] = {}

    def request(self, run: Run, reason: str) -> str:
        checkpoint_id = "cp-" + uuid.uuid4().hex[:12]
        self._pending[checkpoint_id] = {"run_id": run.run_id, "reason": reason}
        return checkpoint_id

    def decide(self, task_id: str, checkpoint: str):
        """Return the scripted (action, rationale, exception_id), or None."""
        return self._decisions.get((task_id, checkpoint))

    def resolve(
        self,
        checkpoint_id: str,
        action: str,
        actor: str,
        rationale: str | None = None,
        exception_id: str | None = None,
    ) -> dict[str, Any]:
        if checkpoint_id not in self._pending:
            raise KeyError(f"unknown checkpoint_id: {checkpoint_id}")
        if action not in VALID_ACTIONS:
            raise ValueError(f"invalid action: {action!r}")
        if action == "override" and not (rationale and exception_id):
            raise ValueError("override requires rationale and exception_id")
        pending = self._pending.pop(checkpoint_id)
        return {
            "checkpoint_id": checkpoint_id,
            "run_id": pending["run_id"],
            "action": action,
            "actor": actor,
            "rationale": rationale,
            "exception_id": exception_id,
        }


class ConsoleCheckpoint:
    """Interactive checkpoint for local (unmeasured) runs."""

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}

    def request(self, run: Run, reason: str) -> str:
        checkpoint_id = "cp-" + uuid.uuid4().hex[:12]
        self._pending[checkpoint_id] = {"run_id": run.run_id, "reason": reason}
        print(f"[checkpoint {checkpoint_id}] run={run.run_id} state={run.state.value}")
        print(f"  reason: {reason}")
        return checkpoint_id

    def resolve(
        self,
        checkpoint_id: str,
        action: str = "",
        actor: str = "human:console",
        rationale: str | None = None,
        exception_id: str | None = None,
    ) -> dict[str, Any]:
        if checkpoint_id not in self._pending:
            raise KeyError(f"unknown checkpoint_id: {checkpoint_id}")
        if not action:
            action = input("  action [approve/deny/override]: ").strip().lower()
        if action not in VALID_ACTIONS:
            raise ValueError(f"invalid action: {action!r}")
        if action == "override":
            rationale = rationale or input("  rationale (required): ").strip()
            exception_id = exception_id or input("  exception id (required): ").strip()
            if not (rationale and exception_id):
                raise ValueError("override requires rationale and exception_id")
        pending = self._pending.pop(checkpoint_id)
        return {
            "checkpoint_id": checkpoint_id,
            "run_id": pending["run_id"],
            "action": action,
            "actor": actor,
            "rationale": rationale,
            "exception_id": exception_id,
        }
