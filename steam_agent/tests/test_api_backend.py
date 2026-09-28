from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage


@pytest.fixture
def api_client(isolated_api_database):
    from steam_agent.api.main import app
    from steam_agent.api.security import require_user

    app.dependency_overrides[require_user] = lambda: "alice"
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_authentication_failure_uses_error_contract(api_client):
    from steam_agent.api.main import app

    app.dependency_overrides.clear()
    response = api_client.get("/auth/user-info", headers={"X-Request-ID": "auth-test"})

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "authentication_required", "message": "请先登录"},
        "request_id": "auth-test",
    }


def test_title_and_message_endpoints_enforce_thread_owner(api_client):
    from steam_agent.memory.message_store import archive_sqlite_turn

    archive_sqlite_turn("bob", "bob-thread", "question", "answer")
    archive_sqlite_turn("alice", "alice-thread", "question", "answer")

    foreign_messages = api_client.get("/messages", params={"thread_id": "bob-thread"})
    foreign_title = api_client.post(
        "/thread-title", json={"thread_id": "bob-thread", "title": "stolen"}
    )
    own_title = api_client.post(
        "/thread-title", json={"thread_id": "alice-thread", "title": "owned"}
    )

    assert foreign_messages.status_code == 404
    assert foreign_messages.json()["error"]["code"] == "thread_not_found"
    assert foreign_title.status_code == 404
    assert own_title.status_code == 200


