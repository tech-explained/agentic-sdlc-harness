"""Unit tests for the pure transition function."""

import pytest

from harness.events.model import EventType, make_event, new_run_id
from harness.orchestrator.model import (
    MAX_REPAIRS,
    TERMINAL_STATES,
    InvalidTransition,
    Run,
    State,
    transition,
)

DIGEST = "sha256:" + "a" * 64


def make_run(**overrides) -> Run:
    kwargs = dict(
        run_id=new_run_id(),
        state=State.INTAKE,
        task_id="t1",
        attempt=0,
        context_digest="sha256:" + "b" * 64,
        permissions=frozenset({"fs.read", "fs.write.isolated"}),
    )
    kwargs.update(overrides)
    return Run(**kwargs)


def ev(run: Run, event_type: EventType, payload: dict | None = None) -> object:
    return make_event(
        run.run_id, event_type, run.state.value, "driver:test", payload or {}
    )


def test_happy_path_sequence():
    run = make_run()
    t = transition(run, ev(run, EventType.INTAKE_ACCEPTED, {"task_id": "t1"}))
    assert t.next_state == State.PLAN

    run = make_run(state=State.PLAN)
    t = transition(run, ev(run, EventType.AGENT_SUCCEEDED))
    assert t.next_state == State.WAIT_HUMAN

    run = make_run(state=State.WAIT_HUMAN)
    t = transition(
        run, ev(run, EventType.HUMAN_APPROVED, {"checkpoint": "plan"})
    )
    assert t.next_state == State.IMPLEMENT

    run = make_run(state=State.IMPLEMENT)
    t = transition(
        run, ev(run, EventType.AGENT_SUCCEEDED, {"artifact_digest": DIGEST})
    )
    assert t.next_state == State.VERIFY
    assert t.pending_gates == ("lint", "test-execution", "contract-compatibility")
    assert f"artifact:{DIGEST}" in t.evidence_add

    run = make_run(
        state=State.VERIFY,
        pending_gates=("lint", "test-execution", "contract-compatibility"),
    )
    t = transition(run, ev(run, EventType.GATE_PASSED, {"gate": "lint"}))
    assert t.next_state == State.VERIFY
    assert t.pending_gates == ("test-execution", "contract-compatibility")

    run = make_run(state=State.VERIFY, pending_gates=())
    t = transition(run, ev(run, EventType.ALL_GATES_PASSED))
    assert t.next_state == State.REVIEW

    run = make_run(state=State.REVIEW)
    t = transition(run, ev(run, EventType.REVIEW_PASSED))
    assert t.next_state == State.WAIT_HUMAN

    run = make_run(state=State.WAIT_HUMAN)
    t = transition(
        run, ev(run, EventType.HUMAN_APPROVED, {"checkpoint": "merge"})
    )
    assert t.next_state == State.MERGE_READY

    run = make_run(state=State.MERGE_READY, evidence_refs=(f"artifact:{DIGEST}",))
    t = transition(
        run, ev(run, EventType.MERGE_RECORDED, {"artifact_digest": DIGEST})
    )
    assert t.next_state == State.COMPLETED


def test_invalid_transitions_raise():
    run = make_run(state=State.PLAN)
    with pytest.raises(InvalidTransition):
        transition(run, ev(run, EventType.GATE_PASSED, {"gate": "lint"}))
    run = make_run(state=State.VERIFY, pending_gates=("lint",))
    with pytest.raises(InvalidTransition):
        # out-of-order gate
        transition(run, ev(run, EventType.GATE_PASSED, {"gate": "test-execution"}))
    with pytest.raises(InvalidTransition):
        transition(run, ev(run, EventType.ALL_GATES_PASSED))  # gates pending


def test_retry_ceiling_escalates_after_three():
    for attempt in range(MAX_REPAIRS):
        run = make_run(state=State.VERIFY, attempt=attempt,
                       pending_gates=("lint",))
        t = transition(run, ev(run, EventType.GATE_FAILED, {"gate": "lint"}))
        assert t.next_state == State.REPAIR
        assert t.attempt == attempt + 1
    run = make_run(state=State.VERIFY, attempt=MAX_REPAIRS, pending_gates=("lint",))
    t = transition(run, ev(run, EventType.GATE_FAILED, {"gate": "lint"}))
    assert t.next_state == State.ESCALATED


def test_agent_succeeded_requires_digest():
    run = make_run(state=State.IMPLEMENT)
    with pytest.raises(InvalidTransition):
        transition(run, ev(run, EventType.AGENT_SUCCEEDED, {}))


def test_merge_digest_binding():
    run = make_run(state=State.MERGE_READY, evidence_refs=(f"artifact:{DIGEST}",))
    with pytest.raises(InvalidTransition):
        transition(
            run, ev(run, EventType.MERGE_RECORDED,
                    {"artifact_digest": "sha256:" + "c" * 64})
        )


def test_override_requires_rationale_and_exception():
    run = make_run(state=State.WAIT_HUMAN)
    with pytest.raises(InvalidTransition):
        transition(run, ev(run, EventType.HUMAN_OVERRIDE, {"checkpoint": "plan"}))
    t = transition(
        run,
        ev(run, EventType.HUMAN_OVERRIDE, {
            "checkpoint": "plan", "rationale": "risk accepted",
            "exception_id": "EXP-1",
        }),
    )
    assert t.next_state == State.IMPLEMENT
    assert "exception:EXP-1" in t.evidence_add


def test_human_deny_rejects():
    run = make_run(state=State.WAIT_HUMAN)
    t = transition(run, ev(run, EventType.HUMAN_DENIED, {"checkpoint": "plan"}))
    assert t.next_state == State.REJECTED


def test_terminal_states_closed():
    for state in TERMINAL_STATES:
        run = make_run(state=state)
        with pytest.raises(InvalidTransition):
            transition(run, ev(run, EventType.AGENT_SUCCEEDED,
                               {"artifact_digest": DIGEST}))


def test_cancel_from_non_terminal():
    run = make_run(state=State.IMPLEMENT)
    t = transition(run, ev(run, EventType.CANCEL_REQUESTED))
    assert t.next_state == State.CANCELLED


def test_outcome_recorded_after_completed_is_synthetic():
    run = make_run(state=State.COMPLETED)
    t = transition(run, ev(run, EventType.OUTCOME_RECORDED))
    assert t.next_state == State.COMPLETED
    assert t.commands == ()


def test_permission_denied_escalates():
    run = make_run(state=State.IMPLEMENT)
    t = transition(
        run, ev(run, EventType.PERMISSION_DENIED,
                {"requested_capabilities": ["network"]})
    )
    assert t.next_state == State.ESCALATED
    assert "denied:network" in t.evidence_add
