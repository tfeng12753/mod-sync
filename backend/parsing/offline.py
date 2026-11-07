"""Deterministic, rule-based fallback parser (no LLM, no network).

Lets the demo and UI work without an OpenRouter key. It is intentionally simple: split the text
into clauses, match a few phrase patterns, and map words to catalog IDs with ``inv.lookup()``.
Its result goes through the same ``validate_result()`` as the LLM's, so the keyword allergy/diet
backstop and all safety checks apply unchanged.

Patterns (per clause):
- "X instead of Y", "swap/switch/replace Y for/with X"   -> substitute Y with X
- "no Y, (put) something/anything else (instead)"         -> substitute Y with null
- "X on the side"                                         -> on_side
- "light / less / easy (on) X"                            -> less
- "no / without / hold / remove / skip / leave off X"     -> remove
- "extra / double / add / more X", "3 extra X"            -> add (qty)
- a clause starting with "actually" replaces earlier changes to the same item
Words not on our list become unknown items (-> ``unknown:<word>``); a word matching several items
becomes a clarification question. Allergy/diet restrictions come from the keyword backstop.
"""

from __future__ import annotations

import re

from .parser import (
    DEFAULT_ALLERGEN_TERMS,
    DEFAULT_DIET_TERMS,
    Modification,
    ParseRequest,
    ParseResult,
    UnknownItem,
    dietary_review_categories,
    keyword_restrictions,
)

_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_STOP = {
    "the", "a", "an", "some", "any", "my", "of", "it", "to", "on", "in", "for", "with", "me", "please",
    "pls", "can", "could", "you", "i", "get", "have", "do", "make", "put", "is", "this", "that", "be",
    "both", "all", "at", "them", "just", "bit", "little", "lot", "thanks", "thank", "would", "like",
    "want", "if", "possible", "also", "really", "very", "much", "more", "extra", "instead",
}
# Words that are restrictions, not items ("no meat", "without gluten"): the backstop handles them.
_RESTRICTION_WORDS = {w for t in [*DEFAULT_ALLERGEN_TERMS, *DEFAULT_DIET_TERMS] for w in t.split()} | {"meat", "pork"}

_SUB_SWAP = re.compile(r"\b(?:swap|switch|replace|sub|change)\s+(?:out\s+)?(?P<old>.+?)\s+(?:for|with|to)\s+(?P<new>.+)")
_SUB_INSTEAD = re.compile(r"(?P<new>.+?)\s+(?:instead\s+of|in\s+place\s+of|rather\s+than)\s+(?P<old>.+)")
_SUB_BARE = re.compile(r"\b(?:swap|switch|replace|sub|substitute|change)\s+(?:out\s+)?(?P<old>.+)")  # "swap the chicken"
_SOMETHING_ELSE = re.compile(r"\b(?:something|anything)\s+else\b|\binstead\b|\bsubstitute\b|\breplace\s+it\b")
_ON_SIDE = re.compile(r"(?P<item>.+?)\s+on\s+the\s+side\b")
_LESS = re.compile(r"\b(?:go\s+)?(?:light|lite|less|easy|half)\b(?:\s+(?:on|of))?\s+(?P<item>.+)")
_REMOVE = re.compile(r"\b(?:no|without|hold|remove|skip|minus|leave\s+(?:off|out)|take\s+(?:off|out))\b\s+(?P<item>.+)")
_ADD = re.compile(r"\b(?:(?P<n>\d+|one|two|three|four|five)\s+)?(?:extra|double|add|more|additional)\b\s+(?P<item>.+)")
_SPLIT = re.compile(r"[,.;!?\n]+|\b(?:and|but|also|plus|then)\b")


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]+", " ", text.lower()).replace("'", "").split()


class _Resolver:
    def __init__(self, inv, dish_id: str):
        self.inv, self.dish_id = inv, dish_id

    def _lookup(self, gram: str) -> list[str]:
        ids = self.inv.lookup(gram, self.dish_id)
        if ids:
            return ids
        # inv.lookup() drops IDs that are both a component and the ingredient inside it
        # (e.g. "tomato", "lettuce"); fall back to an exact ID match.
        cat = self.inv.catalog
        both = set(cat.ingredients) & set(cat.components)
        for item_id in sorted(both):
            phrases = {item_id.replace("_", " "), cat.name_of(item_id).lower(),
                       *(a.lower() for a in self.inv.vocab.item_aliases.get(item_id, []))}
            if gram in phrases or (gram.endswith("s") and gram[:-1] in phrases):
                return [item_id]
        return []

    def resolve(self, phrase: str):
        """-> ("id", id) | ("ambiguous", [ids], word) | ("unknown", word) | ("none",)."""
        words = _words(phrase)
        for n in range(min(len(words), 4), 0, -1):
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i:i + n])
                if n == 1 and gram in _STOP:
                    continue
                ids = self._lookup(gram)
                if len(ids) == 1:
                    return ("id", ids[0])
                if ids:
                    return ("ambiguous", ids, gram)
        content = [w for w in words if w not in _STOP]
        if not content:
            return ("none",)
        if all(w in _RESTRICTION_WORDS for w in content):
            return ("none",)
        return ("unknown", content[-1])


