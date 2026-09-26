"""Measured evaluation orchestration: scenario matrix, metrics, verification.

The measured v1 run uses the deterministic stub agent suite only
(12 clean scenarios + 6 single-fault scenarios = 18 runs).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from harness.agents.stub import StubAgent
from harness.evals.driver import Driver, DriverConfig, Scenario
from harness.evals.faults import load_faults
from harness.evals.metrics import build_metrics
from harness.evals.replay import replay_run, verify_log
from harness.orchestrator.checkpoints import ScriptedCheckpoint
from harness.util import utcnow_rfc3339, write_canonical_json

TASK_ORDER = [
    "ll-discount", "ll-fizzbuzz", "ll-stats", "ll-retry",
    "eh-parse", "eh-validate", "eh-missing",
    "cs-order", "cs-user", "cs-payment",
    "ps-inventory", "ps-shipment",
]


def scenario_matrix(fixtures_root: Path) -> list[Scenario]:
    scenarios = [Scenario(task_id=t, scenario="clean") for t in TASK_ORDER]
    faults = {
        f.fault_id: f
        for f in load_faults(Path(fixtures_root) / "faults" / "manifest.json")
    }
    for fid in ["F1", "F2", "F3", "F4", "F5", "F6"]:
        fault = faults[fid]
        scenarios.append(Scenario(task_id=fault.task_id, scenario=fid, fault=fault))
    return scenarios


def run_measured_evaluation(fixtures_root: Path, results_dir: Path) -> dict:
    fixtures_root = Path(fixtures_root)
    results_dir = Path(results_dir)
    runs_dir = results_dir / "runs"
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)

    scenarios = scenario_matrix(fixtures_root)
    rows: list[dict] = []
    for i, scenario in enumerate(scenarios):
        config = DriverConfig(
            fixtures_root=fixtures_root,
            results_dir=results_dir,
            # Measured runs: a scripted policy stands in for the human at the
            # plan and merge checkpoints (always approve). Override/deny paths
            # are exercised separately in integration tests.
            checkpoint=ScriptedCheckpoint({}),
            seed=i,
        )
        driver = Driver(config, StubAgent(fixtures_root))
        row = driver.run(scenario)
        rows.append(row)
        print(f"[{i + 1:2d}/{len(scenarios)}] {scenario.scenario:6s} "
              f"{scenario.task_id:12s} -> {row['outcome']:9s} "
              f"retries={row['retries']} gates={row['gate_first_pass']}")

    metadata = {
        "harness_version": "0.1.0",
        "agent": "stub",
        "evaluated_at": utcnow_rfc3339(),
        "scenario_count": len(rows),
        "clean_scenarios": 12,
        "fault_scenarios": 6,
        "checkpoint_policy": "scripted-auto-approve",
        "policy_version": "1.0",
        "max_repairs": 3,
        "gate_order": ["lint", "test-execution", "contract-compatibility"],
    }
    metrics = build_metrics(rows, metadata)

    manifest = {
        "generated_at": metadata["evaluated_at"],
        "runs": [
            {
                "run_id": r["run_id"],
                "task_id": r["task_id"],
                "scenario": r["scenario"],
                "outcome": r["outcome"],
                "decision_log": r["decision_log_path"],
                "decision_log_digest": r["decision_log_digest"],
            }
            for r in rows
        ],
    }

    write_canonical_json(results_dir / "run_table.json", rows)
    write_canonical_json(results_dir / "metrics.json", metrics)
    write_canonical_json(results_dir / "manifest.json", manifest)
    summary = {
        "runs": len(rows),
        "metrics_path": str(results_dir / "metrics.json"),
        "metrics": metrics,
    }
    return summary


def verify_results(results_dir: Path) -> bool:
    """Verify results/ against the decision logs: chains, replay, metrics."""
    results_dir = Path(results_dir)
    ok = True

    def fail(msg: str) -> None:
        nonlocal ok
        ok = False
        print(f"VERIFY-FAIL: {msg}")

    for name in ("run_table.json", "metrics.json", "manifest.json"):
        if not (results_dir / name).is_file():
            fail(f"missing {name}")
            return False
    rows = json.loads((results_dir / "run_table.json").read_text(encoding="utf-8"))
    recorded_metrics = json.loads(
        (results_dir / "metrics.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((results_dir / "manifest.json").read_text(encoding="utf-8"))

    if len(rows) != 18:
        fail(f"run_table has {len(rows)} rows, expected 18")
    manifest_ids = {r["run_id"] for r in manifest["runs"]}
    if {r["run_id"] for r in rows} != manifest_ids:
        fail("manifest run ids do not match run_table")

    for row in rows:
        log_path = Path(row["decision_log_path"])
        if not log_path.is_file():
            fail(f"{row['run_id']}: log missing: {log_path}")
            continue
        check = verify_log(log_path)
        if not check["ok"]:
            fail(f"{row['run_id']}: log verification failed: {check['errors']}")
            continue
        report = replay_run(log_path, expected_row=row)
        if report.errors or not report.row_matches:
            fail(f"{row['run_id']}: replay failed: "
                 f"{report.errors} {report.decision_mismatches}")

    recomputed = build_metrics(rows, recorded_metrics.get("metadata", {}))
    if recomputed != recorded_metrics:
        fail("recomputed metrics differ from recorded metrics.json")
        for key in recomputed:
            if recomputed.get(key) != recorded_metrics.get(key):
                print(f"  metric {key}: recorded={recorded_metrics.get(key)!r} "
                      f"recomputed={recomputed.get(key)!r}")

    if ok:
        print(f"results verified: {len(rows)} runs, chains OK, replay OK, "
              f"metrics reproduce exactly")
    return ok
