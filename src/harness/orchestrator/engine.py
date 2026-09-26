"""The engine: sole lifecycle authority.

apply(event) is the only path to a state transition:
  validate schema -> idempotency -> load run -> expected-state check ->
  producer check -> permission check -> pure transition ->
  append decision (record before dispatch) -> save run -> publish commands.

Rejections are recorded in the log before the typed error is raised.
"""

from __future__ import annotations

from typing import Any

from harness.events.memory import InMemoryEventBus
from harness.events.model import (
    Event,
    EventType,
    SchemaError,
    make_event,
    validate_envelope,
)
from harness.orchestrator.log import JsonlDecisionLog, MemoryDecisionStore
from harness.orchestrator.model import (
    TERMINAL_STATES,
    Command,
    Decision,
    HarnessError,
    InvalidTransition,
    PermissionViolation,
    ProducerNotAllowed,
    Run,
    State,
    Transition,
    UnexpectedState,
    UnknownRun,
    transition,
)
from harness.orchestrator.policy import ContextAssembler, PermissionPolicy
from harness.util import canonical_json, sha256_hex

# Producer namespaces allowed to emit in each state (prefix before ":").
_PRODUCERS: dict[State, tuple[str, ...]] = {
    State.INTAKE: ("driver",),
    State.PLAN: ("agent",),
    State.WAIT_HUMAN: ("human", "driver"),
    State.IMPLEMENT: ("agent",),
    State.REPAIR: ("agent",),
    State.VERIFY: ("gate",),
    State.REVIEW: ("reviewer",),
    State.MERGE_READY: ("driver",),
    State.COMPLETED: ("driver",),
}

# Record keys copied from the event payload into the decision-log record.
_RECORD_FIELDS = (
    "fault_id",
    "measurement",
    "gate",
    "invocation_id",
    "artifact_digest",
    "human_action",
    "checkpoint",
    "checkpoint_source",
    "requested_capabilities",
    "exception_id",
)


