"""Gate protocol and layered verification pipeline.

Gates are deterministic, independent of the implementing agent, and fail
closed: non-zero exit, timeout, malformed output, or missing evidence is a
failure, never a pass. A gate may never waive its own failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from harness.util import digest_of, canonical_json

SEVERITIES = ("none", "low", "medium", "high", "critical")
_SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITIES)}
FAIL_THRESHOLD = "medium"  # gate fails when max finding severity >= medium


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str  # one of SEVERITIES
    path: str
    message: str

    @property
    def signature(self) -> str:
        """Normalized finding signature for failure learning (replay grouping)."""
        norm_path = self.path.replace("\\", "/")
        return digest_of(
            canonical_json(
                {"rule_id": self.rule_id, "path": norm_path, "message": self.message}
            )
        )


@dataclass(frozen=True)
class Verdict:
    status: str  # "pass" | "fail"
    findings: tuple[Finding, ...] = ()
    severity: str = "none"  # max finding severity, or "none"
    evidence: tuple[str, ...] = ()
    duration_ms: int = 0

    @staticmethod
    def build(
        findings: list[Finding], evidence: list[str], duration_ms: int
    ) -> "Verdict":
        worst = "none"
        for f in findings:
            if _SEVERITY_RANK[f.severity] > _SEVERITY_RANK[worst]:
                worst = f.severity
        status = (
            "fail"
            if _SEVERITY_RANK[worst] >= _SEVERITY_RANK[FAIL_THRESHOLD]
            else "pass"
        )
        return Verdict(
            status=status,
            findings=tuple(findings),
            severity=worst,
            evidence=tuple(evidence),
            duration_ms=duration_ms,
        )


@dataclass(frozen=True)
class GateContext:
    workspace: Path
    artifact_digest: str
    task_manifest: dict
    changed_paths: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    timeout_s: int
    env: dict[str, str] = field(default_factory=dict)


class Gate(Protocol):
    name: str
    order: int

    def run(self, context: GateContext) -> Verdict: ...


class GatePipeline:
    """Invokes gates serially in policy order; stops at the first failure.

    The artifact digest is verified before every gate: a changed digest
    invalidates prior verdicts and fails closed.
    """

    def __init__(self, gates: list[Gate], digest_of_workspace) -> None:
        self.gates = sorted(gates, key=lambda g: g.order)
        self._digest_of_workspace = digest_of_workspace

    def run_all(self, context: GateContext) -> list[tuple[str, Verdict]]:
        results: list[tuple[str, Verdict]] = []
        for gate in self.gates:
            current = self._digest_of_workspace(context.workspace, list(context.changed_paths))
            if current != context.artifact_digest:
                results.append(
                    (
                        gate.name,
                        Verdict.build(
                            [
                                Finding(
                                    rule_id="DIGEST.MISMATCH",
                                    severity="critical",
                                    path=".",
                                    message=(
                                        "artifact digest changed during verification; "
                                        "prior verdicts invalidated"
                                    ),
                                )
                            ],
                            [f"expected={context.artifact_digest}", f"actual={current}"],
                            0,
                        ),
                    )
                )
                break
            verdict = gate.run(context)
            results.append((gate.name, verdict))
            if verdict.status == "fail":
                break
        return results
