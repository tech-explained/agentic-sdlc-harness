"""Policy: phase-scoped permissions and deterministic context injection.

Least privilege: effective permissions = policy[state] intersected with the
task-declared needs. An adapter cannot request expansion; excess requests
escalate (see Engine).
"""

from __future__ import annotations

from harness.orchestrator.model import State
from harness.util import canonical_json, digest_of

# Capability vocabulary (v1).
FS_READ = "fs.read"
FS_WRITE_ISOLATED = "fs.write.isolated"
SUBPROCESS_PYTEST = "subprocess.pytest"
SUBPROCESS_LINT = "subprocess.lint"
NETWORK = "network"  # never granted in v1; used to test denial


class PermissionPolicy:
    """Maps lifecycle state to the allowlisted capability set."""

    _PROFILE: dict[State, frozenset[str]] = {
        State.INTAKE: frozenset({FS_READ}),
        State.PLAN: frozenset({FS_READ}),
        State.IMPLEMENT: frozenset({FS_READ, FS_WRITE_ISOLATED}),
        State.REPAIR: frozenset({FS_READ, FS_WRITE_ISOLATED}),
        State.VERIFY: frozenset({FS_READ, SUBPROCESS_PYTEST, SUBPROCESS_LINT}),
        State.REVIEW: frozenset({FS_READ}),
        State.MERGE_READY: frozenset({FS_READ}),
        State.WAIT_HUMAN: frozenset(),
        State.COMPLETED: frozenset(),
        State.REJECTED: frozenset(),
        State.ESCALATED: frozenset(),
        State.CANCELLED: frozenset(),
    }

    def for_state(self, state: State) -> frozenset[str]:
        return self._PROFILE[state]

    def effective(self, state: State, declared_needs: frozenset[str]) -> frozenset[str]:
        """Policy permissions intersected with task-declared needs."""
        return self.for_state(state) & declared_needs


# ---------------------------------------------------------------------------
# Deterministic context injection
# ---------------------------------------------------------------------------

# Released policy baseline: the fixed, versioned foundation every run builds on.
POLICY_BASELINE: dict = {
    "policy_version": "1.0",
    "max_repairs": 3,
    "gate_order": ["lint", "test-execution", "contract-compatibility"],
    "checkpoint_rule": "plan and merge checkpoints are enforced when configured",
}

# Phase templates: fixed instructions per phase. Agents receive only the
# resolved bundle; they cannot select policies or credentials.
PHASE_TEMPLATES: dict[str, dict] = {
    "lifecycle": {
        "role": "bounded coding agent operating under harness control",
        "rules": [
            "Produce only the artifacts described for the current phase.",
            "Write only inside the provided working directory.",
            "Never request capabilities outside the granted set.",
            "Do not self-certify: verification is performed by independent gates.",
        ],
    },
    "PLAN": {
        "output": "short plan summary",
        "rules": ["Describe the intended change in under 200 words.", "No file writes."],
    },
    "IMPLEMENT": {
        "output": "implementation artifacts",
        "rules": ["Write files only under the allowed paths.", "Keep changes minimal."],
    },
    "REPAIR": {
        "output": "corrected implementation artifacts",
        "rules": [
            "Address the recorded gate findings.",
            "Rewrite the full artifact set; do not patch selectively.",
        ],
    },
}


class ContextAssembler:
    """Combines baseline, task manifest, and phase templates deterministically.

    The bundle is namespaced (no silent precedence surprises), canonicalized
    as JSON, and hashed. The digest is stored on the run; replay rejects a
    digest mismatch.
    """

    def __init__(
        self,
        baseline: dict | None = None,
        phase_templates: dict | None = None,
    ) -> None:
        self.baseline = baseline if baseline is not None else POLICY_BASELINE
        self.phase_templates = (
            phase_templates if phase_templates is not None else PHASE_TEMPLATES
        )

    def assemble(self, task_manifest: dict) -> tuple[dict, str]:
        bundle = {
            "baseline": self.baseline,
            "phase_templates": self.phase_templates,
            "task": {
                "task_id": task_manifest["task_id"],
                "goal": task_manifest.get("goal", ""),
                "allowed_paths": list(task_manifest.get("allowed_paths", [])),
                "needs": sorted(task_manifest.get("needs", [])),
            },
        }
        digest = digest_of(canonical_json(bundle))
        return bundle, digest

    def policy_digest(self) -> str:
        return digest_of(canonical_json(self.baseline))
