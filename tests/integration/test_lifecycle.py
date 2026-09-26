"""Integration: full lifecycle runs through the driver."""

from pathlib import Path

import pytest

from harness.agents.stub import StubAgent
from harness.evals.driver import Driver, DriverConfig, Scenario
from harness.evals.faults import load_faults
from harness.evals.replay import replay_run, verify_log
from harness.orchestrator.checkpoints import ScriptedCheckpoint

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "fixtures"


def run_scenario(tmp_path, task_id, scenario="clean", fault=None,
                 checkpoint=None, adapter=None, seed=0):
    config = DriverConfig(
        fixtures_root=FIXTURES,
        results_dir=tmp_path / "results",
        checkpoint=checkpoint or ScriptedCheckpoint({}),
        seed=seed,
    )
    driver = Driver(config, adapter or StubAgent(FIXTURES))
    return driver.run(Scenario(task_id=task_id, scenario=scenario, fault=fault))


def test_happy_path(tmp_path):
    row = run_scenario(tmp_path, "ll-discount")
    assert row["outcome"] == "completed"
    assert row["retries"] == 0
    assert all(row["gate_first_pass"].values())
    assert row["phase_sequence"] == ["PLAN", "IMPLEMENT", "VERIFY", "REVIEW", "MERGE"]
    log = Path(row["decision_log_path"])
    assert verify_log(log)["ok"]
    report = replay_run(log, expected_row=row)
    assert report.errors == () and report.row_matches


@pytest.mark.parametrize("fault_id", ["F1", "F2", "F3", "F4", "F5", "F6"])
def test_fault_scenarios(tmp_path, fault_id):
    faults = {f.fault_id: f for f in load_faults(FIXTURES / "faults" / "manifest.json")}
    fault = faults[fault_id]
    row = run_scenario(tmp_path, fault.task_id, scenario=fault_id, fault=fault)
    assert row["outcome"] == "completed"
    assert row["retries"] == 1
    assert fault.expected_detector in row["gate_failures"]
    # The fault is caught on its first sighting by the expected gate.
    assert row["gate_first_pass"][fault.expected_detector] is False
    log = Path(row["decision_log_path"])
    report = replay_run(log, expected_row=row)
    assert report.errors == () and report.row_matches


def test_permission_denial_escalates(tmp_path):
    row = run_scenario(
        tmp_path, "ll-discount",
        adapter=StubAgent(FIXTURES, behavior="request-network"),
    )
    assert row["outcome"] == "escalated"
    assert row["terminal_state"] == "ESCALATED"


def test_human_deny_rejects(tmp_path):
    cp = ScriptedCheckpoint({("ll-discount", "plan"): ("deny", None, None)})
    row = run_scenario(tmp_path, "ll-discount", checkpoint=cp)
    assert row["outcome"] == "rejected"
    assert row["terminal_state"] == "REJECTED"
    assert row["human_override"] is False


def test_human_override_records_exception(tmp_path):
    cp = ScriptedCheckpoint(
        {("ll-discount", "plan"): ("override", "risk accepted", "EXP-7")}
    )
    row = run_scenario(tmp_path, "ll-discount", checkpoint=cp)
    assert row["outcome"] == "completed"
    assert row["human_override"] is True
    log = Path(row["decision_log_path"])
    report = replay_run(log, expected_row=row)
    assert report.errors == () and report.row_matches


def test_crash_shaped_replay(tmp_path):
    # A completed run's log must verify and replay from a cold start.
    row = run_scenario(tmp_path, "ps-shipment")
    log = Path(row["decision_log_path"])
    check = verify_log(log)
    assert check["ok"] and check["records"] > 0
    report = replay_run(log, expected_row=row)
    assert report.hash_chain_ok
    assert report.decisions_reproduced == report.accepted
    assert report.row_matches
