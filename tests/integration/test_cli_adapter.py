"""Integration: subprocess CLI adapter (contract, refusal, malformed output)."""

import json
import sys
from pathlib import Path

import pytest

from harness.agents.base import AgentContext
from harness.agents.cli import CliAgent, sanitized_env
from harness.evals.driver import load_manifest
from harness.orchestrator.model import State
from harness.orchestrator.policy import ContextAssembler
from harness.util import artifact_digest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "fixtures"


def make_context(tmp_path, workspace: Path) -> AgentContext:
    manifest = load_manifest(FIXTURES, "ll-discount")
    _, digest = ContextAssembler().assemble(manifest)
    return AgentContext(
        run_id="r" * 32,
        task_id="ll-discount",
        phase=State.IMPLEMENT,
        attempt=0,
        workspace=workspace,
        manifest=manifest,
        context_digest=digest,
        permissions=frozenset({"fs.read", "fs.write.isolated"}),
        seed=0,
    )


def write_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_agent.py"
    script.write_text(body, encoding="utf-8")
    return script


SUCCESS_BODY = """
import json
print(json.dumps({
    "status": "succeeded",
    "summary": "fake agent did the work",
    "changed_paths": ["impl.py"],
    "capabilities_used": ["fs.read", "fs.write.isolated"],
}))
"""


def test_cli_adapter_success(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Seed the workspace the way the driver would, then let the fake CLI act.
    from harness.evals.driver import Driver  # noqa: F401  (import sanity)
    import shutil

    seed = FIXTURES / "tasks" / "ll-discount" / "seed"
    shutil.copytree(seed, workspace, dirs_exist_ok=True)
    shutil.copy(
        FIXTURES / "tasks" / "ll-discount" / "solution" / "impl.py",
        workspace / "impl.py",
    )
    script = write_script(tmp_path, SUCCESS_BODY)
    adapter = CliAgent("fake-cli", ["python", str(script)])
    result = adapter.execute(make_context(tmp_path, workspace))
    assert result.status == "succeeded"
    assert result.changed_paths == ("impl.py",)
    assert result.artifact_digest == artifact_digest(workspace, ["impl.py"])
    assert result.summary == "fake agent did the work"


def test_cli_adapter_malformed_json(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    script = write_script(tmp_path, "print('not json at all')")
    adapter = CliAgent("fake-cli", ["python", str(script)])
    result = adapter.execute(make_context(tmp_path, workspace))
    assert result.status == "failed"
    assert "malformed" in result.error


def test_cli_adapter_refuses_without_capability(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    marker = tmp_path / "spawned.marker"
    script = write_script(
        tmp_path,
        f"from pathlib import Path; Path({str(marker)!r}).write_text('x'); "
        "print('{{}}')",
    )
    adapter = CliAgent(
        "fake-cli", ["python", str(script)],
        required_capabilities=("network",),
    )
    result = adapter.execute(make_context(tmp_path, workspace))
    assert result.status == "failed"
    assert result.requested_capabilities == ("network",)
    assert not marker.exists()  # subprocess never spawned


def test_cli_adapter_timeout(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    script = write_script(tmp_path, "import time; time.sleep(30)")
    adapter = CliAgent("fake-cli", ["python", str(script)], timeout_s=1)
    from harness.agents.base import AgentTimeout

    with pytest.raises(AgentTimeout):
        adapter.execute(make_context(tmp_path, workspace))


def test_sanitized_env_drops_secrets():
    env = sanitized_env({"AWS_SECRET_ACCESS_KEY": "shh", "CUSTOM": "ok"})
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["CUSTOM"] == "ok"
    assert env["PYTHONUTF8"] == "1"


def test_cli_adapter_end_to_end_with_driver(tmp_path):
    """The CLI adapter plugs into the driver behind the same interface."""
    import shutil

    from harness.evals.driver import Driver, DriverConfig, Scenario
    from harness.orchestrator.checkpoints import ScriptedCheckpoint

    workspace_seed = tmp_path / "seedws"
    seed = FIXTURES / "tasks" / "ll-fizzbuzz" / "seed"
    shutil.copytree(seed, workspace_seed)

    script = write_script(
        tmp_path,
        """
import json, os, shutil
from pathlib import Path
sol = Path(%r)
for src in sorted(sol.rglob("*")):
    if src.is_file():
        rel = src.relative_to(sol)
        dest = Path(".") / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
phase = os.environ.get("HARNESS_PHASE", "")
caps = ["fs.read"] if phase == "PLAN" else ["fs.read", "fs.write.isolated"]
print(json.dumps({
    "status": "succeeded",
    "summary": "cli agent copied the solution",
    "changed_paths": ["impl.py", "schemas/candidate.json", "schemas/baseline.json"],
    "capabilities_used": caps,
}))
""" % str(FIXTURES / "tasks" / "ll-fizzbuzz" / "solution"),
    )
    config = DriverConfig(
        fixtures_root=FIXTURES,
        results_dir=tmp_path / "results",
        checkpoint=ScriptedCheckpoint({}),
    )
    adapter = CliAgent("fake-cli", ["python", str(script)])
    row = Driver(config, adapter).run(
        Scenario(task_id="ll-fizzbuzz", scenario="clean")
    )
    assert row["outcome"] == "completed"
    assert row["agent"] == "fake-cli"
