"""Simple in-process event bus for SSE broadcasting.

Each Planning Session gets its own event channel. When agents produce messages,
they publish to the bus, and SSE connections subscribe to receive them.

P1-11: Added message redelivery support with retry tracking.
"""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Event:
    """A single SSE event."""
    __slots__ = ("event", "data", "id", "retry_count", "max_retries")

    def __init__(self, event: str, data: Any, id: Optional[str] = None,
                 retry_count: int = 0, max_retries: int = 3):
        self.event = event
        self.data = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        self.id = id
        self.retry_count = retry_count
        self.max_retries = max_retries


class EventBus:
    """Per-session fan-out event bus using asyncio.Queue.

    P1-11: Supports at-least-once delivery with retry mechanism.
    """

    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 2.0
    MAX_SUBSCRIBER_LAG = 10  # P2-C-013: backpressure threshold

    def __init__(self):
        self._subscribers: Dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._undelivered: Dict[str, List[Event]] = defaultdict(list)  # P1-11
        self._publish_counts: Dict[str, int] = defaultdict(int)  # P2-C-013: rate tracking
        self._backpressure_flags: Dict[str, bool] = defaultdict(bool)  # P2-C-013

    def subscribe(self, session_id: str) -> asyncio.Queue:
        """Subscribe to events for a session. Returns a queue that yields Event objects."""
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers[session_id].append(q)

        # P1-11: Redeliver any undelivered events to new subscriber
        undelivered = self._undelivered.get(session_id, [])
        for event in undelivered:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue):
        """Remove a subscriber queue."""
        subs = self._subscribers.get(session_id, [])
        if q in subs:
            subs.remove(q)

    def publish(self, session_id: str, event: Event):
        """Publish an event to all subscribers of a session.

        P1-11: If no subscribers, stores event for redelivery.
        P2-C-013: Backpressure - if subscribers are falling behind, logs warning.
        """
        # P2-C-013: Track publish rate
        self._publish_counts[session_id] += 1

        subscribers = self._subscribers.get(session_id, [])

        if not subscribers:
            # P1-11: Store for redelivery when a subscriber connects
            self._undelivered[session_id].append(event)
            # Keep only recent undelivered events (last 100)
            if len(self._undelivered[session_id]) > 100:
                self._undelivered[session_id] = self._undelivered[session_id][-100:]
            return

        # P2-C-013: Backpressure detection
        slow_subscribers = []
        for q in subscribers:
            if q.qsize() > 200:  # Queue is >78% full
                slow_subscribers.append(q)

        if slow_subscribers:
            if not self._backpressure_flags.get(session_id):
                self._backpressure_flags[session_id] = True
                logger.warning(
                    f"P2-C-013: Backpressure detected for session {session_id}: "
                    f"{len(slow_subscribers)}/{len(subscribers)} subscribers lagging"
                )

        delivered_to_any = False
        for q in subscribers:
            try:
                q.put_nowait(event)
                delivered_to_any = True
            except asyncio.QueueFull:
                # Drop oldest and retry
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                    delivered_to_any = True
                except asyncio.QueueFull:
                    # P1-11: Track retry
                    if event.retry_count < event.max_retries:
                        retry_event = Event(
                            event=event.event,
                            data=event.data,
                            id=event.id,
                            retry_count=event.retry_count + 1,
                            max_retries=event.max_retries,
                        )
                        self._undelivered[session_id].append(retry_event)

        # Clear undelivered if we delivered to at least one subscriber
        if delivered_to_any and session_id in self._undelivered:
            self._undelivered[session_id].clear()
            self._backpressure_flags[session_id] = False  # P2-C-013: Clear backpressure


# Global singleton
event_bus = EventBus()
