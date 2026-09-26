"""Contract / schema-compatibility gate (order 30).

Compares the candidate JSON Schema against the pinned baseline and enforces
four breaking-change rules:

- SCHEMA.MALFORMED: candidate is not a JSON object.
- SCHEMA.REQUIRED_REMOVED (high): a required field was removed.
- SCHEMA.TYPE_NARROWED (high): a field type was narrowed incompatibly.
- SCHEMA.NEW_REQUIRED (medium): a newly required field has no default.

This is the reference architecture's highest-leverage cross-service control.
v1 uses local schema fixtures so the claim stays testable without a broker.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from harness.gates.base import Finding, GateContext, Verdict


def _as_type_set(t: Any) -> set[str] | None:
    """Normalize a JSON Schema 'type' to a set; None means unconstrained."""
    if t is None:
        return None
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return set(t)
    return None


def _type_compatible(baseline_type: Any, candidate_type: Any) -> bool:
    b = _as_type_set(baseline_type)
    c = _as_type_set(candidate_type)
    if b is None or c is None:
        return True  # unconstrained on either side: cannot prove narrowing
    return b <= c  # widening (or equal) is compatible; narrowing is not


def compare_schemas(
    baseline: Any, candidate: Any, path: str = "$"
) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(candidate, dict):
        findings.append(
            Finding("SCHEMA.MALFORMED", "critical", path,
                    "candidate schema is not a JSON object")
        )
        return findings
    if not isinstance(baseline, dict):
        return findings

    b_required = set(baseline.get("required", []) or [])
    c_required = set(candidate.get("required", []) or [])
    for field_name in sorted(b_required - c_required):
        findings.append(
            Finding(
                "SCHEMA.REQUIRED_REMOVED", "high", f"{path}.required",
                f"required field removed: {field_name}",
            )
        )
    c_props = candidate.get("properties", {}) or {}
    for field_name in sorted(c_required - b_required):
        prop = c_props.get(field_name, {})
        if not isinstance(prop, dict) or "default" not in prop:
            findings.append(
                Finding(
                    "SCHEMA.NEW_REQUIRED", "medium", f"{path}.required",
                    f"newly required field without default: {field_name}",
                )
            )

    b_props = baseline.get("properties", {}) or {}
    if not isinstance(c_props, dict):
        return findings
    for name in sorted(b_props):
        if name not in c_props:
            continue  # removing an optional property is compatible
        b_sub, c_sub = b_props[name], c_props[name]
        if not isinstance(b_sub, dict) or not isinstance(c_sub, dict):
            continue
        sub_path = f"{path}.properties.{name}"
        if not _type_compatible(b_sub.get("type"), c_sub.get("type")):
            findings.append(
                Finding(
                    "SCHEMA.TYPE_NARROWED", "high", sub_path,
                    f"type narrowed: {b_sub.get('type')} -> {c_sub.get('type')}",
                )
            )
        findings.extend(compare_schemas(b_sub, c_sub, sub_path))
    return findings


class ContractGate:
    name = "contract-compatibility"
    order = 30

    def run(self, context: GateContext) -> Verdict:
        started = time.monotonic()
        contract = context.task_manifest.get("contract", {})
        baseline_rel = contract.get("baseline", "schemas/baseline.json")
        candidate_rel = contract.get("candidate", "schemas/candidate.json")
        evidence = [
            f"baseline={baseline_rel}",
            f"candidate={candidate_rel}",
        ]
        baseline_path = context.workspace / baseline_rel
        candidate_path = context.workspace / candidate_rel
        if not baseline_path.is_file():
            return Verdict.build(
                [Finding("SCHEMA.BASELINE_MISSING", "high", baseline_rel,
                         "pinned baseline schema is missing")],
                evidence,
                self._ms(started),
            )
        if not candidate_path.is_file():
            return Verdict.build(
                [Finding("SCHEMA.CANDIDATE_MISSING", "high", candidate_rel,
                         "candidate schema is missing")],
                evidence,
                self._ms(started),
            )
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            return Verdict.build(
                [Finding("SCHEMA.MALFORMED", "critical", baseline_rel,
                         f"baseline is not valid JSON: {exc}")],
                evidence,
                self._ms(started),
            )
        try:
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            return Verdict.build(
                [Finding("SCHEMA.MALFORMED", "critical", candidate_rel,
                         f"candidate is not valid JSON: {exc}")],
                evidence,
                self._ms(started),
            )
        findings = compare_schemas(baseline, candidate)
        evidence.append(f"rules_checked=4 findings={len(findings)}")
        return Verdict.build(findings, evidence, self._ms(started))

    @staticmethod
    def _ms(started: float) -> int:
        import time as _t

        return int((_t.monotonic() - started) * 1000)
