# Evaluation (v1, measured)

## Design

- **Agent**: deterministic stub suite only. The stub copies the fixture's
  solution tree into the isolated workspace; on repair it restores
  seed+solution state (re-copying the solution, deleting untracked files).
- **Tasks**: 12 synthetic fixtures — 4 local-logic, 3 error-handling,
  3 consumer-schema, 2 provider-schema (`fixtures/tasks/`).
- **Faults**: 6 single-fault scenarios, each naming exactly one expected
  detector gate (`fixtures/faults/manifest.json`):

  | fault | description | expected gate |
  |---|---|---|
  | F1 | unused import | lint |
  | F2 | incorrect branch result | test-execution |
  | F3 | deleted required behavior | test-execution |
  | F4 | removed required schema field | contract-compatibility |
  | F5 | narrowed schema field type | contract-compatibility |
  | F6 | write outside allowed paths | lint (scope) |

  Faults are explicit transforms applied by the harness after the first
  implement step — never agent improvisation. Multi-fault variants are out
  of scope for v1.
- **Checkpoints**: a scripted policy auto-approves the plan and merge
  checkpoints in measured runs (standing in for the human). Override and
  deny paths are exercised in integration tests, not in the measured run.

## Metrics (single implementation: `harness/evals/metrics.py`)

- **Per-gate first-pass rate**: runs where the gate passed on its first
  invocation / runs that invoked it.
- **Unaided all-gates rate**: runs reaching `COMPLETED` with zero repair
  retries and no human override / all runs.
- **Retry distribution**: mean, median, max, zero-retry share.
- **Injected-fault catch rate**: fault runs where the *expected* detector
  gate failed / all fault runs.
- **Human override rate**: runs with an override / runs with resolved
  checkpoints (`"not observed"` when none resolve).
- **Failure learning**: top normalized finding signatures across runs.

## Reproducing

```bash
PYTHONPATH=src python -m harness.cli.main run-eval       # 18 runs, ~10 s
PYTHONPATH=src python -m harness.cli.main verify-results # chains + replay + metrics
```

`run-eval` writes `results/run_table.json` (normalized rows),
`results/metrics.json`, and `results/manifest.json`.
`verify-results` re-verifies every log's hash chain, replays every run
against a fresh engine (comparing state, attempt, and decision digests
record-by-record), and recomputes the metrics from the table, failing on
any divergence.

## Current results

See `results/README.md` for the frozen v1 numbers.

## Threats to validity (v1)

- The stub is scripted, so gate/repair behavior is measured against known
  faults, not against open-ended agent mistakes.
- Fixtures are small and synthetic; real codebases have larger blast radii.
- The human is scripted; override/deny dynamics are tested but unmeasured.
- Single machine, single run; no variance analysis yet.
