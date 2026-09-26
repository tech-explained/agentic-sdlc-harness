"""Lifecycle driver: composition root for one measured scenario run.

The driver is the only component allowed to interpret engine commands. It:
  - prepares the isolated workspace from the task seed,
  - applies the (single) injected fault, if any, after the first implement,
  - invokes the agent adapter per RUN_AGENT command,
  - invokes the gate pipeline per RUN_GATES command,
  - resolves human checkpoints via the configured checkpoint provider,
  - performs the deterministic review and merge steps,
  - records the normalized run row for the metrics layer.

The driver never advances lifecycle state itself; every advance is an event
through Engine.apply().
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.agents.base import AgentAdapter, AgentContext, AgentResult, AgentTimeout
from harness.agents.stub import StubAgent
from harness.evals.faults import Fault, apply_fault
from harness.events.memory import InMemoryEventBus
from harness.events.model import EventType, make_event, new_run_id
from harness.gates.base import Finding, Gate, GateContext, GatePipeline, Verdict
from harness.gates.contract import ContractGate
from harness.gates.lint import LintGate
from harness.gates.tests import TestGate
from harness.orchestrator.checkpoints import ScriptedCheckpoint
from harness.orchestrator.engine import Engine
from harness.orchestrator.log import JsonlDecisionLog, MemoryDecisionStore
from harness.orchestrator.model import State
from harness.orchestrator.policy import ContextAssembler, PermissionPolicy
from harness.util import artifact_digest, file_sha256


@dataclass
class Scenario:
    task_id: str
    scenario: str  # "clean" | "F1".."F6" | "permission-denial" | ...
    fault: Fault | None = None
    adapter_id: str = "stub"


@dataclass
class DriverConfig:
    fixtures_root: Path
    results_dir: Path
    checkpoint: ScriptedCheckpoint | None = None
    policy: PermissionPolicy | None = None
    seed: int = 0


def load_manifest(fixtures_root: Path, task_id: str) -> dict:
    path = Path(fixtures_root) / "tasks" / task_id / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def seed_files(fixtures_root: Path, task_id: str) -> list[str]:
    seed_dir = Path(fixtures_root) / "tasks" / task_id / "seed"
    return sorted(
        p.relative_to(seed_dir).as_posix()
        for p in seed_dir.rglob("*")
        if p.is_file()
    )


class Driver:
    def __init__(self, config: DriverConfig, adapter: AgentAdapter) -> None:
        self.config = config
        self.adapter = adapter
        self.checkpoint = config.checkpoint or ScriptedCheckpoint({})
        self.policy = config.policy or PermissionPolicy()
        self.assembler = ContextAssembler()

    # ------------------------------------------------------------------
    # main entry
    # ------------------------------------------------------------------

    def run(self, scenario: Scenario) -> dict:
        started = time.monotonic()
        run_id = new_run_id()
        run_dir = self.config.results_dir / "runs" / run_id
        workspace = run_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        log_path = run_dir / "decisions.jsonl"

        manifest = load_manifest(self.config.fixtures_root, scenario.task_id)
        self._prepare_workspace(scenario.task_id, workspace)

        log = JsonlDecisionLog(log_path)
        engine = Engine(
            store=MemoryDecisionStore(log),
            log=log,
            bus=InMemoryEventBus(),
            policy=self.policy,
            assembler=self.assembler,
        )
        state = _RunState(
            driver=self,
            engine=engine,
            run_id=run_id,
            scenario=scenario,
            manifest=manifest,
            workspace=workspace,
            seed=self.config.seed,
        )
        try:
            state.execute()
        finally:
            log.close()
        duration_ms = int((time.monotonic() - started) * 1000)
        return state.build_row(
            log_path=log_path,
            duration_ms=duration_ms,
            adapter_id=self.adapter.adapter_id,
        )

    # ------------------------------------------------------------------

    def _prepare_workspace(self, task_id: str, workspace: Path) -> None:
        seed_dir = self.config.fixtures_root / "tasks" / task_id / "seed"
        for src in seed_dir.rglob("*"):
            if src.is_file():
                rel = src.relative_to(seed_dir)
                dest = workspace / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)


class _RunState:
    """Mutable per-run driver state (the engine's run record stays canonical)."""

    def __init__(
        self,
        driver: Driver,
        engine: Engine,
        run_id: str,
        scenario: Scenario,
        manifest: dict,
        workspace: Path,
        seed: int,
    ) -> None:
        self.driver = driver
        self.engine = engine
        self.run_id = run_id
        self.scenario = scenario
        self.manifest = manifest
        self.workspace = workspace
        self.seed = seed
        self.phases: list[str] = []
        self.gate_attempts: dict[str, list[dict]] = {}
        self.gate_durations_ms: dict[str, int] = {}
        self.finding_signatures: list[str] = []
        self.checkpoint_ids: list[str] = []
        self.overridden = False
        self.artifact_digest = ""
        self.changed_paths: tuple[str, ...] = ()
        self.fault_applied = False
        self._gate_fail_count = 0
        self._wait_human_visits = 0

    # ------------------------------------------------------------------

    def execute(self) -> None:
        manifest = dict(self.manifest)
        self._apply(
            EventType.RUN_REQUESTED,
            expected_state=None,
            producer="driver:eval",
            payload={"task_manifest": manifest, "scenario": self.scenario.scenario},
        )
        self._apply(
            EventType.INTAKE_ACCEPTED,
            expected_state=State.INTAKE.value,
            producer="driver:eval",
            payload={"task_id": self.scenario.task_id},
        )
        while True:
            run = self.engine.store.load(self.run_id)
            if run.state in (
                State.COMPLETED,
                State.REJECTED,
                State.ESCALATED,
                State.CANCELLED,
            ):
                if run.state == State.COMPLETED:
                    # Synthetic post-merge feedback; terminal states stay closed.
                    self._apply(
                        EventType.OUTCOME_RECORDED,
                        expected_state=run.state.value,
                        producer="driver:eval",
                        payload={"outcome": run.state.value.lower()},
                    )
                return
            handler = {
                State.PLAN: self._do_plan,
                State.IMPLEMENT: self._do_implement,
                State.REPAIR: self._do_repair,
                State.VERIFY: self._do_verify,
                State.WAIT_HUMAN: self._do_checkpoint,
                State.REVIEW: self._do_review,
                State.MERGE_READY: self._do_merge,
            }.get(run.state)
            if handler is None:
                raise RuntimeError(f"driver has no handler for {run.state}")
            handler(run)

    # ------------------------------------------------------------------
    # event helper
    # ------------------------------------------------------------------

    def _apply(
        self,
        event_type: EventType,
        expected_state: str | None,
        producer: str,
        payload: dict | None = None,
    ):
        event = make_event(
            self.run_id, event_type, expected_state, producer, payload or {}
        )
        decision = self.engine.apply(event)
        # Drain command envelopes published by the engine (control plane only).
        self.engine.bus.drain()
        return decision

    def _context_digest(self) -> str:
        _, digest = self.driver.assembler.assemble(self.manifest)
        return digest

    # ------------------------------------------------------------------
    # phase handlers
    # ------------------------------------------------------------------

    def _agent_context(self, phase: State, attempt: int) -> AgentContext:
        run = self.engine.store.load(self.run_id)
        effective = self.driver.policy.effective(phase, run.permissions)
        return AgentContext(
            run_id=self.run_id,
            task_id=self.scenario.task_id,
            phase=phase,
            attempt=attempt,
            workspace=self.workspace,
            manifest=self.manifest,
            context_digest=self._context_digest(),
            permissions=effective,
            seed=self.seed,
        )

    def _agent_result_payload(self, result: AgentResult) -> dict:
        payload: dict[str, Any] = {
            "adapter_id": self.driver.adapter.adapter_id,
            "summary": result.summary,
        }
        if result.artifact_digest:
            payload["artifact_digest"] = result.artifact_digest
        if result.changed_paths:
            payload["changed_paths"] = list(result.changed_paths)
        if result.capabilities_used:
            payload["capabilities_used"] = list(result.capabilities_used)
        if result.requested_capabilities:
            payload["requested_capabilities"] = list(result.requested_capabilities)
        if result.error:
            payload["error"] = result.error
        return payload

    def _do_plan(self, run) -> None:
        self.phases.append("PLAN")
        context = self._agent_context(State.PLAN, run.attempt)
        result = self.driver.adapter.execute(context)
        if result.status == "failed":
            self._apply(
                EventType.AGENT_FAILED,
                State.PLAN.value,
                f"agent:{self.driver.adapter.adapter_id}",
                {**self._agent_result_payload(result), "phase": "PLAN"},
            )
        else:
            self._apply(
                EventType.AGENT_SUCCEEDED,
                State.PLAN.value,
                f"agent:{self.driver.adapter.adapter_id}",
                {**self._agent_result_payload(result), "phase": "PLAN"},
            )

    def _do_implement(self, run) -> None:
        self.phases.append("IMPLEMENT")
        context = self._agent_context(State.IMPLEMENT, run.attempt)
        result = self._run_adapter(context)
        self._publish_agent_result(result, State.IMPLEMENT)

    def _do_repair(self, run) -> None:
        self.phases.append("REPAIR")
        context = self._agent_context(State.REPAIR, run.attempt)
        result = self._run_adapter(context)
        self._publish_agent_result(result, State.REPAIR)

    def _run_adapter(self, context: AgentContext) -> AgentResult:
        try:
            return self.driver.adapter.execute(context)
        except AgentTimeout as exc:
            return AgentResult(
                run_id=context.run_id,
                phase=context.phase,
                attempt=context.attempt,
                status="failed",
                error=str(exc),
            )

    def _publish_agent_result(self, result: AgentResult, phase: State) -> None:
        # Fault injection happens once, after the first implement, before the
        # artifact digest is computed: the measured artifact contains the fault.
        if (
            self.scenario.fault
            and not self.fault_applied
            and phase == State.IMPLEMENT
        ):
            desc = apply_fault(self.workspace, self.scenario.fault)
            self.fault_applied = True
            result = AgentResult(
                run_id=result.run_id,
                phase=result.phase,
                attempt=result.attempt,
                status=result.status,
                artifact_digest="",
                changed_paths=result.changed_paths,
                capabilities_used=result.capabilities_used,
                requested_capabilities=result.requested_capabilities,
                summary=result.summary + f" [fault {self.scenario.fault.fault_id}: {desc}]",
                error=result.error,
            )
        if result.status == "failed" and not result.requested_capabilities:
            self._apply(
                EventType.AGENT_FAILED,
                phase.value,
                f"agent:{self.driver.adapter.adapter_id}",
                {**self._agent_result_payload(result), "phase": phase.value},
            )
            return
        changed = list(result.changed_paths)
        if result.artifact_digest:
            digest = result.artifact_digest
        elif changed:
            digest = artifact_digest(self.workspace, changed)
        else:
            digest = ""
        # The permission-denial probe carries no digest; the engine's excess
        # capability check fires before the transition's digest requirement.
        if not digest and not result.requested_capabilities:
            raise RuntimeError("agent result has no artifact digest and no denial probe")
        self.artifact_digest = digest or self.artifact_digest
        self.changed_paths = tuple(changed) or self.changed_paths
        payload = self._agent_result_payload(result)
        if digest:
            payload["artifact_digest"] = digest
        payload["phase"] = phase.value
        self._apply(
            EventType.AGENT_SUCCEEDED,
            phase.value,
            f"agent:{self.driver.adapter.adapter_id}",
            payload,
        )

    # ------------------------------------------------------------------

    def _do_verify(self, run) -> None:
        self.phases.append("VERIFY")
        gates: list[Gate] = [LintGate(), TestGate(), ContractGate()]
        manifest = dict(self.manifest)
        manifest["_seed_files"] = seed_files(
            self.driver.config.fixtures_root, self.scenario.task_id
        )
        context = GateContext(
            workspace=self.workspace,
            artifact_digest=self.artifact_digest,
            task_manifest=manifest,
            changed_paths=self.changed_paths,
            allowed_paths=tuple(self.manifest.get("allowed_paths", ())),
            timeout_s=int(self.manifest.get("gate_timeout_s", 120)),
            # Subprocess gates need the harness package importable.
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        )
        pipeline = GatePipeline(gates, artifact_digest)
        results = pipeline.run_all(context)
        for gate_name, verdict in results:
            self.gate_attempts.setdefault(gate_name, []).append(
                {"attempt": run.attempt, "status": verdict.status}
            )
            self.gate_durations_ms[gate_name] = verdict.duration_ms
            invocation_id = f"inv-{run.attempt}-{gate_name}"
            if verdict.status == "pass":
                self._apply(
                    EventType.GATE_PASSED,
                    State.VERIFY.value,
                    f"gate:{gate_name}",
                    {
                        "gate": gate_name,
                        "invocation_id": invocation_id,
                        "artifact_digest": self.artifact_digest,
                    },
                )
            else:
                self._gate_fail_count += 1
                for finding in verdict.findings:
                    self.finding_signatures.append(finding.signature)
                self._apply(
                    EventType.GATE_FAILED,
                    State.VERIFY.value,
                    f"gate:{gate_name}",
                    {
                        "gate": gate_name,
                        "invocation_id": invocation_id,
                        "artifact_digest": self.artifact_digest,
                        "findings": [
                            {
                                "rule_id": f.rule_id,
                                "severity": f.severity,
                                "path": f.path,
                                "message": f.message,
                                "signature": f.signature,
                            }
                            for f in verdict.findings
                        ],
                    },
                )
                return  # repair or escalate; remaining gates do not run
        self._apply(
            EventType.ALL_GATES_PASSED,
            State.VERIFY.value,
            "gate:pipeline",
            {"artifact_digest": self.artifact_digest},
        )

    # ------------------------------------------------------------------

    def _do_checkpoint(self, run) -> None:
        self._wait_human_visits += 1
        # Lifecycle order is fixed: the first WAIT_HUMAN is the plan
        # checkpoint, the second (after REVIEW) is the merge checkpoint.
        # The driver never raises the input checkpoint (intake always validates).
        checkpoint_name = "merge" if self._wait_human_visits > 1 else "plan"
        checkpoint_id = self.driver.checkpoint.request(
            run, f"checkpoint:{checkpoint_name}"
        )
        self.checkpoint_ids.append(checkpoint_id)
        scripted = self.driver.checkpoint.decide(self.scenario.task_id, checkpoint_name)
        if scripted is None:
            action, rationale, exception_id = "approve", None, None
        else:
            action, rationale, exception_id = scripted
        resolution = self.driver.checkpoint.resolve(
            checkpoint_id,
            action,
            actor="human:scripted",
            rationale=rationale,
            exception_id=exception_id,
        )
        payload: dict[str, Any] = {
            "checkpoint": checkpoint_name,
            "checkpoint_id": checkpoint_id,
            "checkpoint_source": "scripted",
            "human_action": resolution["action"],
        }
        if action == "override":
            self.overridden = True
            payload["rationale"] = rationale
            payload["exception_id"] = exception_id
            event_type = EventType.HUMAN_OVERRIDE
        elif action == "deny":
            event_type = EventType.HUMAN_DENIED
        else:
            event_type = EventType.HUMAN_APPROVED
            payload["action"] = "approve"
        self._apply(
            event_type, State.WAIT_HUMAN.value, "human:scripted", payload
        )

    # ------------------------------------------------------------------

    def _do_review(self, run) -> None:
        self.phases.append("REVIEW")
        # Deterministic review: the verified artifact digest must match the
        # last artifact recorded in the evidence trail, and every gate must
        # have passed on the final attempt.
        last_digest = None
        for ref in reversed(run.evidence_refs):
            if ref.startswith("artifact:"):
                last_digest = ref[len("artifact:") :]
                break
        checks = {
            "artifact_digest_matches": last_digest == self.artifact_digest,
            "all_gates_passed_final_attempt": all(
                attempts and attempts[-1]["status"] == "pass"
                for attempts in self.gate_attempts.values()
            ),
        }
        if not all(checks.values()):
            raise RuntimeError(f"review checks failed: {checks}")
        self._apply(
            EventType.REVIEW_PASSED,
            State.REVIEW.value,
            "reviewer:driver",
            {"checks": checks, "artifact_digest": self.artifact_digest},
        )

    def _do_merge(self, run) -> None:
        self.phases.append("MERGE")
        self._apply(
            EventType.MERGE_RECORDED,
            State.MERGE_READY.value,
            "driver:eval",
            {"artifact_digest": self.artifact_digest},
        )

    # ------------------------------------------------------------------
    # normalized row
    # ------------------------------------------------------------------

    def build_row(self, log_path: Path, duration_ms: int, adapter_id: str) -> dict:
        run = self.engine.store.load(self.run_id)
        gate_first_pass: dict[str, bool] = {}
        gate_failures: list[str] = []
        for gate_name, attempts in self.gate_attempts.items():
            # First-pass = the gate passed on its first invocation.
            gate_first_pass[gate_name] = attempts[0]["status"] == "pass"
            if any(a["status"] == "fail" for a in attempts):
                gate_failures.append(gate_name)
        outcome = {
            State.COMPLETED: "completed",
            State.REJECTED: "rejected",
            State.ESCALATED: "escalated",
            State.CANCELLED: "cancelled",
        }[run.state]
        row = {
            "run_id": self.run_id,
            "task_id": self.scenario.task_id,
            "scenario": self.scenario.scenario,
            "agent": adapter_id,
            "phase_sequence": self.phases,
            "gate_first_pass": gate_first_pass,
            "gate_failures": sorted(gate_failures),
            "retries": run.attempt,
            "outcome": outcome,
            "verdict": "pass" if run.state == State.COMPLETED else "fail",
            "human_override": self.overridden,
            "human_checkpoint_id": self.checkpoint_ids[-1] if self.checkpoint_ids else None,
            "duration_ms": duration_ms,
            "artifact_digest": self.artifact_digest,
            "decision_log_digest": file_sha256(log_path),
            "decision_log_path": str(log_path),
            "event_count": sum(
                1 for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ),
            "gate_durations_ms": self.gate_durations_ms,
            "finding_signatures": self.finding_signatures,
            "terminal_state": run.state.value,
        }
        if self.scenario.fault:
            row["expected_detector"] = self.scenario.fault.expected_detector
            row["fault_id"] = self.scenario.fault.fault_id
        return row
