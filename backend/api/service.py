"""Orchestration: text -> parser -> inventory.evaluate_request -> engine routing -> order + stock hold.

The parser and engine steps are injected (``parse_fn``, ``route_fn``) so tests can run offline.
Defaults are the real adapters: ``backend.parsing.adapter.parse_request`` and
``backend.integration.engine_adapter.route_options``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable

from backend.inventory import Inventory, load_inventory

from .store import Order, OrderStore, utcnow

logger = logging.getLogger(__name__)

HOLD_SECONDS = 600
MAX_CLARIFICATION_REPLIES = 3
UNAVAILABLE_MESSAGE = "We cannot accommodate this request with the available menu and inventory options."


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _default_parse(*args, **kwargs):
    from backend.parsing.adapter import parse_request
    return parse_request(*args, **kwargs)


async def _default_route(*args, **kwargs):
    from backend.integration.engine_adapter import route_options
    return await route_options(*args, **kwargs)


ParseFn = Callable[..., Any]
RouteFn = Callable[..., Awaitable[Any]]


class OrderService:
    def __init__(self, inv: Inventory | None = None, parse_fn: ParseFn | None = None,
                 route_fn: RouteFn | None = None, inventory_loader: Callable[[], Inventory] = load_inventory) -> None:
        self._loader = inventory_loader
        self.inv = inv or inventory_loader()
        self._initial = {i: self.inv.stock.on_hand(i) for i in self.inv.stock.items()}
        self.parse_fn = parse_fn or _default_parse
        self.route_fn = route_fn or _default_route
        self.orders = OrderStore()

    # ----- evaluate ----------------------------------------------------------------------------

    async def evaluate(self, dish_id: str, qty: int, text: str, clarifications: list[dict] | None = None,
                       channel: str | None = None, customer_name: str | None = None) -> dict[str, Any]:
        self.sweep()
        inv = self.inv
        if dish_id not in inv.catalog.dishes:
            raise ApiError(404, f"Unknown dish {dish_id!r}.")
        if qty < 1:
            raise ApiError(400, "qty must be >= 1.")
        dish = {"id": dish_id, "name": inv.catalog.dishes[dish_id].name}

        try:
            parsed = await asyncio.to_thread(self.parse_fn, inv, dish_id, qty, text, list(clarifications or []))
        except ApiError:
            raise
        except Exception as exc:  # ParserValidationError, provider errors: never treated as approved
            logger.warning("parser failed: %s", type(exc).__name__)
            raise ApiError(502, "We couldn't safely read that request; please rephrase or ask staff.") from exc

        base = {
            "order_id": None, "dish": dish, "qty": qty, "original_text": text,
            "parsed": {"ops": list(parsed.parser_output.get("ops", [])), "restrictions": list(parsed.restrictions),
                       "source": getattr(parsed, "source", None)},
            "options": [], "recommended_option_id": None, "unavailable_options": [], "warnings": [],
            "clarifications": list(clarifications or []),
            "clarification_limit": MAX_CLARIFICATION_REPLIES, "clarification_exhausted": False,
        }

        if parsed.status == "needs_clarification" or parsed.questions:
            base["review"] = {"required": parsed.status == "needs_staff_review" or bool(parsed.restrictions),
                              "reasons": [], "staff_notes": list(parsed.staff_notes), "evidence": {}}
            return self._final(base, "needs_clarification", questions=list(parsed.questions),
                               message="We need a little more detail about your request.")

        evaluation = inv.evaluate_request(parsed.parser_output)
        review_required = evaluation.requires_staff_review or parsed.status == "needs_staff_review"
        review_reasons = list(evaluation.review_reasons)
        if parsed.status == "needs_staff_review" and not review_reasons:
            review_reasons = ["parser flagged this request for staff review"]
        staff_notes = list(parsed.staff_notes)
        # "No X" when the dish has no X is not a change: drop it from what the customer sees.
        absent = {r.item for r in evaluation.check.reasons if r.code == "NOT_IN_DISH" and r.status == "info"}
        base["parsed"]["ops"] = [_as_dish_part(inv, dish_id, op) for op in base["parsed"]["ops"]
                                 if op.get("ingredient") not in absent]
        base["as_requested"] = {
            "feasible": evaluation.check.ok,
            "reasons": [r.message for r in evaluation.check.reasons
                        if not (r.code == "NOT_IN_DISH" and r.status == "info")],
            "kitchen_instructions": list(evaluation.check.kitchen_instructions),
            "price_change_cents": evaluation.check.price_delta_cents,
        }
        base["review"] = {"required": review_required, "reasons": review_reasons, "staff_notes": staff_notes,
                          "evidence": evaluation.check.evidence}

        if evaluation.status == "needs_clarification":
            questions = list(parsed.questions) + [r.message for r in evaluation.reasons or evaluation.check.reasons
                                                  if r.status == "needs_clarification"]
            return self._final(base, "needs_clarification", questions=questions,
                               message="We need a little more detail about your request.")
        if evaluation.status == "unavailable":
            reasons = [r.message for r in evaluation.reasons]
            return self._final(base, "unavailable", message=_unavailable_message(reasons),
                               unavailable=[{"label": "As requested", "reasons": reasons}])

        # Never bypass the combined-score cutoff when forecast routing fails.
        try:
            routing = await self.route_fn(inv, evaluation)
        except Exception as exc:
            logger.exception("engine routing failed")
            base["warnings"].append(f"Forecast routing unavailable ({type(exc).__name__}).")
            return self._final(base, "unavailable", message="We can't check the available options right now. Please try again or ask staff.")

        requirements_by_choice: dict[str, dict[str, int]] = {}
        ops_by_choice: dict[str, list[dict[str, Any]]] = {}
        if evaluation.check.ok:
            requirements_by_choice["as_requested"] = dict(evaluation.check.requirements)
            ops_by_choice["as_requested"] = [dict(op) for op in parsed.parser_output.get("ops", [])]
        options, unavailable = self._options(evaluation, routing, review_required, requirements_by_choice,
                                             ops_by_choice)
        # Extras are listed on their own so the customer can keep or drop each one; every option's
        # price is split into its own change plus the extras it carries.
        extras = _extras(inv, dish_id, qty, parsed.parser_output.get("ops", []))
        extras_cents = sum(e["price_cents"] for e in extras)
        base["extras"] = extras
        for choice in [base["as_requested"], *options]:
            price = choice.get("price_change_cents")
            choice["extras_cents"] = extras_cents if extras else 0
            choice["own_price_change_cents"] = price - extras_cents if price is not None and extras else price
        base["options"] = options
        base["unavailable_options"] = unavailable
        if routing is not None:
            base["review"]["staff_notes"] = list(dict.fromkeys(staff_notes + list(routing.staff_review_notes)))
            base["forecast"] = getattr(routing, "forecast_summary", {})

        as_requested_route = None
        if routing is not None:
            as_requested_route = next((c.route for c in routing.choices if c.option_index is None), None)

        if evaluation.check.ok:
            needs_review = review_required or as_requested_route == "staff"
            status = "ready"
            selected = None
        elif options:
            selected = options[0]["option_id"]
            needs_review = review_required or options[0]["route"] == "staff"
            status = "needs_customer_approval"
        else:
            msg = routing.customer_message if routing is not None else UNAVAILABLE_MESSAGE
            return self._final(base, "unavailable", message=msg)

        if needs_review and not base["review"]["reasons"]:
            base["review"]["reasons"] = ["forecast engine routed this option to staff"]
        base["review"]["required"] = needs_review
        base["recommended_option_id"] = selected
        evidence_by_choice = {"as_requested": evaluation.check.evidence,
                              **{o["option_id"]: o.get("_evidence", {}) for o in options}}
        if selected:
            chosen = options[0]
            base["review"]["evidence"] = chosen.pop("_evidence", base["review"]["evidence"])
        for o in options:
            o.pop("_evidence", None)

        with self.orders.lock:
            if inv is not self.inv:
                raise ApiError(409, "The backend was cleared while processing this request. Please submit it again.")
            order_id = self.orders.new_id()
            hold_key = selected or "as_requested"
            hold = inv.hold(order_id, requirements_by_choice[hold_key], ttl_seconds=HOLD_SECONDS)
            if not hold.ok:
                return self._final(base, "unavailable", message="Stock just changed and this can't be made right now. "
                                                                 "Please try again.")
            now = utcnow()
            evaluation_out = self._final(base, status, message=_customer_message(status, options, selected))
            if needs_review:
                flags = parsed.parser_output.get("flags") or {}
                safety = bool(parsed.restrictions) or flags.get("level") in ("allergy", "diet")
                evaluation_out["customer_message"] = (
                    "Please review the proposed ingredients and price before sending your request to staff. "
                    + ("Staff must review allergy and dietary requests; AI cannot certify a dish as safe or allergen-free."
                       if safety else "The kitchen needs to confirm this change before it's final.")
                )
            evaluation_out["order_id"] = order_id
            evaluation_out["expires_at"] = (now + timedelta(seconds=HOLD_SECONDS)).isoformat()
            order = Order(
                order_id=order_id,
                status="awaiting_customer",
                created_at=now, expires_at=now + timedelta(seconds=HOLD_SECONDS), channel=channel,
                customer_name=customer_name, dish=dish, qty=qty, original_text=text, evaluation=evaluation_out,
                selected_option_id=selected, requirements_by_choice=requirements_by_choice,
                evidence_by_choice=evidence_by_choice, ops_by_choice=ops_by_choice,
                check_args=_check_args(parsed.parser_output),
            )
            order.log("created", status)
            self.orders.add(order)
            return evaluation_out

    def _options(self, evaluation, routing, review_required, requirements_by_choice, ops_by_choice):
        cat = self.inv.catalog
        ordered: list[tuple[int, Any]] = []
        unavailable: list[dict[str, Any]] = []
        if routing is not None:
            ordered = [(c.option_index, c) for c in routing.choices if c.option_index is not None]
            unavailable = [{"label": u.get("label", ""), "reasons": list(u.get("reasons", []))}
                           for u in routing.unavailable]
        else:
            ordered = [(i, None) for i in range(len(evaluation.options))]

        options = []
        for n, (index, choice) in enumerate(ordered, start=1):
            opt = evaluation.options[index]
            option_id = f"opt-{n}"
            changes = [{
                "replace": c["replace"], "replace_name": cat.name_of(c["replace"]),
                "with": c["with"], "with_name": cat.name_of(c["with"]) if c["with"] else None,
            } for c in opt.changes]
            label = "; ".join(f"{ch['with_name']} instead of {ch['replace_name']}" if ch["with"]
                              else f"Leave off {ch['replace_name']}" for ch in changes)
            route = choice.route if choice is not None else ("staff" if review_required else "customer")
            price = choice.price_change_cents if choice is not None else opt.price_delta_cents
            options.append({
                "option_id": option_id,
                "label": label,
                "changes": changes,
                "route": "staff" if review_required else route,
                "score": choice.score if choice is not None else None,
                "similarity": choice.similarity if choice is not None else opt.similarity,
                "price_change_cents": price,
                "reasons": list(choice.reasons) if choice is not None else [],
                "staff_notes": list(choice.staff_notes) if choice is not None else [],
                "kitchen_instructions": list(opt.check.kitchen_instructions),
                "_evidence": opt.check.evidence,
            })
            requirements_by_choice[option_id] = dict(opt.requirements)
            ops_by_choice[option_id] = [dict(op) for op in opt.ops]
        return options, unavailable

    @staticmethod
    def _final(base: dict[str, Any], status: str, message: str, questions: list[str] | None = None,
               unavailable: list[dict] | None = None) -> dict[str, Any]:
        out = dict(base)
        out["status"] = status
        out["questions"] = list(dict.fromkeys(questions or []))
        out["customer_message"] = message
        out.setdefault("as_requested", {"feasible": False, "reasons": [], "kitchen_instructions": [],
                                        "price_change_cents": 0})
        out.setdefault("review", {"required": False, "reasons": [], "staff_notes": [], "evidence": {}})
        if unavailable:
            out["unavailable_options"] = list(out.get("unavailable_options", [])) + unavailable
        out.setdefault("expires_at", None)
        if status in ("needs_clarification", "unavailable"):
            out["order_id"] = None
            if len(out.get("clarifications", [])) >= MAX_CLARIFICATION_REPLIES:
                out["status"] = "unavailable"
                out["questions"] = []
                out["clarification_exhausted"] = True
                out["customer_message"] = (
                    "We couldn't find a request we can accommodate after three replies. "
                    "Please start a new request or contact restaurant staff for help."
                )
        return out

    # ----- order actions -----------------------------------------------------------------------

    def accept(self, order_id: str, option_id: str | None,
               declined_extras: list[str] | None = None) -> dict[str, Any]:
        self.sweep()
        with self.orders.lock:
            order = self._order(order_id)
            if order.status != "awaiting_customer":
                raise ApiError(409, f"Order is {order.status}; it can't be accepted.")
            choice = order.choice(option_id)
            if choice is None:
                raise ApiError(400, "Unknown option, or the request can't be made as asked.")
            key = option_id or "as_requested"
            if order.staff_approved_choice is not None and key != order.staff_approved_choice:
                raise ApiError(409, "Please confirm the option approved by staff, or submit a new request.")
            if declined_extras:
                if order.staff_approved_choice is not None:
                    raise ApiError(409, "Staff approved this order with its extras; submit a new request to change them.")
                self._decline_extras(order, key, choice, set(declined_extras))
            needs_staff = order.evaluation["review"]["required"] or (option_id and choice.get("route") == "staff")
            if needs_staff and order.staff_approved_choice is None:
                self._rehold(order, key)
                order.status = "awaiting_staff"
                order.evaluation["status"] = "needs_staff_review"
                order.evaluation["customer_message"] = "Staff are reviewing the option you chose. You'll confirm the final option and price after their review."
                order.log("accepted", "waiting for staff review")
            else:
                self._confirm(order, key)
                order.log("accepted", "confirmed")
            order.selected_option_id = option_id
            order.evaluation["review"]["evidence"] = order.evidence_by_choice.get(key, {})
            return order.to_dict()

    def review(self, order_id: str, action: str, option_id: str | None = None, note: str = "") -> dict[str, Any]:
        self.sweep()
        with self.orders.lock:
            order = self._order(order_id)
            if order.status != "awaiting_staff":
                raise ApiError(409, f"Order is {order.status}; nothing to review.")
            if action == "reject":
                self.inv.release(order_id)
                order.status = "rejected"
                order.staff_note = note or None
                order.evaluation["status"] = "unavailable"
                order.evaluation["customer_message"] = (
                    "The kitchen couldn't approve this request" + (f": {note.rstrip('.')}." if note else ".")
                    + " You won't be charged. Would you like something else instead?")
                order.log("rejected", note)
            elif action in ("approve", "edit"):
                target = option_id if (action == "edit" or option_id is not None) else order.selected_option_id
                if order.choice(target) is None:
                    raise ApiError(400, "Unknown option, or the request can't be made as asked.")
                self._rehold(order, target or "as_requested")
                order.selected_option_id = target
                order.staff_approved_choice = target or "as_requested"
                order.evaluation["staff_approved_choice"] = order.staff_approved_choice
                order.status = "awaiting_customer"
                order.evaluation["status"] = "ready" if target is None else "needs_customer_approval"
                order.evaluation["recommended_option_id"] = target
                order.evaluation["review"]["evidence"] = order.evidence_by_choice.get(target or "as_requested", {})
                order.evaluation["customer_message"] = "Staff have reviewed your request. Are you happy with this final option and price? Confirm to place the order."
                order.log("edited" if action == "edit" else "approved", note)
            else:
                raise ApiError(400, "action must be approve, edit or reject.")
            if note and action != "reject":
                order.staff_note = note
            return order.to_dict()

    def cancel(self, order_id: str) -> dict[str, Any]:
        """Customer withdraws an order that isn't confirmed yet; releases its stock hold."""
        self.sweep()
        with self.orders.lock:
            order = self._order(order_id)
            if order.status not in ("awaiting_customer", "awaiting_staff"):
                raise ApiError(409, f"Order is {order.status}; it can't be cancelled.")
            self.inv.release(order_id)
            order.status = "cancelled"
            order.log("cancelled", "customer cancelled")
            return order.to_dict()

    def _decline_extras(self, order: Order, key: str, choice: dict[str, Any], declined: set[str]) -> None:
        """Drop the declined extras from this choice and re-check the exact remaining recipe."""
        extras = {e["id"]: e for e in order.evaluation.get("extras", [])}
        unknown = declined - set(extras)
        if unknown:
            raise ApiError(400, f"Not an extra on this order: {', '.join(sorted(unknown))}.")
        dish_id = order.dish["id"]
        ops = [op for op in order.ops_by_choice.get(key, [])
               if not (op.get("op") == "add"
                       and _as_dish_part(self.inv, dish_id, op)["ingredient"] in declined)]
        exclude, diets = order.check_args
        res = self.inv.check(dish_id, ops, order.qty, exclude, diets)
        if not res.ok:
            raise ApiError(409, "That combination can't be made; please submit a new request.")
        order.ops_by_choice[key] = ops
        order.requirements_by_choice[key] = dict(res.requirements)
        dropped = sum(extras[i]["price_cents"] for i in declined)
        choice["kitchen_instructions"] = list(res.kitchen_instructions)
        if choice.get("price_change_cents") is not None:
            choice["price_change_cents"] -= dropped
        choice["extras_cents"] = choice.get("extras_cents", 0) - dropped
        choice["declined_extras"] = sorted(declined)
        order.log("extras", "declined " + ", ".join(extras[i]["name"] for i in sorted(declined)))

    def _rehold(self, order: Order, key: str) -> None:
        res = self.inv.hold(order.order_id, order.requirements_by_choice[key], ttl_seconds=HOLD_SECONDS)
        if not res.ok:
            raise ApiError(409, f"Stock changed; this option can't be held: {res.shortfalls}")

    def _confirm(self, order: Order, key: str) -> None:
        res = self.inv.confirm(order.order_id, order.requirements_by_choice[key])
        if not res.ok:
            raise ApiError(409, f"Stock changed; please re-evaluate: {res.shortfalls}")
        order.status = "confirmed"

    def _order(self, order_id: str) -> Order:
        order = self.orders.get(order_id)
        if order is None:
            raise ApiError(404, f"Unknown order {order_id!r}.")
        return order

    # ----- reads / housekeeping ----------------------------------------------------------------

    def list_orders(self) -> list[dict[str, Any]]:
        self.sweep()
        return [o.to_dict() for o in self.orders.all()]

    def get_order(self, order_id: str) -> dict[str, Any]:
        self.sweep()
        return self._order(order_id).to_dict()

    def sweep(self) -> list[str]:
        """Expire orders whose stock holds timed out (timeout = reject)."""
        expired = self.inv.expired_holds()
        now = utcnow()
        with self.orders.lock:
            for order in self.orders.all():
                if order.status in ("awaiting_customer", "awaiting_staff") and (
                        order.order_id in expired or order.expires_at <= now):
                    self.inv.release(order.order_id)
                    order.status = "expired"
                    order.log("expired", "no response before the hold expired")
        return expired

    def stock(self) -> list[dict[str, Any]]:
        inv = self.inv
        out = []
        for item in inv.stock.items():
            unit = inv.unit_of(item) or ""
            name = inv.catalog.ingredients[item].name if item in inv.catalog.ingredients else inv.catalog.name_of(item)
            out.append({"id": item, "name": name, "unit": unit,
                        "on_hand": inv.stock.on_hand(item), "available": inv.available(item),
                        "initial_on_hand": self._initial.get(item, 0)})
        return out

    def set_stock(self, item_id: str, on_hand: int, reason: str) -> dict[str, Any]:
        if not self.inv.stock.is_tracked(item_id):
            raise ApiError(404, f"Unknown stock item {item_id!r}.")
        if on_hand < 0:
            raise ApiError(400, "on_hand must be >= 0.")
        self.inv.set_on_hand(item_id, on_hand, reason or "demo control")
        return next(i for i in self.stock() if i["id"] == item_id)

    def reset(self) -> None:
        with self.orders.lock:
            self.inv = self._loader()
            self._initial = {i: self.inv.stock.on_hand(i) for i in self.inv.stock.items()}
            self.orders.clear()


