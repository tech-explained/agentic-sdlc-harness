"""Event envelope for the harness control plane.

Every lifecycle input is a typed, versioned envelope. The envelope carries
correlation IDs (run_id, event_id); consumers must ignore duplicate event IDs.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any

from harness.util import utcnow_rfc3339

SCHEMA_VERSION = "1.0"


class EventType(str, enum.Enum):
    RUN_REQUESTED = "RUN_REQUESTED"
    INTAKE_ACCEPTED = "INTAKE_ACCEPTED"
    INPUT_MISSING = "INPUT_MISSING"
    AGENT_SUCCEEDED = "AGENT_SUCCEEDED"
    AGENT_FAILED = "AGENT_FAILED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_DENIED = "HUMAN_DENIED"
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
    GATE_PASSED = "GATE_PASSED"
    ALL_GATES_PASSED = "ALL_GATES_PASSED"
    GATE_FAILED = "GATE_FAILED"
    REVIEW_PASSED = "REVIEW_PASSED"
    MERGE_RECORDED = "MERGE_RECORDED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    OUTCOME_RECORDED = "OUTCOME_RECORDED"
    COMMAND = "COMMAND"  # control-plane command envelope (bus transport only)


class SchemaError(Exception):
    """Raised when an event envelope fails schema validation."""


@dataclass(frozen=True)
class Event:
    schema_version: str
    event_id: str
    run_id: str
    type: EventType
    expected_state: str | None
    producer: str
    occurred_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "type": self.type.value,
            "expected_state": self.expected_state,
            "producer": self.producer,
            "occurred_at": self.occurred_at,
            "payload": self.payload,
        }


def new_event_id() -> str:
    return uuid.uuid4().hex


def new_run_id() -> str:
    return uuid.uuid4().hex


def make_event(
    run_id: str,
    type: EventType,
    expected_state: str | None,
    producer: str,
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> Event:
    return Event(
        schema_version=SCHEMA_VERSION,
        event_id=event_id or new_event_id(),
        run_id=run_id,
        type=type,
        expected_state=expected_state,
        producer=producer,
        occurred_at=utcnow_rfc3339(),
        payload=dict(payload or {}),
    )


def _valid_uuid_hex(value: Any) -> bool:
    try:
        uuid.UUID(hex=str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def validate_envelope(event: Event) -> None:
    """Fail closed on any malformed envelope. Raises SchemaError."""
    if not isinstance(event, Event):
        raise SchemaError(f"not an Event: {type(event).__name__}")
    if event.schema_version != SCHEMA_VERSION:
        raise SchemaError(f"unsupported schema_version: {event.schema_version!r}")
    if not isinstance(event.type, EventType):
        raise SchemaError(f"unknown event type: {event.type!r}")
    if not _valid_uuid_hex(event.event_id):
        raise SchemaError(f"invalid event_id: {event.event_id!r}")
    if not _valid_uuid_hex(event.run_id):
        raise SchemaError(f"invalid run_id: {event.run_id!r}")
    if not event.producer or not isinstance(event.producer, str):
        raise SchemaError("producer must be a non-empty string")
    if not isinstance(event.payload, dict):
        raise SchemaError("payload must be an object")
