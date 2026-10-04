import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR: Path = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- LLM ---
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "deepseek").lower()
CUSTOM_LLM_API_KEY: str = os.environ.get("CUSTOM_LLM_API_KEY", "")
CUSTOM_LLM_BASE_URL: str = os.environ.get(
    "CUSTOM_LLM_BASE_URL", "https://codex.wlbclub.com"
)
CUSTOM_LLM_MODEL: str = os.environ.get("CUSTOM_LLM_MODEL", "gpt-6-luna")
CUSTOM_LLM_REASONING_EFFORT: str = os.environ.get(
    "CUSTOM_LLM_REASONING_EFFORT", "low"
).lower()
if CUSTOM_LLM_REASONING_EFFORT not in {"low", "medium", "high"}:
    raise ValueError("CUSTOM_LLM_REASONING_EFFORT must be low, medium, or high")

if LLM_PROVIDER == "custom":
    if not CUSTOM_LLM_API_KEY:
        raise RuntimeError("CUSTOM_LLM_API_KEY is required when LLM_PROVIDER=custom")
    DEEPSEEK_API_KEY: str = CUSTOM_LLM_API_KEY
    DEEPSEEK_BASE_URL: str = CUSTOM_LLM_BASE_URL
    # Custom provider has one canonical model setting; do not require LLM_MODEL.
    LLM_MODEL: str = CUSTOM_LLM_MODEL
else:
    DEEPSEEK_API_KEY: str = os.environ["DEEPSEEK_API_KEY"]
    DEEPSEEK_BASE_URL: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    LLM_MODEL: str = os.environ.get("LLM_MODEL", "deepseek-chat")
if LLM_PROVIDER == "custom":
    LLM_FAST_MODEL: str = LLM_MODEL
    LLM_CANDIDATE_MODEL: str = LLM_MODEL
else:
    LLM_FAST_MODEL: str = os.environ.get("LLM_FAST_MODEL", LLM_MODEL)
    LLM_CANDIDATE_MODEL: str = os.environ.get("LLM_CANDIDATE_MODEL", LLM_MODEL)
LLM_TEMPERATURE: float = float(os.environ.get("LLM_TEMPERATURE", "0.3"))
LLM_MAX_TOKENS: int = int(os.environ.get("LLM_MAX_TOKENS", "2048"))
LLM_REQUEST_TIMEOUT_SECONDS: float = float(
    os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", "45")
)
LLM_MAX_RETRIES: int = int(os.environ.get("LLM_MAX_RETRIES", "2"))
LLM_INPUT_CNY_PER_MILLION: float = float(
    os.environ.get("LLM_INPUT_CNY_PER_MILLION", "0")
)
LLM_OUTPUT_CNY_PER_MILLION: float = float(
    os.environ.get("LLM_OUTPUT_CNY_PER_MILLION", "0")
)
AGENT_EXPERIMENT_NAME: str = os.environ.get("AGENT_EXPERIMENT_NAME", "")
AGENT_EXPERIMENT_CANDIDATE_PERCENT: int = int(
    os.environ.get("AGENT_EXPERIMENT_CANDIDATE_PERCENT", "0")
)