def offline_parse(inv, request: ParseRequest, dish_id: str) -> ParseResult:
    """Rule-based ``ParseResult`` (not yet validated; the adapter runs ``validate_result``)."""
    cart_id = request.cart[0].cart_item_id
    recipe = request.cart[0].ingredients
    res = _Resolver(inv, dish_id)
    texts = [request.instructions, *(c.answer for c in request.clarifications)]

    entries: list[dict] = []   # {"id", "op", "qty", "with"} or {"unknown", "op"}
    questions: list[str] = []
    conflicts: set[str] = set()

    def name(iid):
        return inv.catalog.name_of(iid)

    def push(entry, correction):
        key = entry.get("id") or entry.get("unknown")
        same = [e for e in entries if (e.get("id") or e.get("unknown")) == key]
        if same and correction:
            for e in same:
                entries.remove(e)
        elif same:
            if all(e["op"] == entry["op"] for e in same):
                return
            conflicts.add(key)
        entries.append(entry)

    def item(phrase, op, correction, qty=1):
        r = res.resolve(phrase)
        if r[0] == "id":
            iid = r[1]
            if op in ("on_side", "less") and recipe.get(iid, 0) <= 0:
                questions.append(f"{name(iid)} isn't part of this dish; what would you like changed?")
                return None
            entry = {"id": iid, "op": op, "qty": qty}
            push(entry, correction)
            return entry
        if r[0] == "ambiguous":
            questions.append(f"Which do you mean by '{r[2]}': " + " or ".join(name(i) for i in r[1]) + "?")
        elif r[0] == "unknown":
            push({"unknown": r[1], "op": op}, correction)
        return None

    for text in texts:
        for raw in _SPLIT.split(text.lower()):
            clause = " ".join(_words(raw))
            if not clause:
                continue
            correction = clause.startswith("actually") or " actually " in f" {clause} "
            clause = re.sub(r"\bactually\b", "", clause).strip()
            if m := (_SUB_SWAP.search(clause) or _SUB_INSTEAD.search(clause)):
                old = res.resolve(m.group("old"))
                new = res.resolve(m.group("new"))
                if old[0] == "id":
                    with_ = new[1] if new[0] == "id" and new[1] != old[1] else None
                    push({"id": old[1], "op": "substitute", "with": with_}, correction)
                    if new[0] == "unknown":
                        push({"unknown": new[1], "op": "add"}, correction)
                    elif new[0] == "ambiguous":
                        questions.append(f"Which do you mean by '{new[2]}': " + " or ".join(name(i) for i in new[1]) + "?")
                else:
                    item(m.group("old"), "substitute", correction)
                continue
            if (m := _SUB_BARE.search(clause)) and res.resolve(m.group("old"))[0] == "id":
                # "swap the chicken" with no replacement named -> substitute with null (inventory offers options)
                item(m.group("old"), "substitute", correction)
                continue
            if _SOMETHING_ELSE.search(clause) and res.resolve(clause)[0] in ("none", "unknown"):
                removed = [e for e in entries if e.get("id") and e["op"] == "remove"]
                if removed:
                    removed[-1]["op"] = "substitute"
                    removed[-1]["with"] = None
                    continue
            if m := _ON_SIDE.search(clause):
                item(m.group("item"), "on_side", correction)
            elif m := _LESS.search(clause):
                item(m.group("item"), "less", correction)
            elif m := _REMOVE.search(clause):
                item(m.group("item"), "remove", correction)
            elif m := _ADD.search(clause):
                n = m.group("n")
                qty = int(n) if n and n.isdigit() else _NUMBERS.get(n or "", 1)
                item(m.group("item"), "add", correction, max(qty, 1))

    for key in sorted(conflicts):
        label = name(key) if key in inv.catalog.ingredients or key in inv.catalog.components else key
        questions.append(f"You asked for different things for {label}; which one did you mean?")

    has_safety = bool(dietary_review_categories(texts) or keyword_restrictions(texts, request.vocabulary))
    if not entries and not questions and not has_safety:
        questions.append("Sorry, we couldn't work out the change you'd like. Could you rephrase it "
                         "(for example 'no avocado' or 'swap the chicken for tofu')?")

    if questions:
        return ParseResult(status="needs_clarification", changes=[], questions=list(dict.fromkeys(questions)),
                           staff_notes=[], restrictions=[], unknown_items=[])

    changes, unknown = [], []
    for e in entries:
        if "unknown" in e:
            unknown.append(UnknownItem(cart_item_id=cart_id, word=e["unknown"], operation=e["op"]))
        else:
            changes.append(Modification(
                cart_item_id=cart_id, ingredient_id=e["id"], operation=e["op"],
                quantity=e["qty"] if e["op"] == "add" else 0,
                with_ingredient_id=e.get("with") if e["op"] == "substitute" else None,
            ))
    return ParseResult(status="ready", changes=changes, questions=[], staff_notes=[],
                       restrictions=[], unknown_items=unknown)
