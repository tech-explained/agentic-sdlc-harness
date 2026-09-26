# Architecture

## Control-plane shape

The harness is a **control plane** around untrusted workers. Agents are
bounded: they receive an isolated workspace, a phase-scoped capability set,
and a deterministic context bundle; they return structured results. They
never advance lifecycle state. Every advance is a typed event through the
single authority, `Engine.apply()`.

```
                ┌─────────────────────────────────────────────┐
                │                  Engine                       │
                │  validate → idempotency → expected-state →   │
                │  producer → permission → pure transition →   │
                │  append decision (before dispatch) → save →  │
                │  publish commands                           │
                └─────────────────────────────────────────────┘
         events ▲                                        │ commands
                │                                        ▼
   driver ─────┴─── interprets commands only ────► agents, gates,
        (agents, gates, checkpoints, review)        checkpoints
```

## State machine

States: `INTAKE PLAN IMPLEMENT VERIFY REVIEW` (work),
`WAIT_HUMAN REPAIR MERGE_READY` (control),
`COMPLETED REJECTED ESCALATED CANCELLED` (terminal).

The transition table is a pure function
(`harness.orchestrator.model.transition`): `(Run, Event) -> Transition`.
Notable rules:

- `IMPLEMENT/REPAIR + AGENT_SUCCEEDED` requires an `artifact_digest` and
  arms `pending_gates = ("lint", "test-execution", "contract-compatibility")`.
- `VERIFY + GATE_PASSED` pops the head of `pending_gates` and rejects
  out-of-order gates; `ALL_GATES_PASSED` requires empty `pending_gates`.
- Gate failure routes to `REPAIR` (attempt+1) up to `MAX_REPAIRS = 3`,
  then `ESCALATED`.
- `HUMAN_OVERRIDE` requires a rationale and an exception ID; it is recorded
  in the evidence trail but never converts a failed gate into a pass.
- `MERGE_RECORDED` requires the merge artifact digest to equal the last
  verified artifact digest (digest binding).
- Terminal states are closed to state-changing inputs, except the synthetic
  `OUTCOME_RECORDED` feedback after `COMPLETED`.

## Permissions

Effective permissions = `policy[state] ∩ task-declared needs`. An event that
declares capabilities outside the effective set triggers the
`PERMISSION_DENIED` path: the run escalates, the offending event is recorded
as rejected, and redelivery is idempotent. `network` is never granted in v1.

## Context injection

`ContextAssembler` builds a namespaced bundle
(baseline policy + phase templates + task fields), canonicalizes it as JSON,
and hashes it. The digest is stored on the run at creation.

## Evidence

Every accepted decision and every rejection is appended to a JSONL decision
log with a sequence number and a SHA-256 hash chain (`prev_hash →
record_hash`). Records carry the full event payload, so replay can re-apply
transitions exactly with no out-of-band state. `JsonlDecisionLog.verify`
checks sequence continuity, chain integrity, and record hashes.

## Failure learning

Gate findings carry a normalized `signature` (hash of rule + path +
message). The evaluation aggregates signature counts so recurring failure
modes are visible across runs.
