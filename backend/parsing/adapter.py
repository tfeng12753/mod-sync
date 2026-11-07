"""Parser <-> inventory adapter: the one call the API makes for parsing.

    from backend.inventory import load_inventory
    from backend.parsing.adapter import parse_request
    parsed = parse_request(inv, "chicken_bowl", 1, "swap the chicken for tofu")
    evaluation = inv.evaluate_request(parsed.parser_output)

What it does:
1. Builds the parser request from the inventory catalog: one ``CartItem`` (``cart_item_id`` = the
   dish id) whose recipe lists every dish component with its servings AND every ingredient inside
   those components (portions = servings of the components that contain it), so the model can
   target "garlic_sauce" as well as "garlic" or "avocado". ``allowed_ingredient_ids`` = every
   catalog ingredient and component (so "add tomato" on a bowl or "falafel instead of chicken" can
   be reported faithfully; inventory then says it isn't offered/approved). The dish's vocabulary
   slice (names, nicknames, kinds, made-ahead notes, extras, swaps) plus canonical allergens/diets
   and their aliases go into the prompt payload.
2. Runs the parser: ``mode="openrouter"`` (LLM; needs ``OPENROUTER_API_KEY`` unless a ``client``
   is passed) or ``mode="offline"`` (deterministic rules, see ``offline.py``). Default: openrouter
   when a client is passed or the key is set, else offline. With a key and no explicit mode/client,
   exact menu commands use the alias index; other requests use the validated model parse cache.
3. Translates parser changes to inventory ops (CLAUDE.md "Contracts"); ``set N`` is compared with
   the recipe amount: 0 -> remove, above -> add the difference, below -> less, equal -> nothing.
   Unknown items become ``"unknown:<word>"`` ops so inventory returns ``needs_clarification``.
4. Flags: level "allergy" if any allergy restriction (or an allergy was mentioned but no allergen
   could be named), else "diet" if any diet restriction (or a diet/religious requirement was
   mentioned that the catalog doesn't track), else None.

Raises ``ParserValidationError`` for unsafe model output (API -> 502), ``KeyError`` for an
unknown dish, ``ValueError`` for a bad quantity.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from .parser import (
    CartItem,
    Clarification,
    ParseRequest,
    ParseResult,
    ParserValidationError,
    parse_modifications,
    validate_result,
)

UNKNOWN_PREFIX = "unknown:"

__all__ = ["ParsedRequest", "ParserValidationError", "parse_request", "build_parse_request", "to_parser_output"]


@dataclass
class ParsedRequest:
    status: str               # "ready" | "needs_clarification" | "needs_staff_review"
    parser_output: dict       # ready to pass to Inventory.evaluate_request()
    questions: list[str] = field(default_factory=list)
    staff_notes: list[str] = field(default_factory=list)
    restrictions: list[dict] = field(default_factory=list)  # [{"kind": "allergy"|"diet", "value": str}]
    source: str = "offline"   # "menu" | "openrouter" | "openrouter_cache" | "offline"


# ----- catalog -> parser request -------------------------------------------------------------------


def dish_recipe(inv, dish_id: str) -> dict[str, int]:
    """Component servings + ingredients inside them (summed over containing components)."""
    cat = inv.catalog
    dish = cat.dishes[dish_id]
    recipe: dict[str, int] = {}
    for cid, servings in dish.components.items():
        for ing in cat.components[cid].ingredient_ids():
            if ing not in dish.components:
                recipe[ing] = recipe.get(ing, 0) + int(servings)
    for cid, servings in dish.components.items():  # a component id wins over a same-named ingredient
        recipe[cid] = int(servings)
    return recipe


def dish_vocabulary(inv, dish_id: str, qty: int = 1) -> dict[str, Any]:
    vocab = inv.parser_vocabulary()
    dish = next(d for d in vocab["dishes"] if d["id"] == dish_id)
    return {
        "dish": dish,
        "quantity_ordered": qty,
        "quantity_note": "Quantity comes from the order; changes apply to each serving.",
        "allergens": vocab["allergens"],
        "allergen_aliases": vocab["allergen_aliases"],
        "diets": vocab["diets"],
        "diet_aliases": vocab["diet_aliases"],
    }


def _clarifications(items: Iterable[Any]) -> list[Clarification]:
    out = []
    for c in items or ():
        if isinstance(c, Clarification):
            out.append(c)
        elif isinstance(c, dict):
            out.append(Clarification(question=str(c.get("question", "")), answer=str(c.get("answer", ""))))
        elif hasattr(c, "question") and hasattr(c, "answer"):
            out.append(Clarification(question=str(c.question), answer=str(c.answer)))
        else:
            q, a = c
            out.append(Clarification(question=str(q), answer=str(a)))
    return out


def build_parse_request(inv, dish_id: str, qty: int, text: str, clarifications=()) -> ParseRequest:
    if dish_id not in inv.catalog.dishes:
        raise KeyError(f"unknown dish {dish_id!r}")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
        raise ValueError(f"qty must be a whole number >= 1, got {qty!r}")
    cat = inv.catalog
    allowed = sorted({*cat.ingredients, *cat.components})
    return ParseRequest(
        instructions=text,
        cart=[CartItem(cart_item_id=dish_id, name=cat.dishes[dish_id].name, ingredients=dish_recipe(inv, dish_id))],
        allowed_ingredient_ids=allowed,
        clarifications=_clarifications(clarifications),
        vocabulary=dish_vocabulary(inv, dish_id, qty),
    )


# ----- parser result -> inventory input ------------------------------------------------------------


def _unknown_id(word: str) -> str:
    return UNKNOWN_PREFIX + "_".join(word.split())


def _part_aliases(inv, dish_id: str) -> dict[str, str]:
    """Ingredient id -> dish part whose aliases name that ingredient ("tahini" -> tahini_sauce).

    A customer saying "no tahini" means the sauce; the bare ingredient is made ahead inside it."""
    cat = inv.catalog
    dish = cat.dishes.get(dish_id)
    vocab = next((d for d in inv.parser_vocabulary()["dishes"] if d["id"] == dish_id), None)
    if dish is None or vocab is None:
        return {}
    parts = {i["id"]: {a.lower() for a in i.get("aliases", [])} for i in vocab["items"]
             if i.get("kind") == "component" and i["id"] in dish.components}
    out = {}
    for item in vocab["items"]:
        if item.get("kind") != "ingredient" or item["id"] in dish.components:
            continue
        names = {item["id"].replace("_", " "), item["name"].lower()}
        hits = [cid for cid, aliases in parts.items() if names & aliases]
        if len(hits) == 1:
            out[item["id"]] = hits[0]
    return out


def to_parser_output(inv, request: ParseRequest, result: ParseResult, dish_id: str, qty: int) -> dict[str, Any]:
    recipe = request.cart[0].ingredients
    part_for = _part_aliases(inv, dish_id)
    ops: list[dict[str, Any]] = []

    def emit(op):
        if op not in ops:
            ops.append(op)

    for ch in result.changes:
        iid = part_for.get(ch.ingredient_id, ch.ingredient_id)
        if ch.operation == "add":
            emit({"op": "add", "ingredient": iid, "qty": ch.quantity})
        elif ch.operation == "substitute":
            emit({"op": "substitute", "ingredient": iid, "with": ch.with_ingredient_id})
        elif ch.operation == "set":
            base = recipe.get(iid, 0)
            if ch.quantity == 0:
                emit({"op": "remove", "ingredient": iid})
            elif ch.quantity > base:
                emit({"op": "add", "ingredient": iid, "qty": ch.quantity - base})
            elif ch.quantity < base:
                emit({"op": "less", "ingredient": iid})
        else:  # remove, on_side, less
            emit({"op": ch.operation, "ingredient": iid})
    for u in result.unknown_items:
        op: dict[str, Any] = {"op": u.operation, "ingredient": _unknown_id(u.word)}
        if u.operation == "add":
            op["qty"] = 1
        elif u.operation == "substitute":
            op["with"] = None
        emit(op)

    allergens = [r.value for r in result.restrictions if r.kind == "allergy"]
    diets = [r.value for r in result.restrictions if r.kind == "diet"]
    if allergens or "allergy" in result.safety_flags:
        level = "allergy"
    elif diets or "diet" in result.safety_flags:
        level = "diet"
    else:
        level = None
    return {
        "dish": dish_id,
        "qty": qty,
        "original_text": request.instructions,
        "ops": ops,
        "flags": {"level": level, "allergens": allergens, "diets": diets},
    }


# ----- entry point ---------------------------------------------------------------------------------


def _resolve_mode(mode: str | None, client) -> str:
    if mode is None:
        return "openrouter" if client is not None or os.getenv("OPENROUTER_API_KEY") else "offline"
    if mode not in ("openrouter", "offline"):
        raise ValueError(f"mode must be 'openrouter', 'offline' or None, got {mode!r}")
    return mode


def parse_request(inv, dish_id: str, qty: int, text: str, clarifications=(), client=None,
                  mode: str | None = None) -> ParsedRequest:
    """Customer text for one dish -> ``ParsedRequest`` whose ``parser_output`` goes straight into
    ``inv.evaluate_request()``. See the module docstring for the representation."""
    source = _resolve_mode(mode, client)
    text = text or ""
    if not text.strip() and not clarifications:
        if dish_id not in inv.catalog.dishes:
            raise KeyError(f"unknown dish {dish_id!r}")
        return ParsedRequest(status="ready", parser_output={
            "dish": dish_id, "qty": qty, "original_text": text, "ops": [],
            "flags": {"level": None, "allergens": [], "diets": []}}, source=source)

    request = build_parse_request(inv, dish_id, qty, text if text.strip() else "(no text)", clarifications)
    if source == "openrouter":
        if mode is None and client is None:
            from .cache import parse_with_cache
            from .menu_phrases import parse_menu_phrase
            fast_path = os.getenv("PARSER_FAST_PATH_ENABLED", "1").lower() not in ("0", "false", "off")
            result = parse_menu_phrase(inv, request, dish_id) if fast_path else None
            if result is not None:
                source = "menu"
            else:
                result, hit = parse_with_cache(request)
                if hit:
                    source = "openrouter_cache"
        else:
            # Explicit modes and injected clients keep model benchmarks/tests reproducible.
            result = parse_modifications(request, client=client)
    else:
        from .offline import offline_parse
        result = validate_result(request, offline_parse(inv, request, dish_id))

    output = to_parser_output(inv, request, result, dish_id, qty)
    output["original_text"] = text
    return ParsedRequest(
        status=result.status,
        parser_output=output,
        questions=list(result.questions),
        staff_notes=list(result.staff_notes),
        restrictions=[r.model_dump() for r in result.restrictions],
        source=source,
    )
