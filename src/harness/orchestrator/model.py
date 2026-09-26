"""Orchestrator domain model: states, run record, transition table, protocols.

The transition table is encoded as data plus a pure transition function.
Only Engine.apply() may create a state transition (single-authority invariant).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol

from harness.events.model import Event, EventType

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


class State(str, enum.Enum):
    # work states
    INTAKE = "INTAKE"
    PLAN = "PLAN"
    IMPLEMENT = "IMPLEMENT"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    # control states
    WAIT_HUMAN = "WAIT_HUMAN"
    REPAIR = "REPAIR"
    MERGE_READY = "MERGE_READY"
    # terminal states
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = frozenset(
    {State.COMPLETED, State.REJECTED, State.ESCALATED, State.CANCELLED}
)

# Ordered gate set; also the v1 repair/retry bound.
GATE_ORDER: tuple[str, ...] = ("lint", "test-execution", "contract-compatibility")
MAX_REPAIRS = 3


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    run_id: str
    state: State
    task_id: str
    attempt: int  # current repair attempt, 0..MAX_REPAIRS
    context_digest: str
    permissions: frozenset[str]  # task-declared needs; effective = policy[state] & this
    pending_gates: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    version: int = 0  # optimistic transition counter


# Command kinds emitted by the transition function.
VALIDATE_INTAKE = "VALIDATE_INTAKE"
RUN_AGENT = "RUN_AGENT"
RUN_GATES = "RUN_GATES"
REQUEST_HUMAN = "REQUEST_HUMAN"
REQUEST_REVIEW = "REQUEST_REVIEW"
RECORD_MERGE = "RECORD_MERGE"
RECORD_OUTCOME = "RECORD_OUTCOME"
NOTIFY_ESCALATION = "NOTIFY_ESCALATION"


@dataclass(frozen=True)
class Command:
    command_id: str
    run_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Transition:
    next_state: State
    attempt: int
    pending_gates: tuple[str, ...]
    commands: tuple[tuple[str, dict[str, Any]], ...]
    evidence_add: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Decision:
    event_id: str
    run_id: str
    state_before: State
    state_after: State
    attempt: int
    commands: tuple[Command, ...]
    decision_digest: str
    note: str = ""


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class HarnessError(Exception):
    """Base for all harness control-plane errors."""


class InvalidTransition(HarnessError):
    """No valid transition for (state, event)."""


class UnexpectedState(HarnessError):
    """Event's expected_state does not match the run's current state."""


class UnknownRun(HarnessError):
    """Event references a run_id the store does not know."""


class PermissionViolation(HarnessError):
    """A capability request exceeds the effective permission profile."""


class ProducerNotAllowed(HarnessError):
    """The event producer may not emit in the run's current state."""


# ---------------------------------------------------------------------------
# Protocols (seams; concrete adapters depend inward on these)
# ---------------------------------------------------------------------------


class DecisionStore(Protocol):
    def create_run(self, run: Run) -> None: ...
    def load(self, run_id: str) -> Run: ...
    def save(self, run: Run) -> None: ...
    def lock(self, run_id: str): ...
    def has_event(self, event_id: str) -> bool: ...
    def get_decision(self, event_id: str) -> Decision: ...
    def remember(self, event_id: str, decision: Decision) -> None: ...


