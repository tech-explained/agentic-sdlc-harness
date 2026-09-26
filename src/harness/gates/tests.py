"""Test-execution gate (order 20): runs the allowlisted unit-test command.

Fails on test failure, timeout, collection error, or empty test discovery.
Records counts, failure IDs, duration, and output digest. Fails closed on
malformed output.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

from harness.agents.cli import sanitized_env
from harness.gates.base import Finding, GateContext, Verdict
from harness.util import digest_of

_FAILED_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
_ERROR_RE = re.compile(r"^ERROR\s+(\S+)", re.MULTILINE)
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error|skipped)", re.MULTILINE)


class TestGate:
    name = "test-execution"
    order = 20

    def run(self, context: GateContext) -> Verdict:
        started = time.monotonic()
        test_cmd = list(context.task_manifest.get("commands", {}).get("test", []))
        if not test_cmd:
            return Verdict.build(
                [Finding("TEST.NOT_CONFIGURED", "high", ".",
                         "task manifest declares no test command")],
                ["test command missing from task manifest"],
                0,
            )
        test_cmd = [sys.executable if tok == "python" else tok for tok in test_cmd]
        evidence = [f"command={' '.join(test_cmd)}"]
        env = sanitized_env(context.env)
        # The workspace must be importable for `from impl import ...`, while the
        # harness package itself must stay importable for subprocess tools.
        import os as _os

        existing = env.get("PYTHONPATH", "")
        parts = [str(context.workspace)]
        if existing:
            parts.append(existing)
        env["PYTHONPATH"] = _os.pathsep.join(parts)
        try:
            proc = subprocess.run(
                test_cmd,
                cwd=context.workspace,
                env=env,
                capture_output=True,
                text=True,
                timeout=context.timeout_s,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return Verdict.build(
                [Finding("TEST.TIMEOUT", "critical", ".",
                         f"tests exceeded {context.timeout_s}s")],
                evidence + ["timeout"],
                int((time.monotonic() - started) * 1000),
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        output = (proc.stdout or "") + (proc.stderr or "")
        evidence.append(f"exit_code={proc.returncode}")
        evidence.append(f"output_digest={digest_of(output)}")

        counts = dict(_COUNT_RE.findall(output))
        n_failed = int(counts.get("failed", 0))
        n_error = int(counts.get("error", 0))
        n_passed = int(counts.get("passed", 0))
        evidence.append(
            f"passed={n_passed} failed={n_failed} error={n_error} "
            f"duration_ms={duration_ms}"
        )

        findings: list[Finding] = []
        if proc.returncode != 0 and "no tests ran" in output.lower():
            findings.append(
                Finding("TEST.NO_TESTS", "high", "tests/",
                        "test discovery found no tests")
            )
            return Verdict.build(findings, evidence, duration_ms)
        if "collection error" in output.lower() or _ERROR_RE.search(output):
            for test_id in sorted(set(_ERROR_RE.findall(output))):
                findings.append(
                    Finding("TEST.COLLECTION_ERROR", "high", test_id,
                            f"collection error: {test_id}")
                )
            if not findings:
                findings.append(
                    Finding("TEST.COLLECTION_ERROR", "high", "tests/",
                            "test collection failed")
                )
            return Verdict.build(findings, evidence, duration_ms)
        for test_id in sorted(set(_FAILED_RE.findall(output))):
            findings.append(
                Finding("TEST.FAILURE", "high", test_id,
                        f"test failed: {test_id}")
            )
        if proc.returncode != 0 and not findings:
            findings.append(
                Finding("TEST.UNKNOWN_FAILURE", "high", "tests/",
                        f"test command exited {proc.returncode} with no parsed failures")
            )
        return Verdict.build(findings, evidence, duration_ms)
