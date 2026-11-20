import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import agent, auth, jobs, profiles
from .config import settings
from .db import engine
from .evaluations import api as evaluation
from .observability import telemetry
from .queueing import redis


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


class UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = profiles.PDF_LIMIT + 65536 if scope["path"] == "/resumes/upload" else 256 * 1024
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > limit:
                raise HTTPException(413, "Request exceeds upload limit")
            return message

        headers = dict(scope.get("headers", []))
        try:
            too_large = int(headers.get(b"content-length", b"0")) > limit
        except ValueError:
            too_large = True
        if too_large:
            await JSONResponse({"detail": "Request exceeds upload limit"}, 413)(scope, receive, send)
            return
        await self.app(scope, bounded_receive, send)


def rate_limit(request, bucket, limit):
    if settings().environment == "test":
        return
    host = request.client.host if request.client else "unknown"
    try:
        key = f"careerlens:rate:{bucket}:{host}:{int(datetime.now(UTC).timestamp()) // 60}"
        with redis().pipeline() as pipeline:
            count, _ = pipeline.incr(key).expire(key, 120).execute()
        if count > limit:
            raise HTTPException(
                429, "Too many requests. Try again in a minute.", headers={"Retry-After": "60"}
            )
    except RedisError:
        raise HTTPException(503, "Rate limiter unavailable") from None


@asynccontextmanager
async def lifespan(app):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app")
    logger.addHandler(handler)
    try:
        yield
    finally:
        telemetry.flush()
        logger.removeHandler(handler)


app = FastAPI(title="CareerLens", version="0.1.0", lifespan=lifespan)
app.state.rate_limit = rate_limit
app.add_middleware(UploadLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings().allowed_origins,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)
for router in (auth.router, jobs.router, profiles.router, agent.router, evaluation.router):
    app.include_router(router)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    return JSONResponse(
        {"detail": [{"loc": item["loc"], "type": item["type"]} for item in exc.errors()]}, 422
    )


@app.exception_handler(SQLAlchemyError)
async def db_error(request, exc):
    logging.getLogger("app").error("database_operation_failed")
    return JSONResponse({"detail": "Database operation failed"}, 503)


@app.get("/health", tags=["operations"])
def health():
    return {"status": "ok"}


@app.get("/ready", tags=["operations"])
def ready():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM alembic_version"))
        redis().ping()
    except (SQLAlchemyError, RedisError):
        raise HTTPException(503, "Storage is unavailable") from None
    return {"status": "ready"}
