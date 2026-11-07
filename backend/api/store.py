"""In-memory order store for the demo (restarting the API clears it)."""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

STATUSES = ("awaiting_customer", "awaiting_staff", "confirmed", "rejected", "expired", "cancelled")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Order:
    order_id: str
    status: str
    created_at: datetime
    expires_at: datetime
    channel: str | None
    customer_name: str | None
    dish: dict[str, str]
    qty: int
    original_text: str
    evaluation: dict[str, Any]
    selected_option_id: str | None
    staff_note: str | None = None
    # Internal: what to hold/confirm for each choice ("as_requested" or option_id).
    requirements_by_choice: dict[str, dict[str, int]] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    def log(self, event: str, note: str = "") -> None:
        self.history.append({"at": utcnow().isoformat(), "event": event, "note": note})

    def choice(self, option_id: str | None) -> dict[str, Any] | None:
        if option_id is None:
            return self.evaluation["as_requested"] if self.evaluation["as_requested"]["feasible"] else None
        return next((o for o in self.evaluation["options"] if o["option_id"] == option_id), None)

    def kitchen_instructions(self) -> list[str]:
        chosen = self.choice(self.selected_option_id)
        return list(chosen["kitchen_instructions"]) if chosen else []

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "channel": self.channel,
            "customer_name": self.customer_name,
            "dish": dict(self.dish),
            "qty": self.qty,
            "original_text": self.original_text,
            "selected_option_id": self.selected_option_id,
            "staff_note": self.staff_note,
            "kitchen_instructions": self.kitchen_instructions(),
            "evaluation": self.evaluation,
            "history": list(self.history),
        }


class OrderStore:
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._ids = itertools.count(1)
        self.lock = threading.RLock()

    def new_id(self) -> str:
        with self.lock:
            return f"ord-{next(self._ids):04d}"

    def add(self, order: Order) -> None:
        with self.lock:
            self._orders[order.order_id] = order

    def get(self, order_id: str) -> Order | None:
        with self.lock:
            return self._orders.get(order_id)

    def all(self) -> list[Order]:
        with self.lock:
            return sorted(self._orders.values(), key=lambda o: o.created_at, reverse=True)

    def clear(self) -> None:
        with self.lock:
            self._orders.clear()
            self._ids = itertools.count(1)
