from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from steam_agent.memory import async_memory, insight_store, message_store, session_summary, thread_title


@pytest.fixture
def isolated_database(monkeypatch):
    descriptor, database_path = tempfile.mkstemp(
        prefix=".memory-test-", suffix=".db", dir=Path(__file__).resolve().parent
    )
    os.close(descriptor)
    monkeypatch.setattr(insight_store, "SQLITE_DB_PATH", database_path)
    monkeypatch.setattr(message_store, "SQLITE_DB_PATH", database_path)
    monkeypatch.setattr(session_summary, "SQLITE_DB_PATH", database_path)
    monkeypatch.setattr(thread_title, "SQLITE_DB_PATH", database_path)
    monkeypatch.setattr(async_memory, "SQLITE_DB_PATH", database_path)
    yield database_path
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(database_path + suffix).unlink(missing_ok=True)


def test_structured_memory_add_replace_delete_and_expiry(isolated_database):
    with pytest.raises(ValueError, match="memory_key is too long"):
        insight_store.save_insight(
            "alice", "value", "fact", memory_key="x" * 201, action="add"
        )

    first = insight_store.save_insight(
        "alice", "喜欢开放世界", "preference", memory_key="game_genres", action="add",
        scope="stable", ttl_days=2,
    )
    duplicate = insight_store.save_insight(
        "alice", "喜欢开放世界", "preference", memory_key="game_genres", action="add",
    )
    other = insight_store.save_insight(
        "alice", "喜欢策略游戏", "preference", memory_key="game_genres", action="add",
    )

    assert first["status"] == "inserted"
    assert duplicate["status"] == "deduplicated"
    assert other["status"] == "inserted"

    replaced = insight_store.save_insight(
        "alice", "现在喜欢解谜游戏", "preference", memory_key="game_genres", action="replace",
    )
    active = insight_store.get_insights("alice")
    assert replaced["status"] == "superseded"
    assert len(active) == 1
    assert active[0]["insight"] == "现在喜欢解谜游戏"
    assert active[0]["expires_at"] is None

    deleted = insight_store.save_insight(
        "alice", "", "preference", memory_key="game_genres", action="delete",
    )
    assert deleted["status"] == "deleted"
    assert insight_store.get_insights("alice") == []
    assert insight_store.get_insights("bob") == []

    temporary = insight_store.save_insight(
        "alice", "最近在玩模拟经营", "preference", memory_key="current_interest",
        action="add", scope="temporary",
    )
    assert temporary["status"] == "inserted"
    assert insight_store.get_insights("alice")[-1]["expires_at"] is not None


def test_archiving_enqueues_one_durable_memory_task(isolated_database):
    message_store.archive_sqlite_turn(
        "alice", "memory-task-thread", "帮我推荐解谜游戏", "可以看看这些游戏。"
    )

    claimed = async_memory.claim_memory_tasks()
    assert len(claimed) == 1
    assert claimed[0]["user_id"] == "alice"
    assert claimed[0]["turn_number"] == 1
    assert async_memory.claim_memory_tasks() == []


def test_memory_quality_gate_requires_user_evidence(isolated_database):
    accepted, review = async_memory._review_operations(
        "请记住我喜欢开放世界游戏",
        [{
            "memory_key": "game_genres",
            "value": "喜欢开放世界游戏",
            "category": "preference",
            "action": "add",
            "confidence": 0.95,
            "scope": "stable",
            "evidence": "我喜欢开放世界游戏",
        }],
    )
    assert len(accepted) == 1
    assert review["approved"] == 1

    rejected, review = async_memory._review_operations(
        "帮我推荐开放世界游戏",
        [{
            "memory_key": "game_genres",
            "value": "喜欢开放世界游戏",
            "category": "preference",
            "action": "add",
            "confidence": 0.99,
            "scope": "stable",
            "evidence": "开放世界游戏",
        }],
    )
    assert rejected == []
    assert review["reason"] == "no_explicit_memory_intent"


