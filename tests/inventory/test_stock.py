import threading
import unittest

from backend.inventory import StockStore

from .helpers import FakeClock, fresh_inventory


class StockStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = StockStore({"tofu": 300, "rice": 1000}, safety_buffer={"tofu": 50}, clock=self.clock)

    def test_hold_reduces_available(self):
        self.assertTrue(self.store.hold("o1", {"tofu": 100}).ok)
        self.assertEqual(self.store.available("tofu"), 200)
        self.assertEqual(self.store.on_hand("tofu"), 300)

    def test_hold_fails_when_short_and_keeps_existing_hold(self):
        self.store.hold("o1", {"tofu": 100})
        r = self.store.hold("o1", {"tofu": 400})
        self.assertFalse(r.ok)
        self.assertEqual(r.shortfalls["tofu"], {"need": 400, "available": 300})
        self.assertEqual(self.store.available("tofu"), 200)  # old hold still there

    def test_rehold_replaces_hold(self):
        self.store.hold("o1", {"tofu": 100})
        self.assertTrue(self.store.hold("o1", {"tofu": 250}).ok)
        self.assertEqual(self.store.available("tofu"), 50)

    def test_confirm_uses_hold_and_deducts_once(self):
        self.store.hold("o1", {"tofu": 100, "rice": 200})
        r = self.store.confirm("o1")
        self.assertEqual(r.code, "CONFIRMED")
        self.assertEqual(self.store.on_hand("tofu"), 200)
        again = self.store.confirm("o1")
        self.assertTrue(again.ok and again.already_confirmed)
        self.assertEqual(self.store.on_hand("tofu"), 200)
        self.assertEqual(len(self.store.usage_log()), 2)

    def test_confirm_without_hold_needs_requirements(self):
        self.assertEqual(self.store.confirm("o1").code, "NO_REQUIREMENTS")
        self.assertTrue(self.store.confirm("o1", {"rice": 100}).ok)

    def test_confirm_rechecks_against_other_holds(self):
        self.store.hold("o1", {"tofu": 250})
        r = self.store.confirm("o2", {"tofu": 100})
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "INSUFFICIENT_STOCK")

    def test_hold_expires_and_is_reported(self):
        self.store.hold("o1", {"tofu": 100}, ttl_seconds=600)
        self.clock.advance(601)
        self.assertEqual(self.store.available("tofu"), 300)
        self.assertEqual(self.store.pop_expired(), ["o1"])
        self.assertEqual(self.store.pop_expired(), [])

    def test_expired_hold_confirm_rechecks(self):
        self.store.hold("o1", {"tofu": 100}, ttl_seconds=10)
        self.clock.advance(11)
        self.assertEqual(self.store.confirm("o1").code, "NO_REQUIREMENTS")
        self.assertTrue(self.store.confirm("o1", {"tofu": 100}).ok)

    def test_release(self):
        self.store.hold("o1", {"tofu": 100})
        self.assertTrue(self.store.release("o1"))
        self.assertFalse(self.store.release("o1"))
        self.assertEqual(self.store.available("tofu"), 300)

    def test_cannot_hold_confirmed_order(self):
        self.store.confirm("o1", {"rice": 10})
        self.assertEqual(self.store.hold("o1", {"rice": 10}).code, "ALREADY_CONFIRMED")

    def test_adjust_cannot_go_negative(self):
        with self.assertRaises(ValueError):
            self.store.adjust("tofu", -301, "waste")
        self.assertEqual(self.store.adjust("tofu", 100, "delivery"), 400)
        self.assertEqual(self.store.adjustments()[-1]["reason"], "delivery")

    def test_negative_requirement_rejected(self):
        with self.assertRaises(ValueError):
            self.store.hold("o1", {"tofu": -5})

    def test_concurrent_confirms_never_oversell(self):
        store = StockStore({"tofu": 1000})
        results = []

        def worker(i):
            results.append(store.confirm(f"o{i}", {"tofu": 150}).ok)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 6)
        self.assertEqual(store.on_hand("tofu"), 100)


class StaffReviewFlowTests(unittest.TestCase):
    """The allergy flow end to end at the inventory level."""

    def setUp(self):
        self.clock = FakeClock()
        self.inv = fresh_inventory(self.clock)
        self.ops = [{"op": "remove", "ingredient": "peanuts"},
                    {"op": "substitute", "ingredient": "peanut_sauce", "with": "sesame_dressing"}]

    def test_approve(self):
        r = self.inv.check("satay_noodles", self.ops, exclude_allergens=["peanut"])
        self.assertTrue(r.ok and r.requires_staff_review)
        self.assertTrue(self.inv.hold("order-1", r.requirements).ok)
        self.assertEqual(self.inv.confirm("order-1").code, "CONFIRMED")
        self.assertEqual(self.inv.stock.on_hand("sesame_dressing"), 760)

    def test_staff_edit_rechecks_then_replaces_hold(self):
        r = self.inv.check("satay_noodles", self.ops, exclude_allergens=["peanut"])
        self.inv.hold("order-1", r.requirements)
        edited = [self.ops[0], {"op": "substitute", "ingredient": "peanut_sauce", "with": "garlic_sauce"}]
        r2 = self.inv.check("satay_noodles", edited, exclude_allergens=["peanut"])
        self.assertTrue(r2.ok)
        self.assertTrue(self.inv.hold("order-1", r2.requirements).ok)
        self.assertEqual(self.inv.available("sesame_dressing"), 800)  # old hold replaced
        self.assertTrue(self.inv.confirm("order-1").ok)

    def test_reject_releases(self):
        r = self.inv.check("satay_noodles", self.ops, exclude_allergens=["peanut"])
        self.inv.hold("order-1", r.requirements)
        self.assertTrue(self.inv.release("order-1"))
        self.assertEqual(self.inv.available("sesame_dressing"), 800)

    def test_timeout_means_reject(self):
        r = self.inv.check("satay_noodles", self.ops, exclude_allergens=["peanut"])
        self.inv.hold("order-1", r.requirements, ttl_seconds=600)
        self.clock.advance(601)
        self.assertEqual(self.inv.expired_holds(), ["order-1"])
        self.assertFalse(self.inv.confirm("order-1").ok)


if __name__ == "__main__":
    unittest.main()
