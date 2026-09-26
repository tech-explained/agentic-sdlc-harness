"""Append-only, hash-chained decision log + in-memory decision store.

The log is the durable evidence layer: every accepted decision (and every
rejection) is appended with a sequence number and a hash chain
(prev_hash -> record_hash). Replay verifies the chain before reconstructing.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from harness.orchestrator.model import Decision, Run
from harness.util import canonical_json, sha256_hex

GENESIS_PREV_HASH = "sha256:" + "0" * 64


class ReplayIntegrityError(Exception):
    """Raised when the decision log fails chain or schema verification."""


class JsonlDecisionLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._last_hash = GENESIS_PREV_HASH
        self._fh = open(self.path, "a", encoding="utf-8")
        # Resume chain if the file already has records (not used by the demo,
        # kept for the crash-recovery shape).
        if self.path.stat().st_size:
            for rec in self._iter_records():
                self._seq = rec["seq"]
                self._last_hash = rec["record_hash"]

    def _iter_records(self) -> Iterator[dict]:
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def append(self, record: dict) -> dict:
        self._seq += 1
        body = dict(record)
        body["seq"] = self._seq
        body["prev_hash"] = self._last_hash
        body["record_hash"] = "sha256:" + sha256_hex(canonical_json(body))
        self._last_hash = body["record_hash"]
        self._fh.write(canonical_json(body) + "\n")
        self._fh.flush()
        return body

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    @staticmethod
    def verify(path: Path) -> list[dict]:
        """Verify the hash chain; return records. Raises ReplayIntegrityError."""
        records: list[dict] = []
        prev = GENESIS_PREV_HASH
        expected_seq = 0
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError as exc:
                    raise ReplayIntegrityError(f"line {lineno}: not JSON: {exc}")
                expected_seq += 1
                if rec.get("seq") != expected_seq:
                    raise ReplayIntegrityError(
                        f"line {lineno}: sequence gap "
                        f"(expected {expected_seq}, got {rec.get('seq')})"
                    )
                if rec.get("prev_hash") != prev:
                    raise ReplayIntegrityError(f"line {lineno}: hash chain broken")
                body = {k: v for k, v in rec.items() if k != "record_hash"}
                actual = "sha256:" + sha256_hex(canonical_json(body))
                if rec.get("record_hash") != actual:
                    raise ReplayIntegrityError(f"line {lineno}: record hash mismatch")
                for key in ("run_id", "event_id", "event_type"):
                    if key not in rec:
                        raise ReplayIntegrityError(f"line {lineno}: missing {key}")
                prev = rec["record_hash"]
                records.append(rec)
        return records


class MemoryDecisionStore:
    """In-memory run/decision store backed by the JSONL evidence log."""

    def __init__(self, log: JsonlDecisionLog) -> None:
        self._log = log
        self._runs: dict[str, Run] = {}
        self._decisions: dict[str, Decision] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global = threading.Lock()

    @contextmanager
    def lock(self, run_id: str):
        with self._global:
            lock = self._locks.setdefault(run_id, threading.Lock())
        with lock:
            yield

    def create_run(self, run: Run) -> None:
        self._runs[run.run_id] = run

    def load(self, run_id: str) -> Run:
        try:
            return self._runs[run_id]
        except KeyError:
            from harness.orchestrator.model import UnknownRun

            raise UnknownRun(f"unknown run_id: {run_id}")

    def save(self, run: Run) -> None:
        self._runs[run.run_id] = run

    def has_event(self, event_id: str) -> bool:
        return event_id in self._decisions

    def get_decision(self, event_id: str) -> Decision:
        return self._decisions[event_id]

    def remember(self, event_id: str, decision: Decision) -> None:
        self._decisions[event_id] = decision
