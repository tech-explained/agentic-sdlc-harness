# Usage

## Quickstart: run the demo end-to-end

The `demo` command drives one clean task (`ll-discount` by default) through
the full lifecycle — INTAKE → PLAN → IMPLEMENT → VERIFY (lint → tests →
contract) → REVIEW → MERGE — using the deterministic stub agent, with a
scripted policy standing in for the human at checkpoints:

```bash
harness demo --task ll-discount
```

Expected output: a JSON summary of the run, followed by a one-line
digest, for example:

```
outcome=completed retries=0 log=results/demo/<run_id>/decisions.jsonl
```

`outcome=completed` means the task cleared every gate; `retries=0` means no
repair loop was needed; `log=` points at the hash-chained decision log for
this run. Demo artifacts land under `results/demo/` (git-ignored).

> All commands below assume an installed package (`pip install -e ".[dev]"`,
> see [SETUP.md](SETUP.md)). Without installing, prefix with
> `PYTHONPATH=src python -m harness.cli.main` and run from the repo root.

## CLI reference

Every command accepts `--fixtures` and `--results` (defaults: `fixtures/`
and `results/` under the repo root).

| Command | What it does | Example |
|---|---|---|
| `validate-fixtures` | Validates the 12 task fixtures and 6 fault manifests (seed/solution/manifest shape). Exits non-zero on any problem. | `harness validate-fixtures` |
| `demo` | Runs one clean scenario end-to-end with the stub agent. `--task` picks the fixture (default `ll-discount`). Prints the run row as JSON plus `outcome=… retries=… log=…`. | `harness demo --task eh-validate` |
| `run-eval` | Runs the full measured evaluation: 18 runs (12 clean tasks + 6 single-fault scenarios) with the stub agent only. Takes ~10 s. Writes `metrics.json`, `run_table.json`, `manifest.json`, and per-run logs under `results/runs/` (git-ignored; reproducible). | `harness run-eval` |
| `replay` | Replays decision logs against a fresh engine and verifies integrity: hash chain, record-by-record decision reproduction, terminal state. Prints `[OK]`/`[FAIL]` per log with record counts. `--log` replays a single `decisions.jsonl`. | `harness replay` or `harness replay --log results/runs/<run_id>/decisions.jsonl` |
| `verify-results` | End-to-end results audit: checks `run_table.json`/`metrics.json`/`manifest.json` exist and agree (expects 18 rows), verifies every log's SHA-256 hash chain, replays every run, and recomputes the metrics from the run table — failing (`VERIFY-FAIL: …`) on any divergence. | `harness verify-results` |

## Wiring a custom agent adapter

Adapters are bounded workers behind one interface (`src/harness/agents/base.py`).
They never advance lifecycle state — only the orchestrator does.

```python
from harness.agents.base import AgentAdapter, AgentContext, AgentResult

class MyAdapter:
    adapter_id = "my-agent"          # required by the protocol

    def execute(self, context: AgentContext) -> AgentResult:
        ...
```

`AgentContext` gives you `run_id`, `task_id`, `phase` (`PLAN`, `IMPLEMENT`,
or `REPAIR`), `attempt`, `workspace` (an isolated working copy — write
only here), `manifest`, `context_digest`, `permissions` (the effective
capability grants for this phase), and `seed`. Return an `AgentResult` with
`status` of `"succeeded"` or `"failed"`, the `artifact_digest`,
`changed_paths`, `capabilities_used` / `requested_capabilities`, a
`summary`, and `error` on failure.

### Subprocess CLI adapter (for real coding assistants)

`src/harness/agents/cli.py` ships a subprocess-only adapter — no vendor SDK:

```python
from harness.agents.cli import CliAgent

adapter = CliAgent(
    adapter_id="my-cli",
    command=["my-cli", "--task", "{task}"],  # literal argv, no interpolation
    required_capabilities=("fs.read", "fs.write.isolated"),
    timeout_s=300,
)
```

- `command` is a fixed argv list — no shell, no string interpolation (the
  token `"python"` is mapped to `sys.executable`).
- The subprocess runs with `cwd` set to the isolated workspace and a
  **sanitized environment**: only allowlisted keys are forwarded, and
  secret-looking values are never passed through, even via `extra_env`.
- Read-only `HARNESS_*` variables are injected: `HARNESS_PHASE`,
  `HARNESS_RUN_ID`, `HARNESS_TASK_ID`, `HARNESS_ATTEMPT`.
