"""Unit tests: engine idempotency, stale states, producers, rejections."""

import pytest

from harness.events.model import EventType, make_event
from harness.orchestrator.model import (
    ProducerNotAllowed,
    UnexpectedState,
    UnknownRun,
    State,
)
from tests.conftest import advance_to_implement, log_records, start_run


def test_duplicate_delivery_is_idempotent(engine_and_log):
    engine, log = engine_and_log
    run_id = start_run(engine)
    event = make_event(
        run_id, EventType.AGENT_SUCCEEDED, "PLAN", "agent:stub",
        {"summary": "plan"},
    )
    first = engine.apply(event)
    before = len(log_records(log))
    second = engine.apply(event)
    assert second.event_id == first.event_id
    assert second.decision_digest == first.decision_digest
    assert len(log_records(log)) == before  # no duplicate record


def test_stale_expected_state_rejected(engine_and_log):
    engine, log = engine_and_log
    run_id = start_run(engine)
    with pytest.raises(UnexpectedState):
        engine.apply(
            make_event(
                run_id, EventType.AGENT_SUCCEEDED, "IMPLEMENT", "agent:stub",
                {"artifact_digest": "sha256:" + "a" * 64},
            )
        )
    records = log_records(log)
    rejected = [r for r in records if r.get("rejected")]
    assert len(rejected) == 1
    assert "UnexpectedState" in rejected[0]["rejection_reason"]


def test_unknown_run_rejected(engine_and_log):
    engine, _ = engine_and_log
    with pytest.raises(UnknownRun):
        engine.apply(
            make_event(
                "f" * 32, EventType.AGENT_SUCCEEDED, "PLAN", "agent:stub", {}
            )
        )


def test_producer_not_allowed_recorded(engine_and_log):
    engine, log = engine_and_log
    run_id = start_run(engine)
    with pytest.raises(ProducerNotAllowed):
        engine.apply(
            make_event(
                run_id, EventType.AGENT_SUCCEEDED, "PLAN", "gate:lint", {}
            )
        )
    rejected = [r for r in log_records(log) if r.get("rejected")]
    assert rejected and "ProducerNotAllowed" in rejected[0]["rejection_reason"]


def test_permission_denial_path_and_redelivery(engine_and_log):
    engine, log = engine_and_log
    run_id = start_run(engine)
    advance_to_implement(engine, run_id)
    offense = make_event(
        run_id, EventType.AGENT_SUCCEEDED, "IMPLEMENT", "agent:stub",
        {"requested_capabilities": ["network"], "summary": "probe"},
    )
    decision = engine.apply(offense)
    assert decision.state_after == State.ESCALATED
    # Redelivery of the offending event returns the denial decision.
    again = engine.apply(offense)
    assert again.decision_digest == decision.decision_digest
    assert again.state_after == State.ESCALATED
    records = log_records(log)
    escalations = [
        r for r in records
        if r.get("event_type") == "PERMISSION_DENIED" and not r.get("rejected")
    ]
    assert len(escalations) == 1


def test_granted_capabilities_pass(engine_and_log):
    engine, _ = engine_and_log
    run_id = start_run(engine)
    advance_to_implement(engine, run_id)
    decision = engine.apply(
        make_event(
            run_id, EventType.AGENT_SUCCEEDED, "IMPLEMENT", "agent:stub",
            {
                "artifact_digest": "sha256:" + "a" * 64,
                "capabilities_used": ["fs.read", "fs.write.isolated"],
            },
        )
    )
    assert decision.state_after == State.VERIFY


def test_cancel_requires_driver_producer(engine_and_log):
    engine, _ = engine_and_log
    run_id = start_run(engine)
    with pytest.raises(ProducerNotAllowed):
        engine.apply(
            make_event(run_id, EventType.CANCEL_REQUESTED, "PLAN", "agent:stub", {})
        )
    decision = engine.apply(
        make_event(run_id, EventType.CANCEL_REQUESTED, "PLAN", "driver:test", {})
    )
    assert decision.state_after == State.CANCELLED


def test_run_requested_idempotent(engine_and_log):
    engine, log = engine_and_log
    from harness.events.model import new_run_id

    run_id = new_run_id()
    request = make_event(
        run_id, EventType.RUN_REQUESTED, None, "driver:test",
        {"task_manifest": {"task_id": "t1", "needs": []}},
    )
    first = engine.apply(request)
    before = len(log_records(log))
    second = engine.apply(request)
    assert second.decision_digest == first.decision_digest
    assert len(log_records(log)) == before  # no duplicate run, no new record
