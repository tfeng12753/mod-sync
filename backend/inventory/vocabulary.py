"""What the parser is allowed to output, plus name lookup and the public menu.

The parser (Person 2) should put ``parser_vocabulary()`` in its prompt and only emit IDs from it.
``lookup()`` is a deterministic helper for mapping a customer's word to catalog IDs (useful for
validating or repairing LLM output). No LLM calls here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .catalog import Catalog, CatalogError
from .models import OPS

UNKNOWN_PREFIX = "unknown:"

OP_DOCS = {
    "remove": "Leave an ingredient or component out. {op, ingredient}",
    "add": "Extra portion of something the dish offers as an extra. {op, ingredient, qty (default 1, = extra portions)}",
    "substitute": "Swap one thing for another. {op, ingredient, with}; with = null if the customer didn't say what.",
    "on_side": "Serve a component on the side. {op, ingredient}",
    "less": "Light / less of something (half portion). {op, ingredient}",
}


def _norm(text: str) -> str:
    t = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return re.sub(r"^(the|some|any|a|an) ", "", t)


class Vocabulary:
    def __init__(self, catalog: Catalog, aliases: dict[str, Any] | None = None) -> None:
        self.cat = catalog
        aliases = aliases or {}
        self.dish_aliases: dict[str, list[str]] = {k: list(v) for k, v in aliases.get("dishes", {}).items()}
        self.item_aliases: dict[str, list[str]] = {k: list(v) for k, v in aliases.get("items", {}).items()}
        self.allergen_aliases: dict[str, str] = {_norm(k): v.lower() for k, v in aliases.get("allergens", {}).items()}
        self.diet_aliases: dict[str, str] = {_norm(k): v.lower() for k, v in aliases.get("diets", {}).items()}
        self._validate()
        self._items = self._index({**{i: [] for i in catalog.ingredients}, **{c: [] for c in catalog.components}},
                                  self.item_aliases)
        self._dishes = self._index({d: [] for d in catalog.dishes}, self.dish_aliases)

    # ----- lookup ------------------------------------------------------------------------------

    def lookup(self, text: str, dish_id: str | None = None) -> list[str]:
        """Catalog IDs a customer's word could mean. Narrowed to the dish (its parts, extras and
        approved swaps) when given. If a component and an ingredient inside it both match, the
        component wins ("no tahini" -> the tahini sauce). Empty list = not on our list."""
        matches = self._match(self._items, text)
        if dish_id in self.cat.dishes:
            # Prefer what's in the dish now, then its extras/approved swaps, then anything.
            for scope in (self.dish_scope(dish_id, base_only=True), self.dish_scope(dish_id)):
                if matches & scope:
                    matches &= scope
                    break
        for cid in [m for m in matches if m in self.cat.components]:
            comp = self.cat.components[cid]
            matches -= set(comp.ingredient_ids()) & matches
        return sorted(matches)

    def lookup_dish(self, text: str) -> list[str]:
        return sorted(self._match(self._dishes, text))

    def allergen(self, text: str) -> str:
        key = _norm(text)
        return self.allergen_aliases.get(key, key)

    def diet(self, text: str) -> str:
        key = _norm(text)
        return self.diet_aliases.get(key, key)

    def dish_scope(self, dish_id: str, base_only: bool = False) -> set[str]:
        """Every ID that makes sense to mention for this dish (``base_only``: just what's in it now)."""
        dish = self.cat.dishes[dish_id]
        comps = set(dish.components)
        if not base_only:
            comps |= set(dish.extras) | {s.with_ for s in dish.substitutions}
        out = set(comps)
        for cid in comps:
            out |= set(self.cat.components[cid].ingredient_ids())
        return out

    # ----- exports -----------------------------------------------------------------------------

    def parser_vocabulary(self) -> dict[str, Any]:
        cat = self.cat
        return {
            "_note": "Generated from backend/data/catalog.json + aliases.json (SYNTHETIC demo data). "
                     "The parser must only emit IDs listed here.",
            "output_format": {
                "dish": "<dish id>",
                "qty": "<int, default 1>",
                "original_text": "<customer's words, verbatim>",
                "ops": [{"op": "|".join(OPS), "ingredient": "<id>", "qty": "<add only>", "with": "<substitute only: id or null>"}],
                "flags": {"level": "allergy | diet | null", "allergens": ["<allergen>"], "diets": ["<diet>"]},
            },
            "rules": [
                "Use only IDs from this file. Prefer the dish's own items (dishes[].items).",
                f"If the customer mentions something not listed, use \"{UNKNOWN_PREFIX}<their word>\" as the ID; inventory will ask for clarification.",
                "If a request can't be expressed as an op (e.g. 'make it spicy'), emit it as an add of \"unknown:<word>\".",
                "Any mention of an allergy, intolerance or celiac sets flags.level = 'allergy' and lists the allergen, even if worded casually.",
                "Dietary needs (vegan, vegetarian, etc.) set flags.level = 'diet'. Allergy takes precedence if both.",
                "'Double X' / 'extra X' = add with qty 1 (one extra portion). '3 extra X' = qty 3.",
                "Keep the customer's exact words in original_text.",
            ],
            "ops": OP_DOCS,
            "allergens": sorted({a for i in cat.ingredients.values() for a in i.allergens}),
            "allergen_aliases": dict(sorted(self.allergen_aliases.items())),
            "diets": sorted({d for i in cat.ingredients.values() for d in i.diet}),
            "diet_aliases": dict(sorted(self.diet_aliases.items())),
            "dishes": [self._dish_vocab(d) for d in cat.dishes],
            "all_items": [self._item_vocab(i) for i in sorted({*cat.ingredients, *cat.components})],
        }

    def menu(self) -> list[dict[str, Any]]:
        """Customer-facing menu for GET /menu and the UI."""
        cat = self.cat
        out = []
        for dish in cat.dishes.values():
            out.append({
                "id": dish.id,
                "name": dish.name,
                "price_cents": dish.price_cents,
                "components": [{"id": c, "name": cat.name_of(c), "servings": n} for c, n in dish.components.items()],
                "extras": [{"id": c, "name": cat.name_of(c), "max": e.max, "price_cents": e.price_cents}
                           for c, e in dish.extras.items()],
                "swaps": [{"replace": s.replace, "replace_name": cat.name_of(s.replace), "with": s.with_,
                           "with_name": cat.name_of(s.with_), "price_delta_cents": s.price_delta_cents}
                          for s in dish.substitutions],
                "allergens": sorted({a for c in dish.components for i in cat.components[c].ingredient_ids()
                                     for a in cat.ingredients[i].allergens}),
            })
        return out

    # ----- internals ---------------------------------------------------------------------------

    def _dish_vocab(self, dish_id: str) -> dict[str, Any]:
        cat = self.cat
        dish = cat.dishes[dish_id]
        return {
            "id": dish.id,
            "name": dish.name,
            "aliases": self.dish_aliases.get(dish.id, []),
            "items": [self._item_vocab(i, dish_id) for i in sorted(self.dish_scope(dish_id))],
            "extras": [{"id": c, "max": e.max} for c, e in dish.extras.items()],
            "swaps": [{"replace": s.replace, "with": s.with_} for s in dish.substitutions],
        }

    def _item_vocab(self, item_id: str, dish_id: str | None = None) -> dict[str, Any]:
        cat = self.cat
        entry: dict[str, Any] = {"id": item_id, "name": cat.name_of(item_id), "aliases": self.item_aliases.get(item_id, [])}
        if item_id in cat.components:
            comp = cat.components[item_id]
            entry["kind"] = "component"
            entry["prep"] = comp.prep
            if comp.is_premade:
                entry["note"] = "Made ahead: its ingredients can't be removed individually."
        else:
            entry["kind"] = "ingredient"
            if dish_id:
                inside = [c for c in cat.dishes[dish_id].components
                          if item_id in cat.components[c].ingredient_ids()]
                if inside:
                    entry["in"] = inside
        return entry

    def _index(self, ids: dict[str, list], aliases: dict[str, list[str]]) -> dict[str, set[str]]:
        index: dict[str, set[str]] = {}
        for item_id in ids:
            phrases = {item_id.replace("_", " "), self.cat.name_of(item_id) if item_id not in self.cat.dishes
                       else self.cat.dishes[item_id].name, *aliases.get(item_id, [])}
            for p in phrases:
                index.setdefault(_norm(p), set()).add(item_id)
        return index

    @staticmethod
    def _match(index: dict[str, set[str]], text: str) -> set[str]:
        key = _norm(text)
        if key in index:
            return set(index[key])
        if key.endswith("s") and key[:-1] in index:
            return set(index[key[:-1]])
        return set()

    def _validate(self) -> None:
        errors = [f"aliases.json: unknown dish {d!r}" for d in self.dish_aliases if d not in self.cat.dishes]
        errors += [f"aliases.json: unknown item {i!r}" for i in self.item_aliases
                   if i not in self.cat.ingredients and i not in self.cat.components]
        if errors:
            raise CatalogError("\n  ".join(errors))


def load_aliases(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)