def test_sync_and_stream_use_same_authenticated_agent_execution(api_client, monkeypatch):
    from steam_agent.api import routes

    graphs = []

    class FakeGraph:
        def __init__(self):
            self.state = None
            self.initial_state = None

        async def aget_state(self, config):
            if self.state is not None:
                return SimpleNamespace(values=self.state)
            return SimpleNamespace(values={"messages": [HumanMessage(content="checkpoint")]})

        async def astream_events(self, initial_state, config, version):
            self.initial_state = initial_state
            self.state = {
                **initial_state,
                "messages": [*initial_state["messages"], AIMessage(content="统一回答")],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            }
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": AIMessageChunk(content="统一回答")},
            }

    async def fake_get_graph():
        graph = FakeGraph()
        graphs.append(graph)
        return graph

    monkeypatch.setattr(routes, "_get_graph", fake_get_graph)
    monkeypatch.setattr(routes, "_maybe_generate_title", lambda *args: None)

    sync_response = api_client.post(
        "/chat", json={"thread_id": "sync-thread", "message": "hello", "user_id": "bob"}
    )
    stream_response = api_client.post(
        "/chat/stream", json={"thread_id": "stream-thread", "message": "hello", "user_id": "bob"}
    )
    stream_events = [
        json.loads(line[6:])
        for line in stream_response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert sync_response.status_code == 200
    assert sync_response.json()["status"] == "success"
    assert sync_response.json()["reply"] == "统一回答"
    assert sync_response.json()["token_usage"]["total_tokens"] == 5
    assert stream_response.status_code == 200
    assert [event["event"] for event in stream_events] == ["token", "done"]
    assert stream_events[-1]["data"]["reply"] == sync_response.json()["reply"]
    assert [graph.initial_state["user_id"] for graph in graphs] == ["alice", "alice"]


def test_first_turn_title_is_scheduled_before_agent_execution(api_client, monkeypatch):
    from steam_agent.api import routes

    order = []

    monkeypatch.setattr(
        routes,
        "_schedule_title_generation",
        lambda *args: order.append("title"),
    )

    async def fake_execute(*args, **kwargs):
        order.append("agent")
        yield {
            "event": "done",
            "data": {
                "status": "success",
                "thread_id": "first-turn",
                "reply": "回答",
                "execution": {"status": "success", "duration_ms": 1, "steps": []},
                "run_metadata": {},
            },
        }

    monkeypatch.setattr(routes, "_execute_agent_events", fake_execute)
    response = api_client.post(
        "/chat", json={"thread_id": "first-turn", "message": "第一句话"}
    )

    assert response.status_code == 200
    assert order == ["title", "agent"]


def test_execution_response_is_safe_and_history_is_replayable(api_client, monkeypatch):
    from steam_agent.api import routes

    class GraphWithToolHistory:
        async def aget_state(self, config):
            return SimpleNamespace(
                values=getattr(self, "state", {"messages": [HumanMessage(content="checkpoint")]})
            )

        async def astream_events(self, initial_state, config, version):
            self.state = {
                **initial_state,
                "messages": [*initial_state["messages"], AIMessage(content="推荐结果")],
                "tool_history": [{
                    "tool": "search_steam_store",
                    "status": "success",
                    "duration_seconds": 0.012,
                    "round": 1,
                    "arguments": {"query": "secret"},
                    "result": {"price": "secret"},
                }],
            }
            yield {
                "event": "on_tool_start",
                "name": "search_steam_store",
                "metadata": {},
                "data": {},
            }
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "agent"},
                "data": {"chunk": AIMessageChunk(content="推荐结果")},
            }

    graph = GraphWithToolHistory()

    async def fake_get_graph():
        return graph

    monkeypatch.setattr(routes, "_get_graph", fake_get_graph)
    monkeypatch.setattr(routes, "_schedule_title_generation", lambda *args: None)

    response = api_client.post(
        "/chat", json={"thread_id": "execution-history", "message": "查商店"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["execution"]["steps"] == [{
        "name": "查询 Steam 商店",
        "status": "completed",
        "duration_ms": 12,
        "round": 1,
    }]
    assert "tool_history" not in body["run_metadata"]
    assert "secret" not in response.text
    history = api_client.get("/messages", params={"thread_id": "execution-history"})
    assert history.status_code == 200
    assert history.json()["messages"][1]["execution"] == body["execution"]


def test_agent_failure_is_reported_as_degraded(api_client, monkeypatch):
    from steam_agent.api import routes

    class FailingGraph:
        async def aget_state(self, config):
            return SimpleNamespace(values={"messages": [HumanMessage(content="checkpoint")]})

        async def astream_events(self, initial_state, config, version):
            raise RuntimeError("model unavailable")
            yield {}

    async def fake_get_graph():
        return FailingGraph()

    monkeypatch.setattr(routes, "_get_graph", fake_get_graph)
    monkeypatch.setattr(routes, "_maybe_generate_title", lambda *args: None)

    response = api_client.post(
        "/chat", json={"thread_id": "failed-thread", "message": "hello"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["run_metadata"]["termination_reason"] == "model_error"
    assert "暂时不可用" in response.json()["reply"]


def test_request_validation_uses_standard_error_shape(api_client):
    response = api_client.post("/chat", json={"thread_id": "thread-only"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_delete_cleanup_is_durable_and_manually_retryable(api_client, monkeypatch):
    from steam_agent.memory import async_memory, message_store, session_summary

    message_store.archive_sqlite_turn("alice", "cleanup-thread", "q", "a")
    original_delete = session_summary.delete_session_summaries
    attempts = 0

    def fail_once(user_id, thread_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary storage failure")
        original_delete(user_id, thread_id)

    monkeypatch.setattr(session_summary, "delete_session_summaries", fail_once)
    from steam_agent.rag import vector_store

    monkeypatch.setattr(vector_store, "get_legacy_user_memory_collection", lambda: None)

    deleted = api_client.delete("/threads", params={"thread_id": "cleanup-thread"})
    blocked_chat = api_client.post(
        "/chat", json={"thread_id": "cleanup-thread", "message": "still cleaning"}
    )
    pending = api_client.post("/threads/cleanup-thread/cleanup/retry")

    assert deleted.status_code == 202
    assert deleted.json()["layers"]["session_summaries"].startswith("error:")
    assert blocked_chat.status_code == 409
    assert blocked_chat.json()["error"]["code"] == "thread_cleanup_pending"
    assert pending.status_code == 200
    assert pending.json()["status"] == "ok"
    assert message_store.get_thread_messages("alice", "cleanup-thread") == []
    assert message_store.retry_thread_cleanup("alice", "cleanup-thread") is None
    assert async_memory.claim_memory_tasks() == []


def test_steam_binding_and_profile_fact_share_one_transaction(
    isolated_api_database, monkeypatch
):
    from steam_agent.memory import auth, insight_store

    assert auth.register("alice", "password-123")[0]
    assert auth.bind_steam_id("alice", "76561198000000001")[0]
    assert any(
        item["normalized_key"] == "fact:steam_id"
        for item in insight_store.get_insights("alice")
    )

    def fail_sync(conn, user_id, steam_id):
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(insight_store, "sync_bound_steam_id", fail_sync)
    with pytest.raises(RuntimeError, match="injected persistence failure"):
        auth.bind_steam_id("alice", "76561198000000002")

    assert auth.get_user_info("alice")["bound_steam_id"] == "76561198000000001"
