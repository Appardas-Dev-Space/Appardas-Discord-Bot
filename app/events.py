"""In-process pub/sub used by the dashboard's SSE stream."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled browser must never grow memory or block a toggle.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:
                    log.debug("dropping event for a full subscriber queue")
            except Exception:
                log.exception("event publish failed")

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()
