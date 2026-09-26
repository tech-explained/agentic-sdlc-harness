"""Unit tests: metric formulas on synthetic run tables."""

from harness.evals.metrics import (
    build_metrics,
    gate_first_pass_rate,
    human_override_rate,
    injected_fault_catch_rate,
    retry_distribution,
    unaided_all_gates_rate,
)


def row(**overrides):
    base = {
        "run_id": "r",
        "task_id": "t",
        "scenario": "clean",
        "agent": "stub",
        "phase_sequence": [],
        "gate_first_pass": {"lint": True, "test-execution": True,
                            "contract-compatibility": True},
        "gate_failures": [],
        "retries": 0,
        "outcome": "completed",
        "verdict": "pass",
        "human_override": False,
        "human_checkpoint_id": "cp-1",
        "duration_ms": 10,
        "artifact_digest": "sha256:x",
        "decision_log_digest": "sha256:y",
        "event_count": 5,
        "gate_durations_ms": {},
        "finding_signatures": [],
        "terminal_state": "COMPLETED",
    }
    base.update(overrides)
    return base


def test_gate_first_pass_rate():
    table = [
        row(run_id="a"),
        row(run_id="b", gate_first_pass={"lint": False, "test-execution": True}),
    ]
    assert gate_first_pass_rate(table, "lint") == 0.5
    assert gate_first_pass_rate(table, "test-execution") == 1.0
    assert gate_first_pass_rate(table, "contract-compatibility") == 1.0
    assert gate_first_pass_rate([], "lint") is None


def test_unaided_all_gates_rate():
    table = [
        row(run_id="a"),
        row(run_id="b", retries=1),
        row(run_id="c", human_override=True),
        row(run_id="d", outcome="rejected"),
    ]
    assert unaided_all_gates_rate(table) == 0.25
    assert unaided_all_gates_rate([]) is None


def test_retry_distribution():
    table = [row(run_id="a"), row(run_id="b", retries=1), row(run_id="c", retries=3)]
    dist = retry_distribution(table)
    assert dist["mean"] == (0 + 1 + 3) / 3
    assert dist["median"] == 1
    assert dist["max"] == 3
    assert dist["zero_retry_share"] == 1 / 3


def test_injected_fault_catch_rate_credits_expected_gate_only():
    table = [
        row(run_id="a", scenario="clean"),
        row(run_id="b", scenario="F1", expected_detector="lint",
            gate_failures=["lint"]),
        row(run_id="c", scenario="F2", expected_detector="test-execution",
            gate_failures=["lint"]),  # wrong gate caught it
    ]
    assert injected_fault_catch_rate(table) == 0.5
    assert injected_fault_catch_rate([row(run_id="a")]) is None


def test_human_override_rate():
    assert human_override_rate([]) == "not observed"
    table = [row(run_id="a", human_checkpoint_id=None)]
    assert human_override_rate(table) == "not observed"
    table = [row(run_id="a"), row(run_id="b", human_override=True)]
    assert human_override_rate(table) == 0.5


def test_build_metrics_shape():
    metrics = build_metrics([row()], {"harness_version": "0.1.0"})
    assert metrics["run_count"] == 1
    assert set(metrics["per_gate_first_pass_rate"]) == {
        "lint", "test-execution", "contract-compatibility"}
    assert metrics["unaided_all_gates_rate"] == 1.0
    assert metrics["injected_fault_catch_rate"] is None
    assert metrics["human_override_rate"] == 0.0
