"""Exact menu commands using the catalog's prebuilt alias index. No fuzzy inference or model call."""

from __future__ import annotations

import re

from .parser import Modification, ParseResult, dietary_review_categories, keyword_restrictions, validate_result

_NUMBER = r"\d+|one|two|three|four|five"
_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_ADD = re.compile(rf"(?:(?P<before>{_NUMBER}) )?(?:extra|add|more) (?:(?P<after>{_NUMBER}) )?(?P<item>.+)")
_SWAP = re.compile(r"(?:swap|switch|replace|substitute|sub) (?:out )?(.+?) (?:for|with) (.+)")
_INSTEAD = re.compile(r"(.+?) instead of (.+)")
_REMOVE = re.compile(r"(?:no|none of|without|hold|remove|skip|leave off) (.+)")
_SIDE = re.compile(r"(?:(?:put|serve) )?(.+) on the side")
_LESS = re.compile(r"(?:light|less|easy on) (.+)")
_BARE_SWAP = re.compile(r"(?:swap|switch|replace|substitute|sub) (.+)")


def parse_menu_phrase(inv, request, dish_id):
    # Allergy/diet wording, dialogue, corrections, and unfamiliar syntax stay with OpenRouter.
    if request.clarifications or dietary_review_categories([request.instructions]):
        return None
    if keyword_restrictions([request.instructions], request.vocabulary):
        return None
    text = " ".join(request.instructions.lower().split()).rstrip(".!? ")
    text = re.sub(r"^(?:can|could) you ", "", text)
    clauses = re.split(r",\s*(?:and )?| and ", text)
    changes, seen = [], set()

    def resolve(phrase):
        # lookup() must consume the whole phrase; substring matches can hide another intent.
        if not re.fullmatch(r"[a-z0-9 _-]+", phrase):
            return None
        hits = inv.lookup(phrase, dish_id)
        return hits[0] if len(hits) == 1 else None

    for clause in clauses:
        clause = clause.strip()
        clause = clause.removeprefix("please ").removesuffix(" please")
        quantity, replacement = 0, None
        if match := _SWAP.fullmatch(clause):
            item, replacement = resolve(match[1]), resolve(match[2])
            operation = "substitute"
            if replacement is None:
                return None
        elif match := _INSTEAD.fullmatch(clause):
            item, replacement = resolve(match[2]), resolve(match[1])
            operation = "substitute"
            if replacement is None:
                return None
        elif match := _REMOVE.fullmatch(clause):
            item, operation = resolve(match[1]), "remove"
        elif match := _SIDE.fullmatch(clause):
            item, operation = resolve(match[1]), "on_side"
        elif match := _LESS.fullmatch(clause):
            item, operation = resolve(match[1]), "less"
        elif match := _ADD.fullmatch(clause):
            if match["before"] and match["after"]:
                return None
            number = match["before"] or match["after"] or "one"
            quantity = int(number) if number.isdigit() else _NUMBERS[number]
            item, operation = resolve(match["item"]), "add"
            if quantity < 1:
                return None
        elif clause.startswith("double "):
            item, operation, quantity = resolve(clause[7:]), "add", 1
        elif match := _BARE_SWAP.fullmatch(clause):
            item, operation = resolve(match[1]), "substitute"
        else:
            return None
        if item is None or item in seen or item == replacement:
            return None
        if operation in ("less", "on_side") and request.cart[0].ingredients.get(item, 0) <= 0:
            return None
        seen.add(item)
        changes.append(Modification(cart_item_id=dish_id, ingredient_id=item, operation=operation,
                                    quantity=quantity, with_ingredient_id=replacement))

    if not changes:
        return None
    return validate_result(request, ParseResult(status="ready", changes=changes, questions=[], staff_notes=[]))
