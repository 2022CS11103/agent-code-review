"""ASGI entrypoint."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings
from app.db import SessionLocal
from app.errors import APIError
from app.limit import RateLimiter
from app.redact import redact_text
from app.services.pipeline import fail_interrupted
from app.services.seed import seed_demo
from app.worker import worker_loop

log = logging.getLogger("prism")


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(message)s")

    class _Redact(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.msg = redact_text(str(record.msg), limit=2000)
            return True

    logging.getLogger().addFilter(_Redact())


def migrate() -> None:
    if __import__("os").environ.get("PRISM_AUTO_MIGRATE") == "0":
        return
    from alembic import command
    from alembic.config import Config

    from app.config import BACKEND_ROOT

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", get_settings().resolved_database_url)
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    if settings.github_webhook_secret == "dev-only-webhook-secret":
        log.warning("PRISM_GITHUB_WEBHOOK_SECRET is the dev default. Set a real secret before exposing webhooks.")
    migrate()
    db = SessionLocal()
    try:
        fail_interrupted(db)
        if settings.auto_seed:
            seed_demo(db, SessionLocal, process_history=True)
    finally:
        db.close()
    stop = __import__("asyncio").Event()
    task = None
    if settings.worker_mode == "embedded":
        task = __import__("asyncio").create_task(worker_loop(stop))
    yield
    stop.set()
    if task is not None:
        await task


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PRISM AI",
        version="0.1.0",
        summary="Production-grade code review and remediation. Tests are not proof of safety.",
        lifespan=lifespan,
    )
    app.add_middleware(Guard, settings=settings)
    app.include_router(router)

    @app.exception_handler(APIError)
    async def _api_error(_request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request", "message": "Request validation failed.", "details": exc.errors()}},
        )

    return app


class Guard:
    def __init__(self, app, settings) -> None:
        self.app = app
        self.general = RateLimiter(settings.rate_limit_per_minute)
        self.webhook = RateLimiter(settings.webhook_rate_limit_per_minute)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        method = scope.get("method") or "GET"
        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope.get("headers") or []}
        request_id = headers.get("x-request-id") or str(uuid.uuid4())
        if path.startswith("/api/") and path not in {"/api/v1/health", "/api/v1/ready"}:
            client = scope.get("client") or ("local", 0)
            key = headers.get("x-forwarded-for") or str(client[0])
            limiter = self.webhook if path.startswith("/api/v1/webhooks/") else self.general
            if not limiter.allow(key):
                await _json(send, 429, {"error": {"code": "rate_limited", "message": "Too many requests.", "request_id": request_id}})
                return
        if method in {"POST", "PATCH", "PUT", "DELETE"} and not path.startswith("/api/v1/webhooks/"):
            origin = headers.get("origin")
            if origin and origin not in get_settings().origin_allowlist:
                await _json(
                    send,
                    403,
                    {"error": {"code": "origin_rejected", "message": "Origin is not allowed.", "request_id": request_id}},
                )
                return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                raw = list(message.get("headers") or [])
                raw.extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"x-content-type-options", b"nosniff"),
                        (b"cache-control", b"no-store"),
                    ]
                )
                message["headers"] = raw
            await send(message)

        await self.app(scope, receive, send_wrapper)


async def _json(send, status: int, payload: dict) -> None:
    import json

    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


app = create_app()
