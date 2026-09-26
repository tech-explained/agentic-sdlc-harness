"""Replay: integrity-verified reconstruction of measured runs.

Replay of a run's JSONL decision log:
1. Verify the SHA-256 hash chain and record schema (tamper evidence).
2. Re-validate every event envelope.
3. Re-apply every accepted event to a fresh engine and confirm the
   reproduced state_after, attempt, and decision digest match the record.
4. Confirm the terminal state matches the normalized run row.

Fails on the first integrity violation. Returns a per-run replay report.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harness.events.memory import InMemoryEventBus
from harness.events.model import EventType, SchemaError, make_event, validate_envelope
from harness.orchestrator.engine import Engine
from harness.orchestrator.log import (
    JsonlDecisionLog,
    MemoryDecisionStore,
    ReplayIntegrityError,
)


@dataclass(frozen=True)
class ReplayReport:
    run_id: str
    records: int
    accepted: int
    rejected: int
    hash_chain_ok: bool
    envelopes_valid: int
    decisions_reproduced: int
    decision_mismatches: tuple[str, ...]
    terminal_state: str | None
    row_matches: bool
    errors: tuple[str, ...]


def _reconstruct_event(record: dict):
    return make_event(
        run_id=record["run_id"],
        type=EventType(record["event_type"]),
        expected_state=record.get("state_before"),
        producer=record["producer"],
        payload=dict(record.get("event_payload") or {}),
        event_id=record["event_id"],
    )


def replay_run(log_path: Path, expected_row: dict | None = None) -> ReplayReport:
    log_path = Path(log_path)
    errors: list[str] = []
    mismatches: list[str] = []
    try:
        records = JsonlDecisionLog.verify(log_path)
        chain_ok = True
    except ReplayIntegrityError as exc:
        return ReplayReport(
            run_id=expected_row["run_id"] if expected_row else log_path.stem,
            records=0,
            accepted=0,
            rejected=0,
            hash_chain_ok=False,
            envelopes_valid=0,
            decisions_reproduced=0,
            decision_mismatches=(),
            terminal_state=None,
            row_matches=False,
            errors=(f"hash chain verification failed: {exc}",),
        )

    # Fresh engine: same policy/assembler as the measured run.
    replay_log = JsonlDecisionLog(log_path.parent / (log_path.stem + ".replay.jsonl"))
    try:
        store = MemoryDecisionStore(replay_log)
        engine = Engine(
            store=store,
            log=replay_log,
            bus=InMemoryEventBus(),
        )
        envelopes_valid = 0
        reproduced = 0
        n_accepted = 0
        n_rejected = 0
        terminal_state: str | None = None
        for record in records:
            if record.get("rejected"):
                n_rejected += 1
                continue
            n_accepted += 1
            try:
                event = _reconstruct_event(record)
                validate_envelope(event)
                envelopes_valid += 1
            except (SchemaError, ValueError, KeyError) as exc:
                errors.append(
                    f"seq {record.get('seq')}: envelope invalid: {exc}"
                )
                continue
            try:
                decision = engine.apply(event)
            except Exception as exc:  # noqa: BLE001 - replay must report, not raise
                errors.append(
                    f"seq {record.get('seq')}: re-application failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            terminal_state = record.get("state_after")
            ok = True
            for field, actual in (
                ("state_after", decision.state_after.value),
                ("attempt", decision.attempt),
                ("decision_digest", decision.decision_digest),
            ):
                if record.get(field) != actual:
                    ok = False
                    mismatches.append(
                        f"seq {record.get('seq')}: {field} mismatch "
                        f"(record={record.get(field)!r} replayed={actual!r})"
                    )
            if ok:
                reproduced += 1
    finally:
        replay_log.close()
        (log_path.parent / (log_path.stem + ".replay.jsonl")).unlink(missing_ok=True)

    row_matches = True
    if expected_row is not None:
        expected_terminal = expected_row.get("terminal_state")
        if expected_terminal and terminal_state != expected_terminal:
            row_matches = False
            errors.append(
                f"terminal state mismatch: replayed={terminal_state} "
                f"recorded={expected_terminal}"
            )

    return ReplayReport(
        run_id=expected_row["run_id"] if expected_row else log_path.stem,
        records=len(records),
        accepted=n_accepted,
        rejected=n_rejected,
        hash_chain_ok=chain_ok,
        envelopes_valid=envelopes_valid,
        decisions_reproduced=reproduced,
        decision_mismatches=tuple(mismatches),
        terminal_state=terminal_state,
        row_matches=row_matches and not mismatches,
        errors=tuple(errors),
    )


def verify_log(log_path: Path) -> dict:
    """Verify only the hash chain + envelope validity of a JSONL log."""
    try:
        records = JsonlDecisionLog.verify(log_path)
    except ReplayIntegrityError as exc:
        return {"ok": False, "records": 0, "errors": [str(exc)]}
    errors: list[str] = []
    envelopes_valid = 0
    for record in records:
        if record.get("rejected"):
            continue
        try:
            validate_envelope(_reconstruct_event(record))
            envelopes_valid += 1
        except (SchemaError, ValueError, KeyError) as exc:
            errors.append(f"seq {record.get('seq')}: envelope invalid: {exc}")
    return {
        "ok": not errors,
        "records": len(records),
        "envelopes_valid": envelopes_valid,
        "errors": errors,
    }
