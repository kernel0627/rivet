from __future__ import annotations

import unittest

from rivet.domain import Event, EventActor
from rivet.observability import EventStream, FanoutEventPublisher


def event(sequence: int) -> Event:
    return Event.create(
        session_id="session_1",
        run_id="run_1",
        sequence=sequence,
        event_type="test.event",
        actor=EventActor.RUNTIME,
    )


class EventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_observer_failure_does_not_abort_other_subscribers(self) -> None:
        observed: list[int] = []

        async def broken(_event):
            raise RuntimeError("observer failed")

        async def healthy(item):
            observed.append(item.sequence)

        publisher = FanoutEventPublisher([broken, healthy])
        await publisher.publish((event(1),))
        self.assertEqual(observed, [1])
        self.assertEqual(publisher.failures[0].error_type, "RuntimeError")

    async def test_bounded_subscription_drops_oldest_event(self) -> None:
        stream = EventStream(max_queue_size=2)
        subscription = stream.open_subscription()
        await stream.publish((event(1), event(2), event(3)))
        iterator = subscription.__aiter__()
        self.assertEqual((await anext(iterator)).sequence, 2)
        self.assertEqual((await anext(iterator)).sequence, 3)
        self.assertEqual(subscription.dropped_events, 1)
        await subscription.close()
        with self.assertRaises(StopAsyncIteration):
            await anext(iterator)


if __name__ == "__main__":
    unittest.main()
