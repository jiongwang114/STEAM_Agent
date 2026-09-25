import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import (
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
from ..observability import (
    configure_logging,
    metrics,
    reset_request_id,
    set_request_id,
)

configure_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from ..rag.embedder import get_embedder
    from ..memory.insight_store import init_db
    from ..tracing import setup_langsmith

    logging.info("Setting up LangSmith tracing...")
    setup_langsmith()
    if MODEL_WARMUP_ON_STARTUP:
        logging.info("Warming up embedding model...")
        get_embedder()
    else:
        logging.info("Embedding model warmup deferred; readiness will report index state.")
    logging.info("Initializing SQLite database...")
    init_db()
    from ..memory.game_profile import init_game_profile_table
    init_game_profile_table()
    from ..memory.auth import init_auth_table
    init_auth_table()
    from ..memory.message_store import init_messages_table
    init_messages_table()
    from ..memory.thread_title import init_threads_table
    init_threads_table()
    from ..rag.vector_store import index_compatibility
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
    yield


app = FastAPI(title="Steam Game Recommendation Agent", version="0.1.0", lifespan=lifespan)


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
                    content={"code": "request_too_large", "message": "请求体超过大小限制"},
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
from .routes import router

app.include_router(router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
def health():
    """Liveness endpoint: no dependency calls and safe for container probes."""
    return {"status": "ok", "check": "live"}


@app.get("/ready")
def ready():
    """Readiness endpoint for operators and load balancers."""
    checks: dict[str, object] = {
        "config": bool(os.environ.get("DEEPSEEK_API_KEY") and os.environ.get("STEAM_API_KEY")),
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
        from chromadb import PersistentClient
        client = PersistentClient(path=CHROMA_PERSIST_DIR)
        client.list_collections()
        checks["chroma"] = True
    except Exception:
        pass
    manifest_path = Path(CHROMA_PERSIST_DIR) / "index_manifest.json"
    if manifest_path.exists():
        try:
            manifest = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
            checks["index_manifest"] = (
                manifest.get("embedding_model") == EMBEDDING_MODEL
                and manifest.get("reranker_model") == RERANKER_MODEL
            )
        except Exception:
            pass
    ready_status = all(bool(value) for value in checks.values())
    payload = {"status": "ready" if ready_status else "not_ready", "checks": checks}
    return JSONResponse(status_code=200 if ready_status else 503, content=payload)


@app.get("/metrics")
def runtime_metrics(request: Request):
    """Return a dependency-free operational snapshot for demos and monitoring."""
    if METRICS_TOKEN and request.headers.get("X-Metrics-Token") != METRICS_TOKEN:
        return JSONResponse(
            status_code=401,
            content={"code": "metrics_auth_required", "message": "metrics access denied"},
        )
    return metrics.snapshot()


def main():
    import uvicorn

    uvicorn.run("steam_agent.api.main:app", host=HOST, port=PORT, reload=True)


if __name__ == "__main__":
    main()