def test_memory_task_failure_is_retryable_and_processing_is_idempotent(isolated_database, monkeypatch):
    message_store.archive_sqlite_turn(
        "alice", "memory-retry-thread", "请记住我喜欢开放世界游戏", "好的，我会记住。"
    )

    def fail_extraction(*args, **kwargs):
        raise RuntimeError("temporary model outage")

    monkeypatch.setattr(async_memory, "extract_memory_operations", fail_extraction)
    first = async_memory.run_pending_memory_tasks()
    assert first[0]["status"] == "retry"
    task = async_memory.get_memory_task(first[0]["id"])
    assert task["attempts"] == 1
    assert task["status"] == "retry"

    monkeypatch.setattr(
        async_memory,
        "MEMORY_AGENT_RETRY_BASE_SECONDS",
        0,
    )
    task = async_memory.claim_memory_tasks(now=task["available_at"] + 1)[0]
    monkeypatch.setattr(
        async_memory,
        "extract_memory_operations",
        lambda *args, **kwargs: ([{
            "memory_key": "game_genres",
            "value": "喜欢开放世界游戏",
            "category": "preference",
            "action": "add",
            "confidence": 0.95,
            "scope": "stable",
            "ttl_days": None,
            "evidence": "我喜欢开放世界游戏",
        }], {"approved": 1, "rejected": 0}),
    )
    assert async_memory.process_memory_task(task) == "completed"
    assert insight_store.get_insights("alice")[0]["insight"] == "喜欢开放世界游戏"

    duplicate = insight_store.save_insight(
        "alice",
        "喜欢开放世界游戏",
        "preference",
        action="add",
        memory_key="game_genres",
    )
    assert duplicate["status"] == "deduplicated"
    assert async_memory.get_memory_task(task["id"])["status"] == "completed"


def test_conversation_archive_is_sqlite_only_and_user_scoped(isolated_database):
    from steam_agent.memory.archiver import archive_conversation

    archived = archive_conversation("alice", "thread-1", "之前说过什么？", "你说过喜欢解谜。")

    assert archived["status"] == "complete"
    assert archived["layers"] == {"sqlite": "ok"}
    assert message_store.thread_belongs_to_user("alice", "thread-1")
    assert not message_store.thread_belongs_to_user("bob", "thread-1")
    assert [item["role"] for item in message_store.get_messages_by_turn("alice", "thread-1", 1)] == [
        "user", "assistant"
    ]
    from steam_agent.tools.recall_message_detail import recall_message_detail
    assert len(recall_message_detail("alice", "thread-1", turn_number=1)["messages"]) == 2
    assert "error" in recall_message_detail("bob", "thread-1", turn_number=1)

    import sqlite3

    conn = sqlite3.connect(isolated_database)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "archive_tasks" not in tables


def test_execution_summary_is_persisted_without_internal_tool_details(isolated_database):
    from steam_agent.memory.archiver import archive_conversation

    archived = archive_conversation(
        "alice",
        "thread-execution",
        "推荐解谜游戏",
        "可以看看这些游戏。",
        execution={
            "status": "success",
            "duration_ms": 42,
            "steps": [{
                "name": "查找相似游戏",
                "status": "completed",
                "duration_ms": 21,
                "round": 1,
                "arguments": {"query": "不要持久化"},
                "result": {"games": ["不要持久化"]},
            }],
        },
    )

    assert archived["turn_number"] == 1
    messages = message_store.get_thread_messages("alice", "thread-execution")
    assert messages[0]["role"] == "user"
    assert "execution" not in messages[0]
    assert messages[1]["execution"] == {
        "status": "success",
        "duration_ms": 42,
        "steps": [{
            "name": "查找相似游戏",
            "status": "completed",
            "duration_ms": 21,
            "round": 1,
        }],
    }


def test_manual_thread_title_wins_over_claimed_auto_title(isolated_database):
    from steam_agent.memory import thread_title

    assert thread_title.claim_auto_title_generation("alice", "thread-title") is True
    thread_title.set_thread_title("alice", "thread-title", "手动标题")

    assert thread_title.auto_generate_title("alice", "thread-title", "新的问题") == "手动标题"
    assert thread_title.get_thread_title("alice", "thread-title") == "手动标题"


def test_session_summary_persistence_and_snapshot(isolated_database):
    saved = session_summary.save_session_summary(
        "alice", "thread-2", 1, 1, 3, "用户更偏好解谜游戏，预算不超过100元。"
    )

    assert saved["covered_to_turn"] == 3
    assert session_summary.get_latest_session_summary("alice", "thread-2")["summary"] == saved["summary"]
    assert session_summary.get_latest_session_summary("bob", "thread-2") is None