class Engine:
    def __init__(
        self,
        store: MemoryDecisionStore,
        log: JsonlDecisionLog,
        bus: InMemoryEventBus,
        policy: PermissionPolicy | None = None,
        assembler: ContextAssembler | None = None,
    ) -> None:
        self.store = store
        self.log = log
        self.bus = bus
        self.policy = policy or PermissionPolicy()
        self.assembler = assembler or ContextAssembler()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def apply(self, event: Event) -> Decision:
        validate_envelope(event)
        if event.type == EventType.RUN_REQUESTED:
            return self._create_run(event)
        if self.store.has_event(event.event_id):
            # Idempotency: repeated event_id returns the original outcome.
            return self.store.get_decision(event.event_id)
        run = self.store.load(event.run_id)
        try:
            with self.store.lock(event.run_id):
                self._check_expected(event, run)
                self._check_producer(event, run)
                excess = self._excess_capabilities(event, run)
                if excess:
                    return self._apply_permission_denied(event, run, excess)
                outcome = transition(run, event)
                return self._commit(event, run, outcome)
        except HarnessError as exc:
            self._record_rejection(event, run, exc)
            raise

    # ------------------------------------------------------------------
    # run creation
    # ------------------------------------------------------------------

    def _create_run(self, event: Event) -> Decision:
        if self.store.has_event(event.event_id):
            return self.store.get_decision(event.event_id)
        manifest = event.payload.get("task_manifest") or {}
        task_id = manifest.get("task_id") or event.payload.get("task_id")
        if not task_id:
            raise SchemaError("RUN_REQUESTED requires task_manifest.task_id")
        needs = frozenset(manifest.get("needs", []))
        _, context_digest = self.assembler.assemble(manifest)
        run = Run(
            run_id=event.run_id,
            state=State.INTAKE,
            task_id=task_id,
            attempt=0,
            context_digest=context_digest,
            permissions=needs,
            pending_gates=(),
            evidence_refs=(),
            version=0,
        )
        self.store.create_run(run)
        decision = Decision(
            event_id=event.event_id,
            run_id=event.run_id,
            state_before=State.INTAKE,
            state_after=State.INTAKE,
            attempt=0,
            commands=(),
            decision_digest=self._decision_digest(event, State.INTAKE, State.INTAKE, 0, ()),
            note=f"run created; context_digest={context_digest}",
        )
        self.store.remember(event.event_id, decision)
        record = self._base_record(event, None, decision)
        record["task_id"] = task_id
        record["context_digest"] = context_digest
        self.log.append(record)
        return decision

    # ------------------------------------------------------------------
    # checks
    # ------------------------------------------------------------------

    def _check_expected(self, event: Event, run: Run) -> None:
        if event.expected_state != run.state.value:
            raise UnexpectedState(
                f"event {event.type.value} expects {event.expected_state}, "
                f"run is {run.state.value}"
            )

    def _check_producer(self, event: Event, run: Run) -> None:
        if event.type == EventType.CANCEL_REQUESTED:
            if not event.producer.startswith("driver"):
                raise ProducerNotAllowed("CANCEL_REQUESTED requires a driver producer")
            return
        allowed = _PRODUCERS.get(run.state, ())
        prefix = event.producer.split(":", 1)[0]
        if prefix not in allowed:
            raise ProducerNotAllowed(
                f"producer {event.producer!r} not allowed in state {run.state.value}"
            )

    def _excess_capabilities(self, event: Event, run: Run) -> tuple[str, ...]:
        """Capabilities declared by the event beyond the effective profile."""
        payload = event.payload or {}
        declared = set(payload.get("requested_capabilities", ()))
        declared |= set(payload.get("capabilities_used", ()))
        if not declared:
            return ()
        allowed = self.policy.effective(run.state, run.permissions)
        excess = sorted(set(declared) - set(allowed))
        return tuple(excess)

    # ------------------------------------------------------------------
    # commit
    # ------------------------------------------------------------------

    def _apply_permission_denied(
        self, event: Event, run: Run, excess: tuple[str, ...]
    ) -> Decision:
        denied = make_event(
            run.run_id,
            EventType.PERMISSION_DENIED,
            run.state.value,
            "system",
            {"requested_capabilities": list(excess), "rejected_event_id": event.event_id},
        )
        outcome = transition(run, denied)
        decision = self._commit(denied, run, outcome, rejected_ref=event.event_id)
        # Idempotent redelivery of the offending event returns this decision.
        self.store.remember(event.event_id, decision)
        # The offending event itself is recorded as rejected for the audit trail.
        self._record_rejection(
            event, run, PermissionViolation(f"excess capabilities: {excess}")
        )
        return decision

    def _commit(
        self,
        event: Event,
        run: Run,
        outcome: Transition,
        rejected_ref: str | None = None,
    ) -> Decision:
        new_run = Run(
            run_id=run.run_id,
            state=outcome.next_state,
            task_id=run.task_id,
            attempt=outcome.attempt,
            context_digest=run.context_digest,
            permissions=run.permissions,
            pending_gates=outcome.pending_gates,
            evidence_refs=run.evidence_refs + outcome.evidence_add,
            version=run.version + 1,
        )
        commands = tuple(
            Command(
                command_id=f"{event.event_id}#{i}",
                run_id=run.run_id,
                kind=kind,
                payload=dict(payload),
            )
            for i, (kind, payload) in enumerate(outcome.commands)
        )
        digest = self._decision_digest(
            event, run.state, outcome.next_state, outcome.attempt, commands
        )
        decision = Decision(
            event_id=event.event_id,
            run_id=run.run_id,
            state_before=run.state,
            state_after=outcome.next_state,
            attempt=outcome.attempt,
            commands=commands,
            decision_digest=digest,
            note=outcome.note,
        )
        self.store.save(new_run)
        self.store.remember(event.event_id, decision)
        record = self._base_record(event, outcome, decision)
        if rejected_ref:
            record["rejected_event_id"] = rejected_ref
        self.log.append(record)
        for cmd in commands:
            # Commands are control-plane envelopes on the same bus shape.
            self.bus.publish(_command_event(cmd))
        return decision

    def _decision_digest(
        self,
        event: Event,
        before: State,
        after: State,
        attempt: int,
        commands: tuple[Command, ...],
    ) -> str:
        body = {
            "run_id": event.run_id,
            "event_id": event.event_id,
            "state_before": before.value,
            "state_after": after.value,
            "attempt": attempt,
            "commands": [
                {"kind": c.kind, "payload": c.payload} for c in commands
            ],
        }
        return "sha256:" + sha256_hex(canonical_json(body))

    def _base_record(
        self, event: Event, outcome: Transition | None, decision: Decision
    ) -> dict[str, Any]:
        payload = event.payload or {}
        record: dict[str, Any] = {
            "run_id": event.run_id,
            "event_id": event.event_id,
            "state_before": decision.state_before.value,
            "event_type": event.type.value,
            "state_after": decision.state_after.value,
            "attempt": decision.attempt,
            "producer": event.producer,
            "decision_digest": decision.decision_digest,
            "measurement": bool(payload.get("measurement", True)),
            # Full payload is recorded so replay can re-apply transitions
            # exactly without out-of-band state.
            "event_payload": payload,
        }
        for key in _RECORD_FIELDS:
            if key in payload:
                record[key] = payload[key]
        if event.type == EventType.GATE_PASSED:
            record["status"] = "pass"
        elif event.type == EventType.GATE_FAILED:
            record["status"] = "fail"
        if outcome is not None and outcome.note:
            record["note"] = outcome.note
        elif decision.note:
            record["note"] = decision.note
        return record

    def _record_rejection(self, event: Event, run: Run | None, exc: HarnessError) -> None:
        record: dict[str, Any] = {
            "run_id": event.run_id,
            "event_id": event.event_id,
            "state_before": run.state.value if run else None,
            "event_type": event.type.value,
            "state_after": run.state.value if run else None,
            "attempt": run.attempt if run else 0,
            "producer": event.producer,
            "rejected": True,
            "rejection_reason": f"{type(exc).__name__}: {exc}",
            "measurement": bool((event.payload or {}).get("measurement", True)),
            "event_payload": dict(event.payload or {}),
        }
        for key in _RECORD_FIELDS:
            if key in (event.payload or {}):
                record[key] = event.payload[key]
        self.log.append(record)


def _command_event(cmd: Command) -> Event:
    """Wrap a command as a bus envelope (control-plane transport only)."""
    return make_event(
        cmd.run_id,
        EventType.COMMAND,
        expected_state=None,
        producer="engine",
        payload={
            "command_id": cmd.command_id,
            "kind": cmd.kind,
            "payload": cmd.payload,
        },
        event_id=cmd.command_id,
    )
