"""Synthetic usage history (forecast input) and use-by / horizon data."""

import json
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.inventory import history
from backend.inventory.stock import StockStore

from .helpers import fresh_inventory

NY = ZoneInfo("America/New_York")


class UsageHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv = fresh_inventory()
        cls.doc = history.load_usage_history_file()
        cls.records = cls.doc["records"]

    def test_file_is_up_to_date(self):
        self.assertEqual(history.render(history.generate_history(self.inv)),
                         history.HISTORY_PATH.read_text(encoding="utf-8"),
                         "run `py -3 -m backend.inventory.history` to regenerate usage_history.json")

    def test_generation_is_deterministic(self):
        self.assertEqual(history.generate_history(self.inv), history.generate_history(self.inv))

    def test_labelled_synthetic(self):
        self.assertIn("SYNTHETIC", self.doc["_note"])

    def test_every_stocked_item_has_every_period(self):
        items = set(self.inv.stocked_items())
        self.assertEqual({r["ingredient_id"] for r in self.records}, items)
        for item in items:
            rows = [r for r in self.records if r["ingredient_id"] == item]
            self.assertEqual(len(rows), history.PERIODS, item)
            self.assertEqual(len({r["start"] for r in rows}), history.PERIODS, item)

    def test_record_format_matches_engine_contract(self):
        keys = {"ingredient_id", "unit", "start", "end", "daypart", "quantity", "source", "stockout", "complete"}
        for r in self.records:
            self.assertEqual(set(r), keys)
            self.assertIn(r["unit"], ("g", "ml", "each"))
            self.assertEqual(r["unit"], self.inv.unit_of(r["ingredient_id"]))
            self.assertEqual((r["daypart"], r["source"], r["stockout"], r["complete"]),
                             ("dinner", "measured", False, True))
            self.assertIsInstance(r["quantity"], int)
            self.assertGreaterEqual(r["quantity"], 0)

    def test_prior_same_weekday_dinners_before_demo_time(self):
        demo = history.DEMO_EVALUATION_TIME
        for r in self.records:
            start, end = datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])
            self.assertIsNotNone(start.utcoffset())
            local = start.astimezone(NY)
            self.assertEqual((local.hour, local.minute), (17, 0))
            self.assertEqual(local.weekday(), demo.astimezone(NY).weekday())
            self.assertEqual(end - start, timedelta(hours=3))
            self.assertLessEqual(end, demo)
            self.assertLessEqual(demo - end, timedelta(days=90))
        # DST: October dinners are -04:00, November ones -05:00.
        self.assertTrue(any(r["start"].endswith("-04:00") for r in self.records))

    def test_demo_story_tofu_scarce_mushrooms_surplus(self):
        def mean(item):
            rows = [r["quantity"] for r in self.records if r["ingredient_id"] == item]
            return sum(rows) / len(rows)
        tofu, mushrooms = mean("tofu"), mean("mushrooms")
        self.assertGreater(tofu, 0.8 * self.inv.stock.on_hand("tofu"))  # dinner needs most of the tofu
        self.assertLess(mushrooms, 0.15 * self.inv.stock.on_hand("mushrooms"))  # mushrooms are surplus
        self.assertGreater(mushrooms, 0)

    def test_inventory_loader(self):
        self.assertEqual(self.inv.usage_history(), self.records)


class UsableUntilTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()
        self.start = datetime(2026, 11, 7, 17, 0, tzinfo=NY)

    def test_every_stocked_item_has_an_aware_use_by(self):
        dates = self.inv.usable_until()
        self.assertEqual(set(dates), set(self.inv.stocked_items()))
        self.assertTrue(all(d.utcoffset() is not None for d in dates.values()))

    def test_demo_batches_last_through_dinner(self):
        dinner_end = self.start + timedelta(hours=3)
        self.assertTrue(all(d > dinner_end for d in self.inv.usable_until().values()))
        self.assertEqual(self.inv.horizon_end(self.inv.stocked_items(), self.start, dinner_end), dinner_end)

    def test_subset_and_unknown_items(self):
        self.assertEqual(set(self.inv.usable_until(["tofu", "nope"])), {"tofu"})

    def test_horizon_stops_at_use_by(self):
        end = self.inv.horizon_end(["mushrooms"], self.start, self.start + timedelta(days=5))
        self.assertEqual(end, datetime.fromisoformat("2026-11-08T22:00:00-05:00"))

    def test_horizon_stops_at_next_delivery(self):
        end = self.inv.horizon_end(["tofu"], self.start, self.start + timedelta(days=5))
        self.assertEqual(end, datetime.fromisoformat("2026-11-08T10:00:00-05:00"))
        later = datetime.fromisoformat("2026-11-08T11:00:00-05:00")
        self.assertIsNone(self.inv.stock.next_delivery("tofu", later))

    def test_horizon_requires_aware_times(self):
        with self.assertRaises(ValueError):
            self.inv.horizon_end(["tofu"], datetime(2026, 11, 7, 17), self.start)

    def test_naive_use_by_rejected(self):
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            StockStore({"tofu": 1}, usable_until={"tofu": "2026-11-08T22:00:00"})
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            StockStore({"tofu": 1}, deliveries=[{"item": "tofu", "qty": 1, "eta": "2026-11-08T10:00:00"}])

    def test_stock_json_documents_use_by(self):
        raw = json.loads((history.DATA_DIR / "stock.json").read_text(encoding="utf-8"))
        self.assertIn("usable_until", raw["_note"])
        self.assertEqual(set(raw["usable_until"]), set(raw["on_hand"]))


if __name__ == "__main__":
    unittest.main()
