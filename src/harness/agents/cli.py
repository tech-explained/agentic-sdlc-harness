"""Subprocess-based coding-assistant CLI adapter (v1, subprocess-only).

The adapter resolves an explicit command, runs it as a subprocess with a
sanitized environment and an isolated working directory, and parses a JSON
AgentResult from stdout. No shell, no interpolation, fixed timeout.

The adapter pre-checks its required capabilities against the granted set and
refuses to spawn the subprocess when a required capability is not granted
(permission denial without subprocess execution).

Wiring a specific vendor CLI is configuration: command + the JSON result
contract below. The v1 measured run uses the deterministic stub suite only;
this adapter is integration-tested and documented separately.

The adapter injects read-only HARNESS_* variables into the subprocess
environment (HARNESS_PHASE, HARNESS_RUN_ID, HARNESS_TASK_ID, HARNESS_ATTEMPT)
so the CLI can report phase-accurate capabilities_used. These names cannot be
overridden by extra_env.

Expected stdout JSON (single object):
  {"status": "succeeded"|"failed", "summary": "...",
   "changed_paths": ["src/a.py"], "artifact_digest": "sha256:...",
   "capabilities_used": [...], "requested_capabilities": [...], "error": "..."}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from harness.agents.base import AgentContext, AgentResult, AgentTimeout
from harness.util import artifact_digest

# Minimal allowlisted environment for adapter subprocesses.
_ENV_ALLOWLIST = ("PATH", "PYTHONPATH", "PYTHONUTF8", "HOME", "SYSTEMROOT")

_SECRET_HINTS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL", "PRIVATE",
    "BEARER", "SESSION", "COOKIE",
)


def _looks_secret(name: str) -> bool:
    upper = name.upper()
    return any(hint in upper for hint in _SECRET_HINTS)


def sanitized_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env["PYTHONUTF8"] = "1"
    if extra:
        for k, v in extra.items():
            if _looks_secret(k):
                continue  # never forward secret-looking values to subprocesses
            env[k] = v
    return env


class CliAgent:
    """Generic subprocess CLI adapter behind the AgentAdapter interface."""

    def __init__(
        self,
        adapter_id: str,
        command: list[str],
        required_capabilities: tuple[str, ...] = (),
        timeout_s: int = 300,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("command must be a non-empty argv list")
        self.adapter_id = adapter_id
        self.command = [sys.executable if tok == "python" else tok for tok in command]
        self.required_capabilities = tuple(required_capabilities)
        self.timeout_s = timeout_s
        self.extra_env = extra_env or {}

    def execute(self, context: AgentContext) -> AgentResult:
        missing = [c for c in self.required_capabilities if c not in context.permissions]
        if missing:
            # Least privilege: refuse before spawning anything.
            return AgentResult(
                run_id=context.run_id,
                phase=context.phase,
                attempt=context.attempt,
                status="failed",
                requested_capabilities=tuple(missing),
                error=f"adapter refused: capabilities not granted: {missing}",
            )
        harness_env = {
            "HARNESS_PHASE": context.phase.value,
            "HARNESS_RUN_ID": context.run_id,
            "HARNESS_TASK_ID": context.task_id,
            "HARNESS_ATTEMPT": str(context.attempt),
        }
        env_extra = {**self.extra_env, **harness_env}  # harness vars win
        try:
            proc = subprocess.run(
                self.command,
                cwd=context.workspace,
                env=sanitized_env(env_extra),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentTimeout(
                f"adapter {self.adapter_id} exceeded {self.timeout_s}s"
            ) from exc
        return self._parse_result(context, proc)

    def _parse_result(
        self, context: AgentContext, proc: "subprocess.CompletedProcess[str]"
    ) -> AgentResult:
        stdout = (proc.stdout or "").strip()
        try:
            data = json.loads(stdout.splitlines()[-1]) if stdout else {}
            if not isinstance(data, dict):
                raise ValueError("top-level JSON must be an object")
        except (ValueError, IndexError) as exc:
            return AgentResult(
                run_id=context.run_id,
                phase=context.phase,
                attempt=context.attempt,
                status="failed",
                error=f"adapter returned malformed JSON: {exc}; "
                f"exit={proc.returncode} stderr={proc.stderr[-500:] if proc.stderr else ''}",
            )
        changed = tuple(data.get("changed_paths", ()))
        digest = data.get("artifact_digest", "")
        if not digest and changed:
            try:
                digest = artifact_digest(context.workspace, list(changed))
            except FileNotFoundError:
                digest = ""
        return AgentResult(
            run_id=context.run_id,
            phase=context.phase,
            attempt=context.attempt,
            status="succeeded" if data.get("status") == "succeeded" else "failed",
            artifact_digest=digest,
            changed_paths=changed,
            capabilities_used=tuple(data.get("capabilities_used", ())),
            requested_capabilities=tuple(data.get("requested_capabilities", ())),
            summary=str(data.get("summary", "")),
            error=str(data.get("error", "")),
        )