class HumanCheckpoint(Protocol):
    def request(self, run: Run, reason: str) -> str: ...
    def resolve(
        self,
        checkpoint_id: str,
        action: str,
        actor: str,
        rationale: str | None = None,
        exception_id: str | None = None,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Pure transition function
# ---------------------------------------------------------------------------


def _require_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> None:
    missing = [f for f in fields if f not in payload]
    if missing:
        raise InvalidTransition(f"missing required payload fields: {missing}")


def _repair_or_escalate(run: Run, reason: str) -> Transition:
    if run.attempt < MAX_REPAIRS:
        return Transition(
            next_state=State.REPAIR,
            attempt=run.attempt + 1,
            pending_gates=(),
            commands=((RUN_AGENT, {"phase": "REPAIR"}),),
            note=f"{reason}; repair attempt {run.attempt + 1}/{MAX_REPAIRS}",
        )
    return Transition(
        next_state=State.ESCALATED,
        attempt=run.attempt,
        pending_gates=(),
        commands=((NOTIFY_ESCALATION, {"reason": "retry_ceiling_exhausted"}),),
        note="retry ceiling exhausted; escalated",
    )


def _last_artifact_digest(run: Run) -> str | None:
    for ref in reversed(run.evidence_refs):
        if ref.startswith("artifact:"):
            return ref[len("artifact:") :]
    return None


def transition(run: Run, event: Event) -> Transition:
    """Pure function: (run, event) -> Transition.

    Raises InvalidTransition for any (state, event) pair without a rule.
    """
    st = run.state
    et = event.type
    p = event.payload or {}

    # --- global rows: valid from any non-terminal state --------------------
    if et == EventType.CANCEL_REQUESTED and st not in TERMINAL_STATES:
        return Transition(
            State.CANCELLED, run.attempt, (), (), (),
            note="cancel requested by authorized requester",
        )
    if et == EventType.PERMISSION_DENIED and st not in TERMINAL_STATES:
        return Transition(
            State.ESCALATED,
            run.attempt,
            (),
            ((NOTIFY_ESCALATION, {"reason": "permission_denied"}),),
            (f"denied:{','.join(sorted(p.get('requested_capabilities', [])))}",),
            note="undeclared capability requested; escalated",
        )
    if et == EventType.OUTCOME_RECORDED and st == State.COMPLETED:
        return Transition(
            st, run.attempt, run.pending_gates, (), (),
            note="synthetic outcome attached; no state change",
        )

    # --- INTAKE -------------------------------------------------------------
    if st == State.INTAKE and et == EventType.INTAKE_ACCEPTED:
        _require_fields(p, ("task_id",))
        return Transition(State.PLAN, 0, (), ((RUN_AGENT, {"phase": "PLAN"}),))
    if st == State.INTAKE and et == EventType.INPUT_MISSING:
        return Transition(
            State.WAIT_HUMAN, 0, (), ((REQUEST_HUMAN, {"checkpoint": "input"}),)
        )

    # --- PLAN ---------------------------------------------------------------
    if st == State.PLAN and et == EventType.AGENT_SUCCEEDED:
        return Transition(
            State.WAIT_HUMAN, run.attempt, (), ((REQUEST_HUMAN, {"checkpoint": "plan"}),)
        )
    if st == State.PLAN and et == EventType.AGENT_FAILED:
        # Documented v1 extension: a failed planning step needs human input.
        return Transition(
            State.WAIT_HUMAN,
            run.attempt,
            (),
            ((REQUEST_HUMAN, {"checkpoint": "plan", "reason": "agent_failed"}),),
            note="plan agent failed; human input required",
        )

    # --- WAIT_HUMAN ----------------------------------------------------------
    if st == State.WAIT_HUMAN and et in (
        EventType.HUMAN_APPROVED,
        EventType.HUMAN_OVERRIDE,
    ):
        checkpoint = p.get("checkpoint")
        action = "override" if et == EventType.HUMAN_OVERRIDE else p.get("action", "approve")
        if et == EventType.HUMAN_OVERRIDE and not (
            p.get("rationale") and p.get("exception_id")
        ):
            raise InvalidTransition("HUMAN_OVERRIDE requires rationale and exception_id")
        evidence = (f"human:{checkpoint}:{action}",)
        note = ""
        if et == EventType.HUMAN_OVERRIDE:
            evidence += (f"exception:{p.get('exception_id')}",)
            note = "override recorded; original findings remain visible"
        if checkpoint == "plan":
            return Transition(
                State.IMPLEMENT, run.attempt, (),
                ((RUN_AGENT, {"phase": "IMPLEMENT"}),), evidence, note,
            )
        if checkpoint == "merge":
            return Transition(
                State.MERGE_READY, run.attempt, (),
                ((RECORD_MERGE, {}),), evidence, note,
            )
        if checkpoint == "input":
            return Transition(
                State.INTAKE, run.attempt, (),
                ((VALIDATE_INTAKE, {}),), evidence, note,
            )
        raise InvalidTransition(f"unknown checkpoint: {checkpoint!r}")
    if st == State.WAIT_HUMAN and et == EventType.HUMAN_DENIED:
        checkpoint = p.get("checkpoint", "?")
        return Transition(
            State.REJECTED, run.attempt, (), (),
            (f"human:{checkpoint}:deny",), "denied by human checkpoint",
        )

    # --- IMPLEMENT -----------------------------------------------------------
    if st == State.IMPLEMENT and et == EventType.AGENT_SUCCEEDED:
        digest = p.get("artifact_digest")
        if not digest:
            raise InvalidTransition("AGENT_SUCCEEDED requires artifact_digest")
        return Transition(
            State.VERIFY, run.attempt, GATE_ORDER, ((RUN_GATES, {}),),
            (f"artifact:{digest}",),
        )
    if st in (State.IMPLEMENT, State.REPAIR) and et == EventType.AGENT_FAILED:
        # Documented v1 extension: agent execution failure follows retry semantics.
        return _repair_or_escalate(run, "agent execution failed")

    # --- VERIFY ----------------------------------------------------------------
    if st == State.VERIFY and et == EventType.GATE_PASSED:
        gate = p.get("gate")
        invocation = p.get("invocation_id", "?")
        if not run.pending_gates or gate != run.pending_gates[0]:
            raise InvalidTransition(
                f"gate {gate!r} is not the next pending gate {run.pending_gates!r}"
            )
        rest = run.pending_gates[1:]
        cmds = ((RUN_GATES, {}),) if rest else ()
        return Transition(
            State.VERIFY, run.attempt, rest, cmds, (f"gate:{gate}:{invocation}:pass",)
        )
    if st == State.VERIFY and et == EventType.ALL_GATES_PASSED:
        if run.pending_gates:
            raise InvalidTransition(f"gates still pending: {run.pending_gates!r}")
        return Transition(State.REVIEW, run.attempt, (), ((REQUEST_REVIEW, {}),))
    if st == State.VERIFY and et == EventType.GATE_FAILED:
        return _repair_or_escalate(run, f"gate {p.get('gate', '?')} failed")

    # --- REPAIR -----------------------------------------------------------------
    if st == State.REPAIR and et == EventType.AGENT_SUCCEEDED:
        digest = p.get("artifact_digest")
        if not digest:
            raise InvalidTransition("AGENT_SUCCEEDED requires artifact_digest")
        return Transition(
            State.VERIFY, run.attempt, GATE_ORDER, ((RUN_GATES, {}),),
            (f"artifact:{digest}",),
        )

    # --- REVIEW ------------------------------------------------------------------
    if st == State.REVIEW and et == EventType.REVIEW_PASSED:
        return Transition(
            State.WAIT_HUMAN, run.attempt, (), ((REQUEST_HUMAN, {"checkpoint": "merge"}),)
        )

    # --- MERGE_READY ---------------------------------------------------------------
    if st == State.MERGE_READY and et == EventType.MERGE_RECORDED:
        digest = p.get("artifact_digest")
        if not digest:
            raise InvalidTransition("MERGE_RECORDED requires artifact_digest")
        if digest != _last_artifact_digest(run):
            raise InvalidTransition("merge artifact digest does not match verified artifact")
        return Transition(State.COMPLETED, run.attempt, (), ((RECORD_OUTCOME, {}),))

    raise InvalidTransition(f"no transition for ({st.value}, {et.value})")
