"""Stock state: on-hand quantities, holds during staff review, confirmation, and a usage log.

In-memory and thread-safe. Stocked items are raw ingredients (used at assembly) and premade
components. Quantities are integers in the item's catalog unit.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .models import StockResult

Clock = Callable[[], float]


@dataclass
class Hold:
    order_id: str
    requirements: dict[str, int]
    expires_at: float


class StockStore:
    def __init__(
        self,
        on_hand: dict[str, int],
        safety_buffer: dict[str, int] | None = None,
        deliveries: list[dict[str, Any]] | None = None,
        clock: Clock = time.time,
    ) -> None:
        self._on_hand = {k: int(v) for k, v in on_hand.items()}
        self._buffer = {k: int(v) for k, v in (safety_buffer or {}).items()}
        self._deliveries = list(deliveries or [])
        self._clock = clock
        self._holds: dict[str, Hold] = {}
        self._confirmed: dict[str, dict[str, int]] = {}
        self._expired: list[str] = []
        self._usage: list[dict[str, Any]] = []
        self._adjustments: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    # ----- reads -------------------------------------------------------------------------------

    def is_tracked(self, item: str) -> bool:
        return item in self._on_hand

    def items(self) -> list[str]:
        """All tracked stock item IDs."""
        with self._lock:
            return list(self._on_hand)

    def on_hand(self, item: str) -> int:
        with self._lock:
            return self._on_hand.get(item, 0)

    def available(self, item: str, ignore_order: str | None = None) -> int:
        """On-hand stock minus active holds (optionally ignoring one order's own hold)."""
        with self._lock:
            self._purge_expired()
            held = sum(
                h.requirements.get(item, 0) for oid, h in self._holds.items() if oid != ignore_order
            )
            return self._on_hand.get(item, 0) - held

    def safety_buffer(self, item: str) -> int:
        return self._buffer.get(item, 0)

    def held_orders(self) -> list[str]:
        with self._lock:
            self._purge_expired()
            return list(self._holds)

    def is_confirmed(self, order_id: str) -> bool:
        with self._lock:
            return order_id in self._confirmed

    def pop_expired(self) -> list[str]:
        """Order IDs whose holds expired since the last call (for timeout notifications)."""
        with self._lock:
            self._purge_expired()
            out, self._expired = self._expired, []
            return out

    def usage_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._usage]

    def adjustments(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._adjustments]

    def deliveries(self) -> list[dict[str, Any]]:
        return [dict(d) for d in self._deliveries]

    # ----- writes ------------------------------------------------------------------------------

    def hold(self, order_id: str, requirements: dict[str, int], ttl_seconds: float = 600) -> StockResult:
        """Set stock aside for an order awaiting approval. Re-holding an order replaces its hold
        atomically; if the new hold doesn't fit, the old hold is kept."""
        reqs = _clean(requirements)
        with self._lock:
            self._purge_expired()
            if order_id in self._confirmed:
                return StockResult(False, "ALREADY_CONFIRMED", f"Order {order_id} is already confirmed.")
            short = self._shortfalls(reqs, ignore_order=order_id)
            if short:
                return StockResult(False, "INSUFFICIENT_STOCK", "Not enough stock to hold this order.", short)
            self._holds[order_id] = Hold(order_id, reqs, self._clock() + ttl_seconds)
            return StockResult(True, "HELD", f"Stock held for order {order_id}.")

    def confirm(self, order_id: str, requirements: dict[str, int] | None = None) -> StockResult:
        """Recheck availability and deduct exactly once. Uses the order's hold if ``requirements``
        is omitted. Calling it again for a confirmed order is a no-op."""
        with self._lock:
            self._purge_expired()
            if order_id in self._confirmed:
                return StockResult(True, "ALREADY_CONFIRMED", "Order was already confirmed; nothing deducted.",
                                   already_confirmed=True)
            hold = self._holds.get(order_id)
            if requirements is not None:
                reqs = _clean(requirements)
            elif hold is not None:
                reqs = hold.requirements
            else:
                return StockResult(False, "NO_REQUIREMENTS",
                                   "No active hold for this order and no requirements given (hold may have expired).")
            short = self._shortfalls(reqs, ignore_order=order_id)
            if short:
                return StockResult(False, "INSUFFICIENT_STOCK", "Stock changed; not enough to confirm.", short)
            now = self._clock()
            for item, qty in reqs.items():
                self._on_hand[item] = self._on_hand.get(item, 0) - qty
                self._usage.append({"ts": now, "order_id": order_id, "item": item, "qty": qty})
            self._holds.pop(order_id, None)
            self._confirmed[order_id] = dict(reqs)
            return StockResult(True, "CONFIRMED", f"Order {order_id} confirmed; stock deducted.")

    def release(self, order_id: str) -> bool:
        with self._lock:
            return self._holds.pop(order_id, None) is not None

    def adjust(self, item: str, delta: int, reason: str) -> int:
        """Change on-hand stock (deliveries, waste, recounts). Cannot go below zero."""
        with self._lock:
            new = self._on_hand.get(item, 0) + int(delta)
            if new < 0:
                raise ValueError(f"adjusting {item!r} by {delta} would make stock negative ({new})")
            self._on_hand[item] = new
            self._adjustments.append({"ts": self._clock(), "item": item, "delta": int(delta), "reason": reason})
            return new

    def set_on_hand(self, item: str, qty: int, reason: str) -> int:
        with self._lock:
            return self.adjust(item, int(qty) - self._on_hand.get(item, 0), reason)

    # ----- internals ---------------------------------------------------------------------------

    def _shortfalls(self, reqs: dict[str, int], ignore_order: str | None) -> dict[str, dict[str, int]]:
        short = {}
        for item, need in reqs.items():
            avail = self.available(item, ignore_order=ignore_order)
            if need > avail:
                short[item] = {"need": need, "available": max(avail, 0)}
        return short

    def _purge_expired(self) -> None:
        now = self._clock()
        for oid in [oid for oid, h in self._holds.items() if h.expires_at <= now]:
            del self._holds[oid]
            self._expired.append(oid)


def _clean(requirements: dict[str, int]) -> dict[str, int]:
    out = {}
    for item, qty in requirements.items():
        q = int(qty)
        if q < 0:
            raise ValueError(f"requirement for {item!r} is negative")
        if q:
            out[item] = q
    return out


def load_stock(path: str | Path, clock: Clock = time.time) -> StockStore:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return StockStore(raw["on_hand"], raw.get("safety_buffer"), raw.get("deliveries"), clock=clock)
