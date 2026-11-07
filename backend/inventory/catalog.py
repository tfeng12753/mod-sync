"""Load and validate the restaurant catalog (ingredients, components, dishes)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Component, Dish, Extra, Ingredient, Substitution


class CatalogError(ValueError):
    """The catalog data is malformed or internally inconsistent."""


@dataclass
class Catalog:
    ingredients: dict[str, Ingredient]
    components: dict[str, Component]
    dishes: dict[str, Dish]
    allergy_protocol: tuple[str, ...] = ()

    def name_of(self, item_id: str) -> str:
        if item_id in self.components:
            return self.components[item_id].name
        if item_id in self.ingredients:
            return self.ingredients[item_id].name
        return item_id

    def unit_cost(self, stock_item: str) -> float:
        """Cost per unit of a stocked item (raw ingredient or premade component)."""
        if stock_item in self.components:
            return self.components[stock_item].cost_cents_per_unit
        if stock_item in self.ingredients:
            return self.ingredients[stock_item].cost_cents_per_unit
        return 0.0

    def components_with_main(self, ingredient_id: str) -> list[str]:
        return [c.id for c in self.components.values() if not c.is_premade and c.main == ingredient_id]


def load_catalog(path: str | Path) -> Catalog:
    with open(path, encoding="utf-8") as f:
        return parse_catalog(json.load(f))


def parse_catalog(raw: dict[str, Any]) -> Catalog:
    try:
        ingredients = {
            iid: Ingredient(
                id=iid,
                name=d["name"],
                unit=d["unit"],
                allergens=tuple(a.lower() for a in d.get("allergens", ())),
                diet=tuple(t.lower() for t in d.get("diet", ())),
                cost_cents_per_unit=float(d.get("cost_cents_per_unit", 0)),
            )
            for iid, d in raw["ingredients"].items()
        }
        components = {cid: _parse_component(cid, d) for cid, d in raw["components"].items()}
        dishes = {did: _parse_dish(did, d) for did, d in raw["dishes"].items()}
    except (KeyError, TypeError, ValueError) as e:
        raise CatalogError(f"malformed catalog: {e!r}") from e

    catalog = Catalog(ingredients, components, dishes, tuple(raw.get("allergy_protocol", ())))
    _validate(catalog)
    return catalog


def _parse_component(cid: str, d: dict[str, Any]) -> Component:
    prep = d["prep"]
    common = dict(
        id=cid,
        name=d["name"],
        prep=prep,
        cross_contact=tuple(d.get("cross_contact", ())),
        tags=tuple(d.get("tags", ())),
    )
    if prep == "assembly":
        return Component(**common, uses={k: int(v) for k, v in d["uses"].items()}, main=d["main"])
    if prep == "premade":
        return Component(
            **common,
            unit=d["unit"],
            portion=int(d["portion"]),
            contains=tuple(d["contains"]),
            cost_cents_per_unit=float(d.get("cost_cents_per_unit", 0)),
        )
    raise CatalogError(f"component {cid!r}: prep must be 'premade' or 'assembly', got {prep!r}")


def _parse_dish(did: str, d: dict[str, Any]) -> Dish:
    return Dish(
        id=did,
        name=d["name"],
        price_cents=int(d["price_cents"]),
        components={k: int(v) for k, v in d["components"].items()},
        extras={
            cid: Extra(component=cid, max=int(e["max"]), price_cents=int(e["price_cents"]))
            for cid, e in d.get("extras", {}).items()
        },
        substitutions=tuple(
            Substitution(
                replace=s["replace"],
                with_=s["with"],
                similarity=None if s.get("similarity") is None else int(s["similarity"]),
                price_delta_cents=int(s.get("price_delta_cents", 0)),
            )
            for s in d.get("substitutions", ())
        ),
    )


def _validate(cat: Catalog) -> None:
    errors: list[str] = []
    for c in cat.components.values():
        for iid in c.ingredient_ids():
            if iid not in cat.ingredients:
                errors.append(f"component {c.id!r} references unknown ingredient {iid!r}")
        if c.is_premade:
            if c.portion <= 0:
                errors.append(f"premade component {c.id!r} needs a positive portion")
        else:
            if c.main not in c.uses:
                errors.append(f"assembly component {c.id!r}: main {c.main!r} must be in uses")
            if any(q <= 0 for q in c.uses.values()):
                errors.append(f"assembly component {c.id!r}: quantities must be positive")
    for dish in cat.dishes.values():
        for cid, servings in dish.components.items():
            if cid not in cat.components:
                errors.append(f"dish {dish.id!r} references unknown component {cid!r}")
            if servings <= 0:
                errors.append(f"dish {dish.id!r}: servings of {cid!r} must be positive")
        for cid, extra in dish.extras.items():
            if cid not in cat.components:
                errors.append(f"dish {dish.id!r} extra references unknown component {cid!r}")
            if extra.max < 1:
                errors.append(f"dish {dish.id!r} extra {cid!r}: max must be >= 1")
        for s in dish.substitutions:
            for cid in (s.replace, s.with_):
                if cid not in cat.components:
                    errors.append(f"dish {dish.id!r} substitution references unknown component {cid!r}")
            if s.replace not in dish.components:
                errors.append(f"dish {dish.id!r} substitution replaces {s.replace!r}, which is not in the dish")
            if s.similarity is not None and not 1 <= s.similarity <= 5:
                errors.append(f"dish {dish.id!r} substitution {s.replace}->{s.with_}: similarity must be 1-5")
    if errors:
        raise CatalogError("invalid catalog:\n  " + "\n  ".join(errors))
