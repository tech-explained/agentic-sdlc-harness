"""Deterministic stub agent suite (mandatory for the measured v1 run).

Scripted, reproducible behaviors: the stub copies the fixture's solution tree
into the isolated workspace. No network, no randomness, no improvisation.
Fault injection is applied by the evaluation harness as explicit transforms,
never by the stub.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from harness.agents.base import AgentContext, AgentResult
from harness.orchestrator.model import State
from harness.orchestrator.policy import FS_READ, FS_WRITE_ISOLATED
from harness.util import artifact_digest


class StubAgent:
    """Deterministic scripted agent.

    behavior="clean": normal scripted implementation.
    behavior="request-network": declares an ungranted capability instead of
        acting, exercising the permission-denial path (no files are written).
    """

    adapter_id = "stub"

    def __init__(self, fixtures_root: Path, behavior: str = "clean") -> None:
        if behavior not in ("clean", "request-network"):
            raise ValueError(f"unknown stub behavior: {behavior}")
        self.fixtures_root = Path(fixtures_root)
        self.behavior = behavior

    # ------------------------------------------------------------------

    def execute(self, context: AgentContext) -> AgentResult:
        task_dir = self.fixtures_root / "tasks" / context.task_id
        if not task_dir.is_dir():
            return AgentResult(
                run_id=context.run_id,
                phase=context.phase,
                attempt=context.attempt,
                status="failed",
                error=f"unknown task: {context.task_id}",
            )
        if self.behavior == "request-network" and context.phase in (
            State.IMPLEMENT,
            State.REPAIR,
        ):
            # Declare the excess capability instead of acting.
            return AgentResult(
                run_id=context.run_id,
                phase=context.phase,
                attempt=context.attempt,
                status="succeeded",
                artifact_digest="",
                changed_paths=(),
                capabilities_used=(FS_READ,),
                requested_capabilities=("network",),
                summary="stub requested network access (permission-denial probe)",
            )
        if context.phase == State.PLAN:
            return self._plan(context)
        if context.phase in (State.IMPLEMENT, State.REPAIR):
            return self._implement(context, task_dir)
        return AgentResult(
            run_id=context.run_id,
            phase=context.phase,
            attempt=context.attempt,
            status="failed",
            error=f"stub does not implement phase {context.phase.value}",
        )

    # ------------------------------------------------------------------

    def _plan(self, context: AgentContext) -> AgentResult:
        goal = context.manifest.get("goal", "")
        summary = (
            f"Plan for {context.task_id}: {goal} "
            "(scripted plan: implement the declared change, then verify)."
        )
        return AgentResult(
            run_id=context.run_id,
            phase=context.phase,
            attempt=context.attempt,
            status="succeeded",
            capabilities_used=(FS_READ,),
            summary=summary,
        )

    def _implement(self, context: AgentContext, task_dir: Path) -> AgentResult:
        solution_dir = task_dir / "solution"
        workspace = context.workspace
        if context.phase == State.REPAIR:
            self._restore(workspace, task_dir)
        changed = self._copy_solution(solution_dir, workspace)
        digest = artifact_digest(workspace, changed)
        return AgentResult(
            run_id=context.run_id,
            phase=context.phase,
            attempt=context.attempt,
            status="succeeded",
            artifact_digest=digest,
            changed_paths=tuple(changed),
            capabilities_used=(FS_READ, FS_WRITE_ISOLATED),
            summary=f"stub wrote {len(changed)} solution files (attempt {context.attempt})",
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _copy_solution(solution_dir: Path, workspace: Path) -> list[str]:
        changed: list[str] = []
        for src in sorted(solution_dir.rglob("*")):
            if src.is_file():
                rel = src.relative_to(solution_dir).as_posix()
                dest = workspace / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                changed.append(rel)
        return changed

    def _restore(self, workspace: Path, task_dir: Path) -> None:
        """Scripted repair: restore the workspace to seed+solution state.

        Re-copies the solution (overwriting any injected fault) and removes
        any file that is neither part of the seed tree nor the solution set.
        """
        seed_dir = task_dir / "seed"
        solution_dir = task_dir / "solution"
        seed_files = {
            p.relative_to(seed_dir).as_posix()
            for p in seed_dir.rglob("*")
            if p.is_file()
        }
        solution_files = {
            p.relative_to(solution_dir).as_posix()
            for p in solution_dir.rglob("*")
            if p.is_file()
        }
        for p in sorted(workspace.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(workspace).as_posix()
            if "__pycache__" in rel or rel.endswith((".pyc", ".pyo")):
                continue
            if ".pytest_cache" in rel:
                continue
            if rel not in seed_files and rel not in solution_files:
                p.unlink()
        self._copy_solution(solution_dir, workspace)
