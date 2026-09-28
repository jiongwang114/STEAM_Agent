from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def isolated_api_database(monkeypatch):
    from steam_agent.memory import async_memory, auth, insight_store, message_store, session_summary, thread_title
    from steam_agent import config

    descriptor, database_path = tempfile.mkstemp(
        prefix=".api-test-", suffix=".db", dir=Path(__file__).resolve().parent
    )
    os.close(descriptor)
    for module in (auth, insight_store, message_store, session_summary, thread_title, async_memory):
        monkeypatch.setattr(module, "SQLITE_DB_PATH", database_path)
    monkeypatch.setattr(config, "SQLITE_DB_PATH", database_path)
    monkeypatch.setattr(config, "CHECKPOINT_DB_PATH", database_path + ".checkpoints")
    yield database_path
    for suffix in ("", "-wal", "-shm", "-journal", ".checkpoints", ".checkpoints-wal", ".checkpoints-shm"):
        Path(database_path + suffix).unlink(missing_ok=True)
