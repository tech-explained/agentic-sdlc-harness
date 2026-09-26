"""Shared test helpers for the harness test suite."""

from __future__ import annotations

import pytest

from harness.events.memory import InMemoryEventBus
from harness.events.model import EventType, make_event, new_run_id
from harness.orchestrator.engine import Engine
from harness.orchestrator.log import JsonlDecisionLog, MemoryDecisionStore

DEFAULT_NEEDS = ("fs.read", "fs.write.isolated")


@pytest.fixture
def engine_and_log(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    log = JsonlDecisionLog(log_path)
    engine = Engine(
        store=MemoryDecisionStore(log),
        log=log,
        bus=InMemoryEventBus(),
    )
    yield engine, log
    log.close()


def start_run(engine, task_id="t1", needs=DEFAULT_NEEDS) -> str:
    run_id = new_run_id()
    engine.apply(
        make_event(
            run_id,
            EventType.RUN_REQUESTED,
            None,
            "driver:test",
            {
                "task_manifest": {
                    "task_id": task_id,
                    "goal": "test goal",
                    "needs": list(needs),
                    "allowed_paths": [],
                }
            },
        )
    )
    engine.apply(
        make_event(
            run_id, EventType.INTAKE_ACCEPTED, "INTAKE", "driver:test",
            {"task_id": task_id},
        )
    )
    return run_id


def advance_to_implement(engine, run_id) -> None:
    """PLAN agent succeeds, plan checkpoint approved."""
    engine.apply(
        make_event(
            run_id, EventType.AGENT_SUCCEEDED, "PLAN", "agent:stub",
            {"summary": "plan"},
        )
    )
    engine.apply(
        make_event(
            run_id, EventType.HUMAN_APPROVED, "WAIT_HUMAN", "human:test",
            {"checkpoint": "plan", "action": "approve"},
        )
    )


def log_records(log) -> list:
    log._fh.flush()
    import json

    return [
        json.loads(line)
        for line in open(log.path, encoding="utf-8")
        if line.strip()
    ]
