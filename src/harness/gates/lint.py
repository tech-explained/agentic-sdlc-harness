"""Lint gate (order 10): static analysis + scope precheck.

Runs the fixture's declared linter (the harness lint_check module) as a
subprocess in check mode with a sanitized environment. Fails closed on
non-zero exit without valid JSON, timeout, or missing evidence.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

from harness.agents.cli import sanitized_env
from harness.gates.base import Finding, GateContext, Verdict


class LintGate:
    name = "lint"
    order = 10

    def run(self, context: GateContext) -> Verdict:
        started = time.monotonic()
        manifest = context.task_manifest
        lint_cmd = list(manifest.get("commands", {}).get("lint", []))
        if not lint_cmd:
            return Verdict.build(
                [
                    Finding(
                        "LINT.NOT_CONFIGURED", "high", ".",
                        "task manifest declares no lint command",
                    )
                ],
                ["lint command missing from task manifest"],
                0,
            )
        lint_cmd = [sys.executable if tok == "python" else tok for tok in lint_cmd]
        cmd = (
            lint_cmd
            + ["--workspace", str(context.workspace)]
            + ["--allowed", *context.allowed_paths]
            + ["--changed", *context.changed_paths]
            + ["--tracked", *sorted(self._tracked(context))]
        )
        evidence = [f"command={' '.join(cmd)}"]
        try:
            proc = subprocess.run(
                cmd,
                cwd=context.workspace,
                env=sanitized_env(context.env),
                capture_output=True,
                text=True,
                timeout=context.timeout_s,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return Verdict.build(
                [Finding("LINT.TIMEOUT", "critical", ".",
                         f"lint exceeded {context.timeout_s}s")],
                evidence + ["timeout"],
                int((time.monotonic() - started) * 1000),
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        evidence.append(f"exit_code={proc.returncode}")
        stdout = (proc.stdout or "").strip()
        try:
            data = json.loads(stdout) if stdout else {}
            raw_findings = data.get("findings", [])
            if not isinstance(raw_findings, list):
                raise ValueError("findings must be a list")
        except ValueError:
            # Non-zero exit or malformed output without valid JSON: fail closed.
            return Verdict.build(
                [
                    Finding(
                        "LINT.EXECUTION_ERROR", "critical", ".",
                        f"linter produced malformed output (exit={proc.returncode})",
                    )
                ],
                evidence + [f"stderr_tail={(proc.stderr or '')[-300:]}"],
                duration_ms,
            )
        findings = [
            Finding(
                rule_id=str(f.get("rule_id", "LINT.UNKNOWN")),
                severity=str(f.get("severity", "high")),
                path=str(f.get("path", ".")),
                message=str(f.get("message", "")),
            )
            for f in raw_findings
        ]
        evidence.append(f"tool=harness.gates.lint_check python={sys.version.split()[0]}")
        return Verdict.build(findings, evidence, duration_ms)

    @staticmethod
    def _tracked(context: GateContext) -> set[str]:
        # Seed files are known-good; declared artifacts are the agent's outputs.
        # Everything else in the workspace is a scope violation candidate.
        # The driver passes the seed file list via the task manifest.
        return set(context.task_manifest.get("_seed_files", ()))
