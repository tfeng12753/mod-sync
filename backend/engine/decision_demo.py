"""Synthetic end-to-end routing demo; no real inventory mutations or AI calls."""
import argparse
import asyncio
import json
from pathlib import Path

from . import InventoryGateway, ModificationRequest, decide_modification


class FixtureInventory:
    def __init__(self, stock):
        self.stock = stock

    def requirements(self, dish_id, changes):
        # Demo-only opaque changes format. The actual inventory team owns its format.
        return dict(changes["recipe"])

    def check(self, dish_id, changes, qty=1):
        requirements = {i: q * qty for i, q in self.requirements(dish_id, changes).items()}
        reasons = [f"{i}: insufficient stock" for i, q in requirements.items() if q > self.available(i)]
        return {"status": "unavailable" if reasons else "ok", "reasons": reasons,
                "requirements": requirements}

    def available(self, ingredient_id):
        return self.stock.get(ingredient_id, 0)

    def confirm(self, order):
        raise RuntimeError("The demo does not confirm orders or modify inventory")


async def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Synthetic substitution/omission/extra routing")
    parser.add_argument("--input", type=Path, default=root / "fixtures/modification_request.json")
    parser.add_argument("--inventory", type=Path, default=root / "fixtures/decision_inventory.json")
    args = parser.parse_args()
    request = ModificationRequest.model_validate_json(args.input.read_text())
    inventory = FixtureInventory(json.loads(args.inventory.read_text())["stock"])
    gateway = InventoryGateway(inventory, success_statuses={"ok"}, blocked_statuses={"unavailable"})
    result = await decide_modification(request, gateway)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
