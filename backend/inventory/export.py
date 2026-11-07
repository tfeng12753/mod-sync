"""Write the shared files other team members build against.

    py -3 -m backend.inventory.export

- fixtures/parser_vocabulary.json  what the parser may output (IDs, names, nicknames, rules)
- fixtures/menu.json               customer-facing menu (same data as GET /menu)

Re-run after changing backend/data/catalog.json or aliases.json; a test fails if they're stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from .service import load_inventory

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def exports() -> dict[str, object]:
    inv = load_inventory()
    return {"parser_vocabulary.json": inv.parser_vocabulary(), "menu.json": inv.menu()}


def render(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, data in exports().items():
        (FIXTURES / name).write_text(render(data), encoding="utf-8")
        print(f"wrote fixtures/{name}")


if __name__ == "__main__":
    main()
