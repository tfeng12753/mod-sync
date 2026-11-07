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

import httpx

from backend.inventory import Inventory, load_inventory
from backend.parsing.parser import ParserValidationError

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
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("OpenRouter parsing request failed: HTTP %s", status)
            messages = {
                400: "OpenRouter rejected the configured model or request settings. Check OPENROUTER_MODEL and structured-output support.",
                401: "OpenRouter rejected the API key. Check OPENROUTER_API_KEY in the backend terminal or server environment.",
                402: "OpenRouter reports insufficient credits. Add credits or check the API key's spending limit.",
                403: "OpenRouter denied access. Check the API key's permissions and account restrictions.",
                404: "OpenRouter could not find the configured model. Check OPENROUTER_MODEL.",
                429: "OpenRouter is rate limiting requests. Please wait and try again.",
            }
            raise ApiError(502, messages.get(status,
                           f"OpenRouter parsing service failed (HTTP {status}). Please try again.")) from exc
        except httpx.TimeoutException as exc:
            logger.warning("OpenRouter parsing request timed out")
            raise ApiError(502, "OpenRouter took too long to respond. Please try again.") from exc
        except httpx.RequestError as exc:
            logger.warning("OpenRouter connection failed: %s", type(exc).__name__)
            raise ApiError(502, "The backend couldn't connect to OpenRouter. Check its internet connection and try again.") from exc
        except ParserValidationError as exc:
            logger.warning("parser rejected the structured response")
            raise ApiError(502, "The parser returned an invalid structured result. Please retry or ask restaurant staff.") from exc
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

        if parsed.status == "needs_clarification":
            return self._final(base, "needs_clarification", questions=list(parsed.questions),
                               message="We need a little more detail about your request.")

        evaluation = inv.evaluate_request(parsed.parser_output)
        review_required = evaluation.requires_staff_review or parsed.status == "needs_staff_review"
        review_reasons = list(evaluation.review_reasons)
        if parsed.status == "needs_staff_review" and not review_reasons:
            review_reasons = ["parser flagged this request for staff review"]
        staff_notes = list(parsed.staff_notes)
        base["as_requested"] = {
            "feasible": evaluation.check.ok,
            "reasons": [r.message for r in evaluation.check.reasons],
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

        # Forecast engine routing (customer vs staff, scores, prices). Fall back to inventory order on failure.
        try:
            routing = await self.route_fn(inv, evaluation)
        except Exception as exc:
            logger.exception("engine routing failed")
            routing = None
            base["warnings"].append(f"Forecast routing unavailable ({type(exc).__name__}); inventory ranking used.")

        requirements_by_choice: dict[str, dict[str, int]] = {}
        if evaluation.check.ok:
            requirements_by_choice["as_requested"] = dict(evaluation.check.requirements)
        options, unavailable = self._options(evaluation, routing, review_required, requirements_by_choice)
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
            status = "needs_staff_review" if needs_review else "ready"
            selected = None
        elif options:
            selected = options[0]["option_id"]
            needs_review = review_required or options[0]["route"] == "staff"
            status = "needs_staff_review" if needs_review else "needs_customer_approval"
        else:
            msg = routing.customer_message if routing is not None else UNAVAILABLE_MESSAGE
            return self._final(base, "unavailable", message=msg)

        if status == "needs_staff_review" and not base["review"]["reasons"]:
            base["review"]["reasons"] = ["forecast engine routed this option to staff"]
        base["review"]["required"] = status == "needs_staff_review"
        base["recommended_option_id"] = selected
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
            evaluation_out["order_id"] = order_id
            evaluation_out["expires_at"] = (now + timedelta(seconds=HOLD_SECONDS)).isoformat()
            order = Order(
                order_id=order_id,
                status="awaiting_staff" if status == "needs_staff_review" else "awaiting_customer",
                created_at=now, expires_at=now + timedelta(seconds=HOLD_SECONDS), channel=channel,
                customer_name=customer_name, dish=dish, qty=qty, original_text=text, evaluation=evaluation_out,
                selected_option_id=selected, requirements_by_choice=requirements_by_choice,
            )
            order.log("created", status)
            self.orders.add(order)
            return evaluation_out

    def _options(self, evaluation, routing, review_required, requirements_by_choice):
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

    def accept(self, order_id: str, option_id: str | None) -> dict[str, Any]:
        self.sweep()
        with self.orders.lock:
            order = self._order(order_id)
            if order.status != "awaiting_customer":
                raise ApiError(409, f"Order is {order.status}; it can't be accepted.")
            choice = order.choice(option_id)
            if choice is None:
                raise ApiError(400, "Unknown option, or the request can't be made as asked.")
            order.selected_option_id = option_id
            key = option_id or "as_requested"
            needs_staff = order.evaluation["review"]["required"] or (option_id and choice.get("route") == "staff")
            if needs_staff:
                self._rehold(order, key)
                order.status = "awaiting_staff"
                order.log("accepted", "waiting for staff review")
            else:
                self._confirm(order, key)
                order.log("accepted", "confirmed")
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
                order.log("rejected", note)
            elif action in ("approve", "edit"):
                target = option_id if (action == "edit" or option_id is not None) else order.selected_option_id
                if order.choice(target) is None:
                    raise ApiError(400, "Unknown option, or the request can't be made as asked.")
                order.selected_option_id = target
                self._confirm(order, target or "as_requested")
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
