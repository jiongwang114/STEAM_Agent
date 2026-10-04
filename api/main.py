import logging
import os
import sqlite3
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from config import (
    CHROMA_PERSIST_DIR,
    EMBEDDING_MODEL,
    HOST,
    MAX_REQUEST_BODY_BYTES,
    METRICS_TOKEN,
    MODEL_WARMUP_ON_STARTUP,
    PORT,
    RERANKER_MODEL,
    SQLITE_DB_PATH,
)
from observability import (
    configure_logging,
    get_request_id,
    metrics,
    reset_request_id,
    set_request_id,
)

configure_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "night-museum-frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from rag.embedder import get_embedder
    from memory.insight_store import init_db
    from tracing import setup_langsmith

    logging.info("Setting up LangSmith tracing...")
    setup_langsmith()
    if MODEL_WARMUP_ON_STARTUP:
        logging.info("Warming up embedding model...")
        get_embedder()
    else:
        logging.info("Embedding model warmup deferred; readiness will report index state.")
    logging.info("Initializing SQLite database...")
    init_db()
    from memory.game_profile import init_game_profile_table
    init_game_profile_table()
    from memory.auth import init_auth_table
    init_auth_table()
    from memory.message_store import init_messages_table
    init_messages_table()
    from memory.session_summary import init_session_summaries_table
    init_session_summaries_table()
    from memory.async_memory import init_memory_tasks_table
    init_memory_tasks_table()
    from memory.thread_title import init_threads_table
    init_threads_table()
    from rag.vector_store import index_compatibility
    compatibility = index_compatibility()
    if not compatibility.get("compatible"):
        logging.warning(
            "rag_index_not_compatible",
            extra={"fields": {
                "status": compatibility.get("status"),
                "reason": compatibility.get("reason", ""),
                "mismatches": compatibility.get("mismatches", {}),
            }},
        )
    logging.info("Steam Agent API ready.")

    from memory.message_store import retry_pending_thread_deletions
    try:
        outcomes = await asyncio.to_thread(retry_pending_thread_deletions)
        failed = sum(item["status"] == "partial" for item in outcomes)
        if failed:
            logging.warning("thread_cleanup_retry_incomplete", extra={"fields": {"failed": failed}})
    except Exception as exc:
        logging.warning(
            "thread_cleanup_retry_failed",
            extra={"fields": {"error_type": type(exc).__name__}},
        )

    async def cleanup_retry_worker():
        while True:
            await asyncio.sleep(30)
            try:
                outcomes = await asyncio.to_thread(retry_pending_thread_deletions)
                failed = sum(item["status"] == "partial" for item in outcomes)
                if failed:
                    logging.warning(
                        "thread_cleanup_retry_incomplete",
                        extra={"fields": {"failed": failed}},
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logging.warning(
                    "thread_cleanup_retry_failed",
                    extra={"fields": {"error_type": type(exc).__name__}},
                )

    cleanup_task = asyncio.create_task(cleanup_retry_worker())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Steam Game Recommendation Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "null",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:4174",
        "http://127.0.0.1:4174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code", "http_error"))
        message = str(detail.get("message", "请求失败"))
        extra = detail.get("details")
    else:
        code = "http_error"
        message = str(detail)
        extra = None
    error = {"code": code, "message": message}
    if extra is not None:
        error["details"] = extra
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={"error": error, "request_id": get_request_id()},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = [
        {
            "location": item.get("loc", []),
            "message": item.get("msg", "invalid value"),
            "type": item.get("type", ""),
        }
        for item in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_request",
                "message": "请求参数无效",
                "details": errors,
            },
            "request_id": get_request_id(),
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    logger.exception(
        "unhandled_request_error",
        extra={"fields": {"error_type": type(exc).__name__}},
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {"code": "internal_error", "message": "服务器内部错误"},
            "request_id": get_request_id(),
        },
    )


@app.middleware("http")
async def observe_request(request: Request, call_next):
    request_id, context_token = set_request_id(request.headers.get("X-Request-ID"))
    started = time.perf_counter()
    status_code = 500
    try:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                oversized = int(content_length) > MAX_REQUEST_BODY_BYTES
            except ValueError:
                oversized = True
            if oversized:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "request_too_large",
                            "message": "请求体超过大小限制",
                        },
                        "request_id": request_id,
                    },
                )
                response.headers["X-Request-ID"] = request_id
                status_code = response.status_code
                return response
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration = time.perf_counter() - started
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        metrics.increment(
            "http_requests_total",
            method=request.method,
            route=route_path,
            status=str(status_code),
        )
        metrics.observe(
            "http_request_duration_seconds",
            duration,
            method=request.method,
            route=route_path,
        )
        logger.info(
            "request_completed",
            extra={"fields": {
                "method": request.method,
                "route": route_path,
                "status_code": status_code,
                "duration_ms": round(duration * 1000, 2),
            }},
        )
        reset_request_id(context_token)
from api.routes import router

app.include_router(router)


@app.get("/health")
def health():
    """Liveness endpoint: no dependency calls and safe for container probes."""
    return {"status": "ok", "check": "live"}


@app.get("/ready")
def ready():
    """Readiness endpoint for operators and load balancers."""
    checks: dict[str, object] = {
        "config": bool(
            os.environ.get("STEAM_API_KEY")
            and (
                os.environ.get("DEEPSEEK_API_KEY")
                if os.environ.get("LLM_PROVIDER", "deepseek").lower() != "custom"
                else os.environ.get("CUSTOM_LLM_API_KEY")
            )
        ),
        "sqlite": False,
        "chroma": False,
        "index_manifest": False,
    }
    try:
        conn = sqlite3.connect(SQLITE_DB_PATH, timeout=2.0)
        conn.execute("SELECT 1")
        conn.close()
        checks["sqlite"] = True
    except Exception:
        pass
    try:
        from rag.vector_store import _get_client, current_games_collection_name, index_manifest

        # Reuse the process-wide client. Opening a second PersistentClient for
        # the same Chroma SQLite file can fail after agent requests initialize
        # the vector store in this process.
        client = _get_client()
        manifest = index_manifest()
        collection = client.get_collection(current_games_collection_name(), embedding_function=None)
        checks["chroma"] = collection.count() == manifest.get("vector_count", manifest.get("game_count", 0)) and collection.count() > 0
    except Exception:
        pass
    manifest_path = Path(CHROMA_PERSIST_DIR) / "index_manifest.json"
    if manifest_path.exists():
        try:
            from rag.vector_store import index_compatibility
            checks["index_manifest"] = index_compatibility()["compatible"]
        except Exception:
            pass
    ready_status = all(bool(value) for value in checks.values())
    payload = {"status": "ready" if ready_status else "not_ready", "checks": checks}
    return JSONResponse(status_code=200 if ready_status else 503, content=payload)


@app.get("/metrics")
def runtime_metrics(request: Request):
    """Return a dependency-free operational snapshot for demos and monitoring."""
    if METRICS_TOKEN and request.headers.get("X-Metrics-Token") != METRICS_TOKEN:
        raise HTTPException(
            status_code=401,
            detail={"code": "metrics_auth_required", "message": "metrics access denied"},
        )
    return metrics.snapshot()


# Keep API routes ahead of the catch-all static mount.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="frontend")


def main():
    import uvicorn

    # SQLite, Chroma and checkpoint files live below the watched project tree.
    # Auto-reload would restart the service on normal runtime writes.
    uvicorn.run("api.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
