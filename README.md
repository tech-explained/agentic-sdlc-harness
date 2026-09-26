# agentic-sdlc-harness

A deterministic control layer for agentic software delivery — a research
prototype (v0.1.0). The harness wraps an AI coding agent in a verifiable
lifecycle: typed events, a single-authority state machine, layered
verification gates, bounded repair, and a hash-chained decision log that
supports exact replay.

## What it does

```
INTAKE → PLAN → (human) → IMPLEMENT → VERIFY → REVIEW → (human) → MERGE
                 ↑            |  lint → tests → contract
                 └─ REPAIR ←──┘  (max 3 attempts, then escalate)
```

- **Orchestrator**: pure transition function + engine. `Engine.apply(event)`
  is the only path to a state transition. Duplicate event IDs are
  idempotent; stale `expected_state`, wrong producers, and excess
  capabilities are rejected and recorded.
- **Events**: typed, versioned envelopes with correlation IDs.
- **Agents**: bounded adapters behind one interface. v1 ships a
  deterministic stub suite (the measured agent) and a subprocess-only CLI
  adapter for real coding assistants (integration-tested, not measured).
- **Gates** (policy order): static analysis + scope → test execution →
  contract/schema compatibility. Deterministic, fail-closed, digest-bound.
- **Repair**: at most 3 attempts per run; exhaustion escalates.
- **Evaluation**: 12 synthetic tasks, 6 single-fault scenarios, four
  metrics, and replay that re-verifies the hash chain and reproduces every
  decision from the log.

## Quickstart

Requires Python 3.11+ and pytest. No third-party runtime dependencies.

```bash
PYTHONPATH=src python -m pytest tests -q        # full test suite
PYTHONPATH=src python -m harness.cli.main validate-fixtures
PYTHONPATH=src python -m harness.cli.main demo --task ll-discount
PYTHONPATH=src python -m harness.cli.main run-eval      # measured run (~10 s)
PYTHONPATH=src python -m harness.cli.main verify-results
```

Or install it: `pip install -e .` then use the `harness` command.

## Layout

```
src/harness/
  orchestrator/   state machine, engine, policy, checkpoints, decision log
  events/         typed envelopes, in-memory bus
  agents/         adapter interface, deterministic stub, subprocess CLI adapter
  gates/          lint/scope, test execution, contract compatibility
  evals/          fault injectors, driver, metrics, replay
  cli/            validate-fixtures, demo, run-eval, replay, verify-results
fixtures/         12 synthetic tasks (seed/solution/manifest) + 6 fault manifests
tests/            unit + integration
docs/             architecture, evaluation, CLI adapter wiring
results/          metrics.json, run_table.json, manifest.json, README.md
```

## Documentation

- [Setup](docs/SETUP.md) — prerequisites, install, and troubleshooting.
- [Usage](docs/USAGE.md) — CLI reference, custom agents and gates, metrics, replay.
- [Architecture](docs/architecture.md), [Evaluation](docs/evaluation.md),
  [CLI adapter wiring](docs/cli-adapter.md) — design references.

## Measured results (v1)

18 runs, stub agent only: 12 clean + 6 single-fault. See `results/README.md`.

| metric | value |
|---|---|
| per-gate first-pass rate (lint / tests / contract) | 16/18 each |
| unaided all-gates rate | 12/18 |
| retries (mean / median / max / zero-retry share) | 0.33 / 0 / 1 / 0.67 |
| injected-fault catch rate | 6/6 (each by its expected gate) |
| human override rate | 0.0 (scripted auto-approve stood in for the human) |

## License

Apache-2.0. See `LICENSE`.