# --- Agent execution budget ---
AGENT_MAX_TOOL_ROUNDS: int = int(os.environ.get("AGENT_MAX_TOOL_ROUNDS", "8"))
AGENT_MAX_TOOL_CALLS_PER_ROUND: int = int(
    os.environ.get("AGENT_MAX_TOOL_CALLS_PER_ROUND", "5")
)
AGENT_MAX_TOTAL_TOKENS: int = int(os.environ.get("AGENT_MAX_TOTAL_TOKENS", "24000"))
AGENT_MAX_WALL_SECONDS: float = float(os.environ.get("AGENT_MAX_WALL_SECONDS", "120"))
AGENT_FINALIZE_MAX_TOKENS: int = int(os.environ.get("AGENT_FINALIZE_MAX_TOKENS", "512"))
AGENT_MAX_REPAIR_ATTEMPTS: int = int(os.environ.get("AGENT_MAX_REPAIR_ATTEMPTS", "2"))
AGENT_HISTORY_TOKEN_BUDGET: int = int(
    os.environ.get("AGENT_HISTORY_TOKEN_BUDGET", "6000")
)
AGENT_CONTEXT_WINDOW_TOKENS: int = int(
    os.environ.get("AGENT_CONTEXT_WINDOW_TOKENS", "64000")
)
AGENT_TOOL_CONTEXT_RESERVE_TOKENS: int = int(
    os.environ.get("AGENT_TOOL_CONTEXT_RESERVE_TOKENS", "4096")
)
AGENT_HISTORY_COMPRESSION_THRESHOLD: float = float(
    os.environ.get("AGENT_HISTORY_COMPRESSION_THRESHOLD", "0.90")
)
AGENT_SUMMARY_MAX_TOKENS: int = int(
    os.environ.get("AGENT_SUMMARY_MAX_TOKENS", "512")
)
MEMORY_SNAPSHOT_MAX_ITEMS: int = int(
    os.environ.get("MEMORY_SNAPSHOT_MAX_ITEMS", "12")
)
MEMORY_SNAPSHOT_MAX_CHARS: int = int(
    os.environ.get("MEMORY_SNAPSHOT_MAX_CHARS", "1800")
)
MEMORY_AGENT_ENABLED: bool = (
    os.environ.get("MEMORY_AGENT_ENABLED", "true").lower() == "true"
)
MEMORY_AGENT_POLL_SECONDS: float = float(
    os.environ.get("MEMORY_AGENT_POLL_SECONDS", "15")
)
MEMORY_AGENT_BATCH_SIZE: int = int(os.environ.get("MEMORY_AGENT_BATCH_SIZE", "5"))
MEMORY_AGENT_MAX_ATTEMPTS: int = int(
    os.environ.get("MEMORY_AGENT_MAX_ATTEMPTS", "5")
)
MEMORY_AGENT_LEASE_SECONDS: float = float(
    os.environ.get("MEMORY_AGENT_LEASE_SECONDS", "300")
)
MEMORY_AGENT_RETRY_BASE_SECONDS: float = float(
    os.environ.get("MEMORY_AGENT_RETRY_BASE_SECONDS", "30")
)
MEMORY_AGENT_MIN_CONFIDENCE: float = float(
    os.environ.get("MEMORY_AGENT_MIN_CONFIDENCE", "0.85")
)
TOOL_TIMEOUT_SECONDS: float = float(os.environ.get("TOOL_TIMEOUT_SECONDS", "20"))
TOOL_MAX_RETRIES: int = int(os.environ.get("TOOL_MAX_RETRIES", "2"))
STEAM_PROFILE_WARMUP_TIMEOUT_SECONDS: float = float(
    os.environ.get("STEAM_PROFILE_WARMUP_TIMEOUT_SECONDS", "5")
)
TOOL_CIRCUIT_FAILURE_THRESHOLD: int = int(
    os.environ.get("TOOL_CIRCUIT_FAILURE_THRESHOLD", "3")
)
TOOL_CIRCUIT_COOLDOWN_SECONDS: float = float(
    os.environ.get("TOOL_CIRCUIT_COOLDOWN_SECONDS", "30")
)
TOOL_RESULT_MAX_CHARS: int = int(os.environ.get("TOOL_RESULT_MAX_CHARS", "12000"))
if AGENT_MAX_TOOL_ROUNDS < 1 or AGENT_MAX_TOOL_CALLS_PER_ROUND < 1:
    raise ValueError("Agent tool budgets must be positive integers")
if AGENT_MAX_TOTAL_TOKENS < 1 or AGENT_MAX_WALL_SECONDS <= 0:
    raise ValueError("Agent token and wall-clock budgets must be positive")
if AGENT_HISTORY_TOKEN_BUDGET < 512 or TOOL_RESULT_MAX_CHARS < 1000:
    raise ValueError("Agent history and tool result budgets are too small")
if AGENT_CONTEXT_WINDOW_TOKENS < 4096 or AGENT_TOOL_CONTEXT_RESERVE_TOKENS < 0:
    raise ValueError("Agent context window or tool reserve is invalid")
if not 0 < AGENT_HISTORY_COMPRESSION_THRESHOLD <= 1:
    raise ValueError("AGENT_HISTORY_COMPRESSION_THRESHOLD must be between 0 and 1")
if AGENT_SUMMARY_MAX_TOKENS < 64:
    raise ValueError("AGENT_SUMMARY_MAX_TOKENS is too small")
if MEMORY_SNAPSHOT_MAX_ITEMS < 1 or MEMORY_SNAPSHOT_MAX_CHARS < 200:
    raise ValueError("Memory snapshot limits are too small")
if (
    MEMORY_AGENT_POLL_SECONDS <= 0
    or MEMORY_AGENT_BATCH_SIZE < 1
    or MEMORY_AGENT_MAX_ATTEMPTS < 1
    or MEMORY_AGENT_LEASE_SECONDS <= 0
    or MEMORY_AGENT_RETRY_BASE_SECONDS <= 0
):
    raise ValueError("Memory agent queue settings are invalid")
if not 0 < MEMORY_AGENT_MIN_CONFIDENCE <= 1:
    raise ValueError("MEMORY_AGENT_MIN_CONFIDENCE must be between 0 and 1")
if TOOL_TIMEOUT_SECONDS <= 0 or TOOL_MAX_RETRIES < 0:
    raise ValueError("Tool timeout must be positive and retries cannot be negative")
if STEAM_PROFILE_WARMUP_TIMEOUT_SECONDS <= 0:
    raise ValueError("Steam profile warmup timeout must be positive")
