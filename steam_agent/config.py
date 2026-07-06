import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR: Path = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- LLM ---
DEEPSEEK_API_KEY: str = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_BASE_URL: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_TEMPERATURE: float = float(os.environ.get("LLM_TEMPERATURE", "0.3"))
LLM_MAX_TOKENS: int = int(os.environ.get("LLM_MAX_TOKENS", "2048"))

# --- Steam ---
STEAM_API_KEY: str = os.environ["STEAM_API_KEY"]
STEAM_API_URL: str = "https://api.steampowered.com"
STEAM_STORE_URL: str = "https://store.steampowered.com/api"

# --- Embedding ---
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")

# --- Chroma ---
CHROMA_PERSIST_DIR: str = os.environ.get(
    "CHROMA_PERSIST_DIR",
    str(BASE_DIR / "rag" / "data"),
)

# --- SQLite ---
SQLITE_DB_PATH: str = os.environ.get(
    "SQLITE_DB_PATH",
    str(BASE_DIR / "data.db"),
)

# --- LangSmith ---
LANGCHAIN_TRACING_V2: str = os.environ.get("LANGCHAIN_TRACING_V2", "false")
LANGCHAIN_API_KEY: str = os.environ.get("LANGCHAIN_API_KEY", "")
LANGCHAIN_PROJECT: str = os.environ.get("LANGCHAIN_PROJECT", "steam-agent")

# --- LangGraph ---
CHECKPOINT_DB_PATH: str = os.environ.get(
    "CHECKPOINT_DB_PATH",
    str(BASE_DIR / "checkpoints.db"),
)

# --- Server ---
HOST: str = os.environ.get("HOST", "0.0.0.0")
PORT: int = int(os.environ.get("PORT", "8000"))