- If any `required_capabilities` entry is outside the phase's effective
  permission set, the adapter **refuses before spawning** and reports the
  missing capabilities (the engine then escalates the run).

The CLI must print a single JSON object on stdout (the last line is parsed):

```json
{
  "status": "succeeded",
  "summary": "what was done",
  "changed_paths": ["impl.py"],
  "artifact_digest": "sha256:… (optional; computed if omitted)",
  "capabilities_used": ["fs.read", "fs.write.isolated"],
  "requested_capabilities": [],
  "error": ""
}
```

Malformed output, a non-zero exit without valid JSON, or a timeout is a
failure — gates and adapters fail closed. A timeout raises `AgentTimeout`,
which the driver records as an `AGENT_FAILED` event.

## Writing a custom gate

Gates implement the `Gate` protocol (`src/harness/gates/base.py`):

```python
from harness.gates.base import Gate, GateContext, Verdict, Finding

class MyGate:
    name = "my-check"
    order = 25                      # policy order (lint=10, tests=20, contract=30)

    def run(self, context: GateContext) -> Verdict:
        ...
```

`GateContext` gives you `workspace`, `artifact_digest`, `task_manifest`,
`changed_paths`, `allowed_paths`, `timeout_s`, and `env`. Build the
`Verdict` with `Verdict.build(findings, evidence, duration_ms)`; a finding
is `Finding(rule_id, severity, path, message)` with severity in
`none | low | medium | high | critical`. The gate **fails when the worst
finding severity is `medium` or higher** (`FAIL_THRESHOLD = "medium"`).

Rules of the pipeline (`GatePipeline`):

- Gates run **serially in `order`**, lowest first; the pipeline **stops at
  the first failure**.
- The artifact digest is re-verified before every gate — a changed digest
  invalidates prior verdicts and fails closed.
- A gate may never waive its own failure; non-deterministic behavior,
  missing evidence, or a timeout is a failure, never a pass.

To register your gate, add it to the gate list in
`src/harness/evals/driver.py` (currently
`gates: list[Gate] = [LintGate(), TestGate(), ContractGate()]`); ordering
is derived from each gate's `order`, so pick a value that slots your gate
where you want it in the policy sequence.

## Reading `results/metrics.json`

`harness run-eval` writes `results/metrics.json` (formulas live in
`src/harness/evals/metrics.py`). In plain words:

- **per-gate first-pass rate** (per gate: `lint`, `test-execution`,
  `contract-compatibility`) — of the runs that reached a gate, the share
  that passed it on the first try. v1: 16/18 (0.889) on each gate.
- **unaided all-gates rate** — the share of runs that cleared every gate
  with zero repair retries and no human override. v1: 12/18 (0.667).
- **retry distribution** — `mean`, `median`, `max`, and `zero_retry_share`
  across runs. v1: mean 0.33, median 0, max 1, zero-retry share 0.667.
- **injected-fault catch rate** — of the fault-injected runs, the share
  where the *expected* detector gate was the one that failed. Credit only
  when the expected gate catches it. v1: 6/6 (1.0).
- **human override rate** — runs containing a human override event divided
  by total runs; `"not observed"` when no checkpoints resolved. v1: 0.0
  (checkpoints were auto-approved by a scripted policy; override/deny
  paths are covered by integration tests).

Companion files: `run_table.json` (the normalized per-run table, 18 rows),
`manifest.json` (run IDs, scenarios, decision-log digests), and per-run
decision logs plus isolated workspaces under `results/runs/` (git-ignored;
reproducible via `run-eval`). `results/README.md` documents the measured v1
numbers in full.

## Replaying and verifying a run

Every run writes an append-only, hash-chained `decisions.jsonl`. To prove a
run happened exactly as recorded:

```bash
# replay every run's log, verifying chain + decision reproduction
harness replay

# replay one run
harness replay --log results/runs/<run_id>/decisions.jsonl

# full audit: files agree, chains verify, replays reproduce, metrics recompute
harness verify-results
```

`verify-results` is the strongest check: it confirms the three results
files exist and agree, verifies each log's SHA-256 chain, replays each run
against a fresh engine comparing state/attempt/decision digests
record-by-record, and recomputes the metrics from the run table — printing
`VERIFY-FAIL: …` and exiting non-zero on any divergence.
