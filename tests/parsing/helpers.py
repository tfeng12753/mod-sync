"""Fake OpenRouter client + helpers for parser tests. No network, no API key."""

import json
from pathlib import Path

CASES_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "parser_cases.json"


def load_cases():
    with open(CASES_FILE, encoding="utf-8") as f:
        return json.load(f)["cases"]


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeClient:
    """Returns ``content`` (dict -> JSON string, or raw string) as the model message."""

    def __init__(self, content):
        self.content = content
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        text = self.content if isinstance(self.content, str) else _json_dumps(self.content)
        return FakeResponse({"choices": [{"message": {"content": text}}]})

    @property
    def payload(self):
        """The user message the parser sent to the model, decoded."""
        return json_loads(self.calls[-1]["json"]["messages"][1]["content"])


def _json_dumps(obj):
    return json.dumps(obj)


def json_loads(text):
    return json.loads(text)


def change(cart, ingredient, operation, quantity=0, with_=None):
    return {"cart_item_id": cart, "ingredient_id": ingredient, "operation": operation,
            "quantity": quantity, "with_ingredient_id": with_}


def model_output(status="ready", changes=(), questions=(), staff_notes=(), restrictions=(), unknown_items=()):
    return {"status": status, "changes": list(changes), "questions": list(questions),
            "staff_notes": list(staff_notes), "restrictions": list(restrictions),
            "unknown_items": list(unknown_items)}


def model_output_for_case(case):
    """The model response equivalent to a parser case's expected inventory input."""
    cart = case["dish"]
    changes, unknown = [], []
    for op in case["expected"]["ops"]:
        target = op["ingredient"]
        if target.startswith("unknown:"):
            unknown.append({"cart_item_id": cart, "word": target[len("unknown:"):], "operation": op["op"]})
        elif op["op"] == "add":
            changes.append(change(cart, target, "add", op.get("qty", 1)))
        elif op["op"] == "substitute":
            changes.append(change(cart, target, "substitute", 0, op.get("with")))
        else:
            changes.append(change(cart, target, op["op"]))
    flags = case["expected"]["flags"] or {}
    restrictions = ([{"kind": "allergy", "value": a} for a in flags.get("allergens", [])]
                    + [{"kind": "diet", "value": d} for d in flags.get("diets", [])])
    review = bool(unknown or restrictions)
    return model_output(
        status="needs_staff_review" if review else "ready",
        changes=changes,
        staff_notes=["Needs staff review (allergy/diet or unknown item)."] if review else [],
        restrictions=restrictions,
        unknown_items=unknown,
    )
