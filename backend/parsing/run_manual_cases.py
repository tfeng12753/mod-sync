"""Runs ten REAL model prompts. Requires an OpenRouter key and may incur API charges."""
import json
from pathlib import Path

from backend.parsing.parser import CartItem, ParseRequest, parse_modifications

INGREDIENTS = [
    "bun", "patty", "cheese", "onion", "sauce", "avocado",
    "mayo", "chicken", "lettuce", "fries", "salt",
]

MENU_ITEMS = {
    "burger-1": CartItem(
        cart_item_id="burger-1", name="Cheeseburger",
        ingredients={"bun": 1, "patty": 1, "cheese": 1, "onion": 1, "sauce": 1},
    ),
    "burger-2": CartItem(
        cart_item_id="burger-2", name="Cheeseburger",
        ingredients={"bun": 1, "patty": 1, "cheese": 1, "onion": 1, "sauce": 1},
    ),
    "chicken-1": CartItem(
        cart_item_id="chicken-1", name="Chicken sandwich",
        ingredients={"bun": 1, "chicken": 1, "mayo": 1, "sauce": 1},
    ),
}


def main():
    cases = json.loads(Path(__file__).with_name("manual_cases.json").read_text())
    for index, case in enumerate(cases, 1):
        request = ParseRequest(
            instructions=case["instructions"],
            cart=[MENU_ITEMS[id_] for id_ in case["cart_item_ids"]],
            allowed_ingredient_ids=INGREDIENTS,
        )
        try:
            output = parse_modifications(request)
            status = "PASS" if output.status == case["expected_status"] else "CHECK"
            print(f"\n{index}. {status} — {case['instructions']}")
            print(f"Expected: {case['expected_status']} / {case['expected_summary']}")
            print("Actual:", output.model_dump_json(indent=2))
        except Exception as exc:
            print(f"\n{index}. ERROR — {case['instructions']}")
            print(type(exc).__name__, str(exc)[:200])


if __name__ == "__main__":
    main()
