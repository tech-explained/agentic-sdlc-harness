"""Metric formulas per the spec (single authoritative implementation).

Normalized run table columns:
run_id, task_id, scenario, agent, phase_sequence, gate_first_pass,
gate_failures, retries, outcome, verdict, human_override,
human_checkpoint_id, duration_ms, artifact_digest, decision_log_digest,
event_count, gate_durations_ms

Formulas:
- per-gate first-pass rate: runs in which the named gate passed on the first
  verification attempt / total runs that reached that gate.
- unaided all-gates rate: runs that cleared every gate with zero repair
  retries and no human override / total runs.
- retry distribution: mean, median, max, zero-retry share.
- injected-fault catch rate: fault runs where the expected detector gate
  failed / total fault runs. Credit only when the expected gate fails.
- human override rate: runs containing a human override event / total runs;
  reported as "not observed" when no checkpoints resolve.
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any


def _rows(table: list[dict]) -> list[dict]:
    return table


def gate_first_pass_rate(table: list[dict], gate: str) -> float | None:
    """Runs where the gate passed on its first invocation / runs that invoked it."""
    reached = [r for r in _rows(table) if gate in r.get("gate_first_pass", {})]
    if not reached:
        return None
    passed = [r for r in reached if r["gate_first_pass"][gate]]
    return len(passed) / len(reached)


def unaided_all_gates_rate(table: list[dict]) -> float | None:
    if not table:
        return None
    unaided = [
        r for r in table
        if r.get("outcome") in ("merged", "completed")
        and r.get("retries", 0) == 0
        and not r.get("human_override", False)
    ]
    return len(unaided) / len(table)


def retry_distribution(table: list[dict]) -> dict[str, Any]:
    retries = [r.get("retries", 0) for r in table]
    if not retries:
        return {"mean": 0.0, "median": 0, "max": 0, "zero_retry_share": 1.0}
    return {
        "mean": sum(retries) / len(retries),
        "median": statistics.median(retries),
        "max": max(retries),
        "zero_retry_share": sum(1 for x in retries if x == 0) / len(retries),
    }


def injected_fault_catch_rate(table: list[dict]) -> float | None:
    fault_runs = [r for r in _rows(table) if r.get("scenario") != "clean"]
    if not fault_runs:
        return None
    caught = [
        r for r in fault_runs
        if r.get("expected_detector") in r.get("gate_failures", [])
    ]
    return len(caught) / len(fault_runs)


def human_override_rate(table: list[dict]) -> float | str:
    if not table:
        return "not observed"
    checkpoints = [r for r in table if r.get("human_checkpoint_id")]
    if not checkpoints:
        return "not observed"
    overridden = [r for r in checkpoints if r.get("human_override")]
    return len(overridden) / len(checkpoints)


def failure_learning_top_signatures(table: list[dict], top_n: int = 5) -> list[dict]:
    counter: Counter[str] = Counter()
    for r in table:
        for sig in r.get("finding_signatures", []):
            counter[sig] += 1
    return [
        {"signature": sig, "count": count}
        for sig, count in counter.most_common(top_n)
    ]


def build_metrics(table: list[dict], metadata: dict) -> dict:
    per_gate: dict[str, float | None] = {}
    for gate in ("lint", "test-execution", "contract-compatibility"):
        per_gate[gate] = gate_first_pass_rate(table, gate)
    return {
        "metadata": metadata,
        "run_count": len(table),
        "per_gate_first_pass_rate": per_gate,
        "unaided_all_gates_rate": unaided_all_gates_rate(table),
        "retry_distribution": retry_distribution(table),
        "injected_fault_catch_rate": injected_fault_catch_rate(table),
        "human_override_rate": human_override_rate(table),
        "failure_learning_top_signatures": failure_learning_top_signatures(table),
    }
