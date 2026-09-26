"""Unit tests: fault transforms and checkpoint providers."""

import json
from pathlib import Path

import pytest

from harness.evals.faults import Fault, apply_fault, load_faults
from harness.orchestrator.checkpoints import ScriptedCheckpoint
from harness.orchestrator.model import Run, State

REPO_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_fault_manifest_loads():
    faults = load_faults(REPO_FIXTURES / "faults" / "manifest.json")
    assert [f.fault_id for f in faults] == ["F1", "F2", "F3", "F4", "F5", "F6"]
    detectors = {f.fault_id: f.expected_detector for f in faults}
    assert detectors == {
        "F1": "lint", "F2": "test-execution", "F3": "test-execution",
        "F4": "contract-compatibility", "F5": "contract-compatibility",
        "F6": "lint",
    }


def _fault(**overrides):
    base = dict(fault_id="FX", task_id="t", expected_detector="lint",
                kind="append_text", path="impl.py", params={})
    base.update(overrides)
    return Fault(**base)


def test_append_text(tmp_path):
    (tmp_path / "impl.py").write_text("X = 1\n")
    f = _fault(kind="append_text", params={"text": "\nimport os\n"})
    apply_fault(tmp_path, f)
    assert "import os" in (tmp_path / "impl.py").read_text()


def test_replace_requires_find_present(tmp_path):
    (tmp_path / "impl.py").write_text("X = 1\n")
    f = _fault(kind="replace", params={"find": "missing", "replace": "y"})
    with pytest.raises(ValueError):
        apply_fault(tmp_path, f)


def test_json_remove_required(tmp_path):
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    }
    (tmp_path / "s.json").write_text(json.dumps(schema))
    f = _fault(kind="json_remove_required", path="s.json",
               params={"at": "root", "field": "b"})
    apply_fault(tmp_path, f)
    data = json.loads((tmp_path / "s.json").read_text())
    assert data["required"] == ["a"]
    assert "b" not in data["properties"]


def test_json_narrow_type(tmp_path):
    schema = {"properties": {"attempt": {"type": "integer"}}}
    (tmp_path / "s.json").write_text(json.dumps(schema))
    f = _fault(kind="json_narrow_type", path="s.json",
               params={"at": "properties.attempt", "to_type": "string"})
    apply_fault(tmp_path, f)
    data = json.loads((tmp_path / "s.json").read_text())
    assert data["properties"]["attempt"]["type"] == "string"


def test_write_outside_allowed(tmp_path):
    f = _fault(kind="write_outside_allowed", path="exfiltrated.txt",
               params={"text": "secret\n"})
    apply_fault(tmp_path, f)
    assert (tmp_path / "exfiltrated.txt").read_text() == "secret\n"


def test_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        apply_fault(tmp_path, _fault(kind="nope"))


def _run():
    return Run(
        run_id="r" * 32, state=State.WAIT_HUMAN, task_id="t", attempt=0,
        context_digest="sha256:x", permissions=frozenset(),
    )


def test_scripted_checkpoint_flows():
    cp = ScriptedCheckpoint({("t", "plan"): ("override", "risk accepted", "EXP-9")})
    cid = cp.request(_run(), "plan review")
    res = cp.resolve(cid, "approve", actor="human:test")
    assert res["action"] == "approve"
    cid2 = cp.request(_run(), "plan review")
    res2 = cp.resolve(cid2, "override", actor="human:test",
                      rationale="risk accepted", exception_id="EXP-9")
    assert res2["exception_id"] == "EXP-9"
    assert cp.decide("t", "plan") == ("override", "risk accepted", "EXP-9")
    assert cp.decide("t", "merge") is None


def test_override_requires_rationale():
    cp = ScriptedCheckpoint({})
    cid = cp.request(_run(), "x")
    with pytest.raises(ValueError):
        cp.resolve(cid, "override", actor="human:test")
    cid2 = cp.request(_run(), "x")
    with pytest.raises(ValueError):
        cp.resolve(cid2, "bogus", actor="human:test")