def test_missing_checkpoint_rebuilds_uncovered_raw_turns(isolated_database):
    import asyncio

    from steam_agent.api.routes import _restore_archived_history

    message_store.archive_sqlite_turn("alice", "thread-restore", "旧问题", "旧回答")
    message_store.archive_sqlite_turn("alice", "thread-restore", "新问题", "新回答")
    session_summary.save_session_summary(
        "alice", "thread-restore", 1, 1, 1, "第一轮摘要。"
    )

    class EmptyCheckpoint:
        async def aget_state(self, config):
            return type("Snapshot", (), {"values": {}})()

    state = {
        "messages": [HumanMessage(content="當前問題")],
        "user_id": "alice",
        "thread_id": "thread-restore",
    }
    restored = asyncio.run(_restore_archived_history(EmptyCheckpoint(), {}, state))

    assert [message.content for message in restored["messages"]] == [
        "新问题", "新回答", "當前問題"
    ]
    assert restored["conversation_summary"] == "第一轮摘要。"


def test_agent_compaction_persists_summary_before_removing_complete_turns(
    isolated_database, monkeypatch
):
    from steam_agent.graph import nodes

    monkeypatch.setattr(nodes, "AGENT_HISTORY_COMPRESSION_THRESHOLD", 0.2)

    class SummaryModel:
        def invoke(self, messages):
            return AIMessage(content="摘要：用户偏好解谜游戏。")

    monkeypatch.setattr(nodes, "build_llm", lambda **kwargs: SummaryModel())
    turns = []
    for turn in range(1, 5):
        turns.extend([
            HumanMessage(content=f"用户消息 {turn} " + "偏好 " * 50, id=f"human-{turn}"),
            AIMessage(content=f"助手回复 {turn} " + "回答 " * 50, id=f"assistant-{turn}"),
        ])

    dynamic, removals, update = nodes._prepare_model_context(
        {
            "messages": turns,
            "user_id": "alice",
            "thread_id": "thread-3",
            "conversation_summary": "",
            "summary_version": 0,
            "summary_covered_to_turn": 0,
            "usage": {},
            "model_history": [],
        },
        512,
    )

    assert update["context_stats"]["compression_triggered"] is True
    assert removals and all(isinstance(item, RemoveMessage) for item in removals)
    assert len(dynamic) < len(turns)
    from langgraph.graph.message import add_messages
    reduced = add_messages(turns, [*removals, AIMessage(content="当前回答", id="final-answer")])
    removed_ids = {item.id for item in removals}
    assert not any(message.id in removed_ids for message in reduced)
    assert len(reduced) == len(turns) - len(removals) + 1
    persisted = session_summary.get_latest_session_summary("alice", "thread-3")
    assert persisted["summary"] == "摘要：用户偏好解谜游戏。"
    assert persisted["covered_to_turn"] == len(removals) // 2


def test_memory_tool_schema_hides_authenticated_user_id():
    from steam_agent.tools.recall_message_detail import recall_message_detail
    from steam_agent.tools.user_insight import save_user_insight

    schema = convert_to_openai_tool(save_user_insight)["function"]["parameters"]
    recall_schema = convert_to_openai_tool(recall_message_detail)["function"]["parameters"]

    assert "user_id" not in schema["properties"]
    assert schema["required"] == ["memory_key"]
    assert "user_id" not in recall_schema["properties"]


def test_agent_tools_no_longer_expose_semantic_history_recall():
    from steam_agent.graph.nodes import get_all_tools

    names = {tool.__name__ for tool in get_all_tools()}

    assert "recall_user_memory" not in names
    assert "recall_message_detail" in names


def test_graph_compiles_with_a_checkpointer():
    from langgraph.checkpoint.memory import MemorySaver

    from steam_agent.graph.builder import _compile

    assert _compile(MemorySaver()) is not None


def test_system_prompt_uses_the_fixed_memory_snapshot():
    from steam_agent.prompts.system import build_system_prompt

    prompt = build_system_prompt(
        "alice",
        thread_id="thread-1",
        insights=[{
            "category": "preference",
            "normalized_key": "game_genres",
            "insight": "喜歡解謎遊戲",
        }],
        steam_profile="",
    )

    assert "[memory_key: game_genres]" in prompt.content
    assert "thread_id: thread-1" in prompt.content
