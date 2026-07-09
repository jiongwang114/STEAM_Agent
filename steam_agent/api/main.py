import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import HOST, PORT

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from ..rag.embedder import get_embedder
    from ..memory.insight_store import init_db
    from ..tracing import setup_langsmith

    logging.info("Setting up LangSmith tracing...")
    setup_langsmith()
    logging.info("Warming up embedding model...")
    get_embedder()
    logging.info("Initializing SQLite database...")
    init_db()
    from ..memory.game_profile import init_game_profile_table
    init_game_profile_table()
    logging.info("Steam Agent API ready.")
    yield


app = FastAPI(title="Steam Game Recommendation Agent", version="0.1.0", lifespan=lifespan)

from .routes import router

app.include_router(router)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
def health():
    return {"status": "ok"}


def main():
    import uvicorn

    uvicorn.run("steam_agent.api.main:app", host=HOST, port=PORT, reload=True)


if __name__ == "__main__":
    main()
