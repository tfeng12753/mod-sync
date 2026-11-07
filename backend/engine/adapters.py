"""Optional interface for the inventory teammate; no storage implementation here."""
from datetime import datetime
from typing import Protocol

from .schemas import InventorySnapshot


class InventoryReader(Protocol):
    async def get_snapshot(
        self, *, ingredient_ids: list[str], start: datetime, end: datetime,
    ) -> InventorySnapshot:
        """Return positions for the window, observing the contract in contracts/README.md."""
        ...


# The team's actual inventory interface. Changes/order payloads pass through
# unchanged: this adapter does not invent their schema or confirmation behavior.
class InventoryFunctions(Protocol):
    def requirements(self, dish_id, changes): ...
    def check(self, dish_id, changes, qty=1): ...
    def available(self, ingredient_id): ...
    def confirm(self, order): ...


async def _invoke(function, *args, **kwargs):
    import asyncio
    import inspect
    if inspect.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    result = await asyncio.to_thread(function, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _quantities(raw):
    from collections.abc import Mapping
    from math import isfinite
    if not isinstance(raw, Mapping):
        raise ValueError("inventory requirements must be a mapping")
    result = {}
    for ingredient_id, value in raw.items():
        if not isinstance(ingredient_id, str) or not ingredient_id or isinstance(value, bool):
            raise ValueError("invalid ingredient ID or quantity")
        number = float(value)
        if not isfinite(number) or number < 0:
            raise ValueError("inventory quantities must be finite and nonnegative")
        if number:
            result[ingredient_id] = number
    return result


class InventoryGateway:
    def __init__(self, functions: InventoryFunctions, *, success_statuses: set[str],
                 blocked_statuses: set[str] | None = None):
        if not success_statuses or success_statuses & (blocked_statuses or set()):
            raise ValueError("configure distinct inventory success and blocked status values")
        self.functions = functions
        self.success_statuses = set(success_statuses)
        self.blocked_statuses = set(blocked_statuses or [])

    async def requirements(self, dish_id, changes):
        return _quantities(await _invoke(self.functions.requirements, dish_id, changes))

    async def check(self, dish_id, changes, qty=1):
        from collections.abc import Mapping
        raw = await _invoke(self.functions.check, dish_id, changes, qty=qty)
        def get(key):
            return raw[key] if isinstance(raw, Mapping) else getattr(raw, key)
        status, reasons = get("status"), get("reasons")
        if not isinstance(status, str) or not isinstance(reasons, (tuple, list)) or any(
            not isinstance(reason, str) for reason in reasons
        ):
            raise ValueError("inventory Result requires a string status and list of reasons")
        passed = True if status in self.success_statuses else False if status in self.blocked_statuses else None
        return passed, list(reasons) + ([f"Inventory status: {status}."] if passed is not True else []), _quantities(get("requirements"))

    async def snapshot(self, ingredients, *, as_of, valid_until,
                       committed_in_horizon=None, version=None):
        import asyncio
        from math import isfinite
        from .schemas import InventoryPosition
        values = await asyncio.gather(*[
            _invoke(self.functions.available, ingredient.ingredient_id) for ingredient in ingredients
        ])
        positions = []
        known = committed_in_horizon is not None and all(
            i.ingredient_id in committed_in_horizon for i in ingredients
        )
        for ingredient, raw in zip(ingredients, values):
            net = float(raw)
            if isinstance(raw, bool) or not isfinite(net) or net < 0:
                raise ValueError("available() must return a finite nonnegative quantity")
            committed = (committed_in_horizon or {}).get(ingredient.ingredient_id, 0)
            if isinstance(committed, bool) or not isfinite(committed) or committed < 0:
                raise ValueError("commitments must be finite nonnegative quantities")
            # Normalize to the legacy gross-stock representation. Adding back
            # known commitments here cancels their one subtraction downstream.
            # Without totals, retain net stock and conservatively reserve gross demand.
            positions.append(InventoryPosition(
                **ingredient.model_dump(), available_now=net + committed,
                committed_in_horizon=committed,
            ))
        return InventorySnapshot(
            version=version or "unversioned-inventory-read", as_of=as_of, valid_until=valid_until,
            positions=positions, commitments_known=known, version_is_authoritative=version is not None,
        )

    async def confirm(self, order):
        """Only the API owner calls this after customer/staff acceptance. No retries."""
        return await _invoke(self.functions.confirm, order)