if TOOL_CIRCUIT_FAILURE_THRESHOLD < 1 or TOOL_CIRCUIT_COOLDOWN_SECONDS <= 0:
    raise ValueError("Tool circuit-breaker settings must be positive")
if LLM_REQUEST_TIMEOUT_SECONDS <= 0 or LLM_MAX_RETRIES < 0:
    raise ValueError("LLM timeout must be positive and retries cannot be negative")
if LLM_INPUT_CNY_PER_MILLION < 0 or LLM_OUTPUT_CNY_PER_MILLION < 0:
    raise ValueError("LLM token prices cannot be negative")
if not 0 <= AGENT_EXPERIMENT_CANDIDATE_PERCENT <= 100:
    raise ValueError("AGENT_EXPERIMENT_CANDIDATE_PERCENT must be between 0 and 100")

# --- Steam ---
STEAM_API_KEY: str = os.environ["STEAM_API_KEY"]
STEAM_API_URL: str = "https://api.steampowered.com"
STEAM_STORE_URL: str = "https://store.steampowered.com/api"

# --- Embedding ---
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
EMBEDDING_REVISION: str = os.environ.get("EMBEDDING_REVISION", "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a" if EMBEDDING_MODEL == "BAAI/bge-base-en-v1.5" else "")
# Read-only cleanup target for existing user-memory vectors; no new vectors are written.
LEGACY_MEMORY_COLLECTION_NAME: str = os.environ.get(
    "MEMORY_COLLECTION_NAME", "user_memory_v2"
)
RERANKER_MODEL: str = os.environ.get(
    "RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
)
RERANKER_ENABLED: bool = os.environ.get("RERANKER_ENABLED", "true").lower() == "true"
RERANKER_REVISION: str = os.environ.get("RERANKER_REVISION", "233902d25c440f23af6f7d6e94d2946bac0bee0a" if RERANKER_MODEL == "cross-encoder/ms-marco-MiniLM-L-6-v2" else "")
RAG_DENSE_CANDIDATES: int = int(os.environ.get("RAG_DENSE_CANDIDATES", "100"))
RAG_LEXICAL_CANDIDATES: int = int(os.environ.get("RAG_LEXICAL_CANDIDATES", "100"))
RAG_RERANK_CANDIDATES: int = int(os.environ.get("RAG_RERANK_CANDIDATES", "50"))
RAG_RERANK_WEIGHT: float = float(os.environ.get("RAG_RERANK_WEIGHT", "0.50"))
if not 0 <= RAG_RERANK_WEIGHT <= 1:
    raise ValueError("RAG_RERANK_WEIGHT must be between 0 and 1")

# --- Chroma ---
CHROMA_PERSIST_DIR: str = os.environ.get(
    "CHROMA_PERSIST_DIR",
    str(BASE_DIR / "rag" / "chroma_data"),
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
SESSION_COOKIE_NAME: str = os.environ.get("SESSION_COOKIE_NAME", "steam_session")
SESSION_TTL_SECONDS: int = int(os.environ.get("SESSION_TTL_SECONDS", str(7 * 24 * 3600)))
SESSION_COOKIE_SECURE: bool = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_COOKIE_SAMESITE: str = os.environ.get("SESSION_COOKIE_SAMESITE", "lax").lower()
AUTH_RATE_LIMIT_WINDOW_SECONDS: int = int(
    os.environ.get("AUTH_RATE_LIMIT_WINDOW_SECONDS", "900")
)
AUTH_RATE_LIMIT_MAX_FAILURES: int = int(
    os.environ.get("AUTH_RATE_LIMIT_MAX_FAILURES", "10")
)
MAX_REQUEST_BODY_BYTES: int = int(os.environ.get("MAX_REQUEST_BODY_BYTES", "65536"))
RAG_DEFAULT_GAME_COUNT: int = int(os.environ.get("RAG_DEFAULT_GAME_COUNT", "1000"))
METRICS_TOKEN: str = os.environ.get("METRICS_TOKEN", "")
MODEL_WARMUP_ON_STARTUP: bool = (
    os.environ.get("MODEL_WARMUP_ON_STARTUP", "false").lower() == "true"
)

if SESSION_TTL_SECONDS < 300:
    raise ValueError("SESSION_TTL_SECONDS must be at least 300 seconds")
if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise ValueError("SESSION_COOKIE_SAMESITE must be lax, strict, or none")
if AUTH_RATE_LIMIT_WINDOW_SECONDS < 60 or AUTH_RATE_LIMIT_MAX_FAILURES < 1:
    raise ValueError("Authentication rate-limit settings are invalid")
if MAX_REQUEST_BODY_BYTES < 1024:
    raise ValueError("MAX_REQUEST_BODY_BYTES is too small")
if RAG_DEFAULT_GAME_COUNT < 1:
    raise ValueError("RAG_DEFAULT_GAME_COUNT must be positive")