def _check_args(parser_output: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Allergens/diets exactly as inventory.evaluate_request reads them from the flags."""
    flags = parser_output.get("flags") or {}
    raw_diets = flags.get("diets", flags.get("diet")) or []
    diets = [raw_diets] if isinstance(raw_diets, str) else list(raw_diets)
    return [a.lower() for a in flags.get("allergens") or []], [d.lower() for d in diets]


def _extras(inv: Inventory, dish_id: str, qty: int, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Paid extras the customer asked for: one line each, priced from the dish's menu."""
    dish = inv.catalog.dishes[dish_id]
    out: dict[str, dict[str, Any]] = {}
    for op in ops:
        if op.get("op") != "add":
            continue
        part = _as_dish_part(inv, dish_id, op)["ingredient"]
        extra = dish.extras.get(part)
        if extra is None:
            continue
        line = out.setdefault(part, {"id": part, "name": inv.catalog.name_of(part), "qty": 0, "price_cents": 0})
        portions = int(op.get("qty", 1) or 1)
        line["qty"] += portions
        line["price_cents"] += extra.price_cents * portions * qty
    return list(out.values())


def _as_dish_part(inv: Inventory, dish_id: str, op: dict[str, Any]) -> dict[str, Any]:
    """Name an op by the dish part the kitchen changes: "extra tofu" on the tofu bowl is extra
    Crispy tofu (whose defining ingredient is firm tofu), not extra raw firm tofu."""
    target = op.get("ingredient")
    cat = inv.catalog
    dish = cat.dishes[dish_id]
    if not isinstance(target, str) or target in dish.components:
        return op
    parts = [cid for cid in dish.components if getattr(cat.components[cid], "main", None) == target]
    return {**op, "ingredient": parts[0]} if len(parts) == 1 else op


def _unavailable_message(reasons: list[str]) -> str:
    return UNAVAILABLE_MESSAGE + (" " + " ".join(reasons) if reasons else "")


def _customer_message(status: str, options: list[dict], selected: str | None) -> str:
    chosen = next((o for o in options if o["option_id"] == selected), None)
    if status == "ready":
        return "We can make this as requested."
    if status == "needs_customer_approval":
        return "We can't make it exactly as asked. Please choose one of these options."
    proposal = f"Proposed: {chosen['label']}. " if chosen else ""
    return proposal + "The kitchen is confirming your request before it's made."
