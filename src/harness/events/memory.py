"""In-memory event backbone (reference implementation of the EventBus seam).

Transports typed envelopes between the orchestrator and workers. It is NOT
durable truth: the append-only decision log owns evidence. A later broker
adapter must preserve at-least-once delivery, deduplication keys, correlation
IDs, and the no-lifecycle-authority invariant.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Callable

from harness.events.model import Event, EventType

Handler = Callable[[Event], None]


class InMemoryEventBus:
    def __init__(self) -> None:
        self._queue: deque[Event] = deque()
        self._handlers: dict[EventType | None, list[Handler]] = defaultdict(list)
        self._seen_ids: set[str] = set()

    def publish(self, event: Event) -> None:
        # Deduplication key: consumers must ignore duplicate event IDs.
        if event.event_id in self._seen_ids:
            return
        self._seen_ids.add(event.event_id)
        self._queue.append(event)
        for handler in self._handlers.get(event.type, []):
            handler(event)
        for handler in self._handlers.get(None, []):
            handler(event)

    def subscribe(self, types: set[EventType] | None, handler: Handler) -> None:
        if types is None:
            self._handlers[None].append(handler)
        else:
            for t in types:
                self._handlers[t].append(handler)

    def drain(self) -> list[Event]:
        items = list(self._queue)
        self._queue.clear()
        return items

    def __len__(self) -> int:
        return len(self._queue)
