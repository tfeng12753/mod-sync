"""FastAPI app. Run from the repo root:

    .venv/Scripts/python -m uvicorn backend.api.app:app --reload --port 8000

Endpoints follow docs/api-contract.md. State is in memory (restart = fresh synthetic stock).
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .service import ApiError, OrderService


class Clarification(BaseModel):
    question: str
    answer: str


class EvaluateBody(BaseModel):
    dish_id: str
    qty: int = Field(default=1, ge=1, le=20)
    text: str = Field(default="", max_length=4000)
    clarifications: list[Clarification] = Field(default_factory=list)
    channel: str | None = None
    customer_name: str | None = None


class AcceptBody(BaseModel):
    option_id: str | None = None
    # Extras (dish part ids from evaluation["extras"]) the customer chose not to keep.
    declined_extras: list[str] = []


class ReviewBody(BaseModel):
    action: str
    option_id: str | None = None
    note: str = ""


class StockBody(BaseModel):
    on_hand: int = Field(ge=0)
    reason: str = ""


def create_app(service: OrderService | None = None, frontend_dist: Path | None = None) -> FastAPI:
    app = FastAPI(title="ModSync API", version="1.0")
    origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])
    app.state.service = service
    service_lock = Lock()

    def svc() -> OrderService:
        if app.state.service is None:  # created lazily so importing the module is cheap
            # Initial menu, stock and order requests share exactly one store.
            with service_lock:
                if app.state.service is None:
                    app.state.service = OrderService()
        return app.state.service

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/menu")
    def menu():
        return svc().inv.menu()

    @app.post("/api/evaluate")
    async def evaluate(body: EvaluateBody):
        return await svc().evaluate(body.dish_id, body.qty, body.text,
                                    [c.model_dump() for c in body.clarifications],
                                    channel=body.channel, customer_name=body.customer_name)

    @app.post("/api/orders/{order_id}/accept")
    def accept(order_id: str, body: AcceptBody):
        return svc().accept(order_id, body.option_id, body.declined_extras)

    @app.post("/api/orders/{order_id}/review")
    def review(order_id: str, body: ReviewBody):
        return svc().review(order_id, body.action, body.option_id, body.note)

    @app.post("/api/orders/{order_id}/cancel")
    def cancel(order_id: str):
        return svc().cancel(order_id)

    @app.get("/api/orders")
    def orders():
        return svc().list_orders()

    @app.get("/api/orders/{order_id}")
    def order(order_id: str):
        return svc().get_order(order_id)

    @app.get("/api/stock")
    def stock():
        return {"items": svc().stock()}

    @app.post("/api/stock/{item_id}")
    def set_stock(item_id: str, body: StockBody):
        return svc().set_stock(item_id, body.on_hand, body.reason)

    @app.post("/api/demo/reset")
    def reset():
        svc().reset()
        return {"status": "reset"}

    # API/docs routes take precedence; local API-only development still works.
    configured_dist = frontend_dist or os.environ.get("FRONTEND_DIST")
    if configured_dist:
        dist = Path(configured_dist)
        if not (dist / "index.html").is_file():
            raise RuntimeError("FRONTEND_DIST must contain a built index.html")
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app


app = create_app()
