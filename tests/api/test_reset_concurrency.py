"""Reset cannot be undone by an older request finishing its parser call."""
import asyncio
from threading import Event
from unittest import TestCase

from backend.api.service import ApiError, OrderService
from backend.inventory import load_inventory
from tests.api.test_api import fake_parse, fake_route


class ResetConcurrencyTests(TestCase):
    def test_inflight_order_cannot_repopulate_cleared_backend(self):
        started, release = Event(), Event()
        def slow_parse(*args):
            started.set()
            if not release.wait(3):
                raise RuntimeError("test parser release timed out")
            return fake_parse(*args)
        service = OrderService(inv=load_inventory(), parse_fn=slow_parse, route_fn=fake_route)
        async def scenario():
            pending = asyncio.create_task(service.evaluate("chicken_bowl", 1, ""))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                service.reset()
            finally:
                release.set()
            with self.assertRaises(ApiError) as caught:
                await pending
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(service.list_orders(), [])
            self.assertTrue(all(i["on_hand"] == i["available"] == i["initial_on_hand"] for i in service.stock()))
        asyncio.run(scenario())
