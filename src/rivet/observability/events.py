from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from rivet.domain import Event

EventSubscriber = Callable[[Event], None | Awaitable[None]]


@runtime_checkable
class EventPublisher(Protocol):
    async def publish(self, events: Sequence[Event]) -> None:
        """Publish already-committed events without changing Runtime facts."""


class NullEventPublisher:
    async def publish(self, events: Sequence[Event]) -> None:
        return None


@dataclass(frozen=True, slots=True)
class PublishFailure:
    subscriber: str
    error_type: str
    message: str


class FanoutEventPublisher:
    """Best-effort publisher; observer failures are retained but never propagated."""

    def __init__(self, subscribers: Sequence[EventSubscriber] = ()) -> None:
        self._subscribers = list(subscribers)
        self._failures: list[PublishFailure] = []

    def subscribe(self, subscriber: EventSubscriber) -> None:
        self._subscribers.append(subscriber)

    @property
    def failures(self) -> tuple[PublishFailure, ...]:
        return tuple(self._failures)

    async def publish(self, events: Sequence[Event]) -> None:
        for event in events:
            for subscriber in tuple(self._subscribers):
                try:
                    result = subscriber(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as error:
                    self._failures.append(
                        PublishFailure(
                            subscriber=getattr(
                                subscriber,
                                "__qualname__",
                                type(subscriber).__name__,
                            ),
                            error_type=type(error).__name__,
                            message=str(error)[:1_000],
                        )
                    )


class EventSubscription:
    def __init__(
        self,
        *,
        max_queue_size: int,
        on_close: Callable[[EventSubscription], None],
    ) -> None:
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(max_queue_size)
        self._on_close = on_close
        self._closed = False
        self.dropped_events = 0

    def offer(self, event: Event) -> None:
        if self._closed:
            return
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self.dropped_events += 1
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(event)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._on_close(self)
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(None)

    def __aiter__(self) -> AsyncIterator[Event]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Event]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item


class EventStream(FanoutEventPublisher):
    """Fan-out publisher plus bounded async subscriptions for TUI/headless clients."""

    def __init__(
        self,
        subscribers: Sequence[EventSubscriber] = (),
        *,
        max_queue_size: int = 1_000,
    ) -> None:
        super().__init__(subscribers)
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")
        self._max_queue_size = max_queue_size
        self._streams: set[EventSubscription] = set()

    def open_subscription(self) -> EventSubscription:
        subscription = EventSubscription(
            max_queue_size=self._max_queue_size,
            on_close=self._streams.discard,
        )
        self._streams.add(subscription)
        return subscription

    async def publish(self, events: Sequence[Event]) -> None:
        await super().publish(events)
        for event in events:
            for subscription in tuple(self._streams):
                subscription.offer(event)
