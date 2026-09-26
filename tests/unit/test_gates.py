"""Unit tests: gates (lint checker, test gate parsing, contract rules, pipeline)."""

from pathlib import Path

import pytest

from harness.gates.base import Finding, GateContext, GatePipeline, Verdict
from harness.gates.contract import compare_schemas
from harness.gates.lint_check import check as lint_check
from harness.util import artifact_digest

TIMEOUT = 30


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def gate_context(workspace: Path, manifest: dict | None = None) -> GateContext:
    manifest = manifest or {}
    return GateContext(
        workspace=workspace,
        artifact_digest="",
        task_manifest=manifest,
        changed_paths=tuple(manifest.get("_changed", [])),
        allowed_paths=tuple(manifest.get("allowed_paths", [])),
        timeout_s=TIMEOUT,
        env={},
    )


# --- lint checker -----------------------------------------------------------


def test_lint_unused_import(tmp_path):
    write(tmp_path / "impl.py", "import os\n\n\ndef f():\n    return 1\n")
    findings = lint_check(tmp_path, [], ["impl.py"], set())
    assert any(
        f["rule_id"] == "LINT.UNUSED_IMPORT" and f["severity"] == "medium"
        for f in findings
    )


def test_lint_scope_violation(tmp_path):
    write(tmp_path / "impl.py", "X = 1\n")
    write(tmp_path / "notes.txt", "hello\n")
    findings = lint_check(tmp_path, ["impl.py"], ["impl.py"], {"impl.py"})
    assert any(f["rule_id"] == "SCOPE.VIOLATION" for f in findings)


def test_lint_syntax_error(tmp_path):
    write(tmp_path / "impl.py", "def broken(:\n")
    findings = lint_check(tmp_path, [], ["impl.py"], set())
    assert any(f["rule_id"] == "LINT.SYNTAX_ERROR" for f in findings)


def test_lint_clean(tmp_path):
    write(tmp_path / "impl.py", "def f():\n    return 1\n")
    findings = lint_check(tmp_path, ["impl.py"], ["impl.py"], {"impl.py"})
    assert [f for f in findings if f["severity"] in ("medium", "high", "critical")] == []


# --- contract rules ----------------------------------------------------------


def base_schema():
    return {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
        },
        "required": ["a", "b"],
    }


def test_contract_compatible():
    assert compare_schemas(base_schema(), base_schema()) == []


def test_contract_required_removed():
    candidate = base_schema()
    candidate["required"] = ["a"]
    del candidate["properties"]["b"]
    findings = compare_schemas(base_schema(), candidate)
    assert any(f.rule_id == "SCHEMA.REQUIRED_REMOVED" for f in findings)


def test_contract_type_narrowed():
    candidate = base_schema()
    candidate["properties"]["b"] = {"type": "string"}
    findings = compare_schemas(base_schema(), candidate)
    assert any(f.rule_id == "SCHEMA.TYPE_NARROWED" for f in findings)


def test_contract_type_widened_ok():
    baseline = base_schema()
    baseline["properties"]["b"] = {"type": "integer"}
    candidate = base_schema()
    candidate["properties"]["b"] = {"type": ["integer", "string"]}
    assert compare_schemas(baseline, candidate) == []


def test_contract_new_required_without_default():
    candidate = base_schema()
    candidate["required"] = ["a", "b", "c"]
    candidate["properties"]["c"] = {"type": "string"}
    findings = compare_schemas(base_schema(), candidate)
    assert any(f.rule_id == "SCHEMA.NEW_REQUIRED" for f in findings)


def test_contract_new_required_with_default_ok():
    candidate = base_schema()
    candidate["required"] = ["a", "b", "c"]
    candidate["properties"]["c"] = {"type": "string", "default": "x"}
    assert compare_schemas(base_schema(), candidate) == []


def test_contract_malformed_candidate():
    findings = compare_schemas(base_schema(), [1, 2, 3])
    assert any(f.rule_id == "SCHEMA.MALFORMED" for f in findings)


# --- pipeline ----------------------------------------------------------------


class _Gate:
    def __init__(self, name, order, verdict):
        self.name = name
        self.order = order
        self._verdict = verdict

    def run(self, context):
        return self._verdict


def _ctx_with_digest(workspace: Path, files: list[str]) -> GateContext:
    digest = artifact_digest(workspace, files)
    return GateContext(
        workspace=workspace,
        artifact_digest=digest,
        task_manifest={},
        changed_paths=tuple(files),
        allowed_paths=(),
        timeout_s=TIMEOUT,
        env={},
    )


def test_pipeline_stops_at_first_failure(tmp_path):
    write(tmp_path / "a.txt", "x")
    ctx = _ctx_with_digest(tmp_path, ["a.txt"])
    calls = []

    class Rec(_Gate):
        def run(self, context):
            calls.append(self.name)
            return self._verdict

    pipe = GatePipeline(
        [
            Rec("lint", 10, Verdict.build([], [], 1)),
            Rec("test-execution", 20, Verdict.build(
                [Finding("TEST.FAILURE", "high", "t", "m")], [], 1)),
            Rec("contract-compatibility", 30, Verdict.build([], [], 1)),
        ],
        artifact_digest,
    )
    results = pipe.run_all(ctx)
    assert [n for n, _ in results] == ["lint", "test-execution"]
    assert results[1][1].status == "fail"
    assert calls == ["lint", "test-execution"]


def test_pipeline_digest_mismatch_fails_closed(tmp_path):
    write(tmp_path / "a.txt", "x")
    ctx = GateContext(
        workspace=tmp_path,
        artifact_digest="sha256:" + "0" * 64,
        task_manifest={},
        changed_paths=("a.txt",),
        allowed_paths=(),
        timeout_s=TIMEOUT,
        env={},
    )
    pipe = GatePipeline([_Gate("lint", 10, Verdict.build([], [], 1))], artifact_digest)
    results = pipe.run_all(ctx)
    assert results[0][1].status == "fail"
    assert results[0][1].findings[0].rule_id == "DIGEST.MISMATCH"


def test_pipeline_respects_order(tmp_path):
    write(tmp_path / "a.txt", "x")
    ctx = _ctx_with_digest(tmp_path, ["a.txt"])
    order = []

    class Rec(_Gate):
        def run(self, context):
            order.append(self.name)
            return self._verdict

    pipe = GatePipeline(
        [Rec("contract-compatibility", 30, Verdict.build([], [], 0)),
         Rec("lint", 10, Verdict.build([], [], 0))],
        artifact_digest,
    )
    pipe.run_all(ctx)
    assert order == ["lint", "contract-compatibility"]
