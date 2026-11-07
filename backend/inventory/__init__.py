"""Inventory, feasibility and substitute similarity. Deterministic: no LLM calls here.

    from backend.inventory import load_inventory
    inv = load_inventory()
    result = inv.check("chicken_bowl", [{"op": "remove", "ingredient": "avocado"}])
"""

from .catalog import Catalog, CatalogError, load_catalog, parse_catalog
from .models import Candidate, CheckResult, Option, Reason, RequestEvaluation, StockResult
from .service import Inventory, load_inventory
from .stock import StockStore, load_stock

__all__ = [
    "Candidate",
    "Catalog",
    "CatalogError",
    "CheckResult",
    "Inventory",
    "Option",
    "Reason",
    "RequestEvaluation",
    "StockResult",
    "StockStore",
    "load_catalog",
    "load_inventory",
    "load_stock",
    "parse_catalog",
]
