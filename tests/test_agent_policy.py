import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("STEAM_API_KEY", "test-key")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from steam_agent.api.routes import _extract_executed_tool_calls
from steam_agent.graph.nodes import (
    _inject_tool_context,
    after_tools,
    finalize_node,
    tool_node,
)
from steam_agent.graph.tool_policy import (
    canonical_tool_call,
    collect_tool_calls,
    count_tool_rounds,
    evaluate_tool_call,
)
from steam_agent.observability import metrics


class ToolPolicyTests(unittest.TestCase):
    def test_canonical_signature_ignores_argument_order(self):
        first = canonical_tool_call("search", {"query": "rpg", "limit": 3})
        second = canonical_tool_call("search", {"limit": 3, "query": "rpg"})

        self.assertEqual(first, second)

    def test_duplicate_call_is_blocked(self):
        decision = evaluate_tool_call(
            "search_steam_store",
            {"query": "roguelike"},
            [{"name": "search_steam_store", "args": {"query": "roguelike"}}],
            position_in_round=0,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "duplicate_call")

    def test_rag_call_limit_is_enforced_across_different_queries(self):
        decision = evaluate_tool_call(
            "rag_search_similar_games",
            {"query": "action roguelike"},
            [{"name": "rag_search_similar_games", "args": {"query": "Hades like"}}],
            position_in_round=0,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "tool_call_limit")

    def test_per_round_call_limit_is_enforced(self):
        with patch("steam_agent.graph.tool_policy.AGENT_MAX_TOOL_CALLS_PER_ROUND", 2):
            decision = evaluate_tool_call("save_user_insight", {}, [], position_in_round=2)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "round_call_limit")

    def test_history_only_counts_calls_after_latest_human_message(self):
        old_call = AIMessage(
            content="",
            tool_calls=[{"name": "search_steam_store", "args": {"query": "old"}, "id": "1"}],
        )
        new_call = AIMessage(
            content="",
            tool_calls=[{"name": "rag_search_similar_games", "args": {"query": "new"}, "id": "2"}],
        )
        messages = [HumanMessage(content="old"), old_call, HumanMessage(content="new"), new_call]

        calls = collect_tool_calls(messages)

        self.assertEqual([call["name"] for call in calls], ["rag_search_similar_games"])
        self.assertEqual(count_tool_rounds(messages), 1)


class ToolNodePolicyTests(unittest.TestCase):
    def setUp(self):
        metrics.reset()

    def tearDown(self):
        metrics.reset()

    def test_duplicate_tool_executes_once_and_returns_structured_block(self):
        fake_tool = Mock(return_value={"results": ["game"]})
        message = AIMessage(
            content="",
            tool_calls=[
                {"name": "search_steam_store", "args": {"query": "rpg"}, "id": "call-1"},
                {"name": "search_steam_store", "args": {"query": "rpg"}, "id": "call-2"},
            ],
        )
        state = {
            "messages": [HumanMessage(content="推荐 RPG"), message],
            "steam_id": "",
            "user_id": "test-user",
        }
        with patch(
            "steam_agent.graph.nodes.get_tool_map",
            return_value={"search_steam_store": fake_tool},
        ):
            result = tool_node(state)

        fake_tool.assert_called_once_with(query="rpg")
        blocked = json.loads(result["messages"][1].content)
        self.assertEqual(blocked["status"], "policy_blocked")
        self.assertEqual(blocked["error"]["code"], "duplicate_call")

    def test_context_arguments_are_injected_from_graph_state(self):
        state = {
            "messages": [],
            "steam_id": "76561190000000000",
            "user_id": "current-user",
        }

        playtime = _inject_tool_context(
            "get_user_playtime", {"steam_id": "model-value", "count": 5}, state
        )
        memory = _inject_tool_context(
            "recall_user_memory", {"user_id": "model-value", "query": "cards"}, state
        )

        self.assertEqual(playtime["steam_id"], "76561190000000000")
        self.assertEqual(memory["user_id"], "current-user")

    def test_tool_is_not_retried_after_empty_result_even_with_new_arguments(self):
        fake_tool = Mock(return_value={"results": [{"appid": 10}]})
        message = AIMessage(
            content="",
            tool_calls=[{
                "name": "search_steam_store",
                "args": {"query": "broader query"},
                "id": "call-2",
            }],
        )
        state = {
            "messages": [HumanMessage(content="找游戏"), message],
            "steam_id": "",
            "user_id": "test-user",
            "tool_history": [{
                "tool": "search_steam_store",
                "status": "empty",
                "arguments": {"query": "first query"},
            }],
        }

        result = tool_node(
            state,
            {"configurable": {"tool_map": {"search_steam_store": fake_tool}}},
        )

        fake_tool.assert_not_called()
        blocked = json.loads(result["messages"][0].content)
        self.assertEqual(blocked["error"]["code"], "empty_result_no_retry")

    def test_independent_tools_execute_concurrently_but_preserve_message_order(self):
        def slow(value):
            time.sleep(0.04)
            return {"value": value}

        message = AIMessage(content="", tool_calls=[
            {"name": "tool_a", "args": {"value": "a"}, "id": "a"},
            {"name": "tool_b", "args": {"value": "b"}, "id": "b"},
        ])
        state = {
            "messages": [HumanMessage(content="parallel"), message],
            "steam_id": "",
            "user_id": "u1",
        }

        started = time.perf_counter()
        result = tool_node(state, {"configurable": {"tool_map": {
            "tool_a": slow,
            "tool_b": slow,
        }}})
        duration = time.perf_counter() - started

        self.assertLess(duration, 0.075)
        self.assertEqual([item.tool_call_id for item in result["messages"]], ["a", "b"])
        self.assertTrue(all(item["parallel"] for item in result["tool_history"]))

    def test_multiple_memory_writes_remain_sequential(self):
        order = []

        def save(**arguments):
            order.append(arguments["insight"])
            return {"saved": True}

        message = AIMessage(content="", tool_calls=[
            {"name": "save_user_insight", "args": {"insight": "first", "category": "fact"}, "id": "1"},
            {"name": "save_user_insight", "args": {"insight": "second", "category": "fact"}, "id": "2"},
        ])
        state = {
            "messages": [HumanMessage(content="save two"), message],
            "steam_id": "",
            "user_id": "u1",
        }

        result = tool_node(state, {"configurable": {"tool_map": {"save_user_insight": save}}})

        self.assertEqual(order, ["first", "second"])
        self.assertTrue(all(not item["parallel"] for item in result["tool_history"]))

    def test_api_reports_only_calls_that_policy_allowed(self):
        assistant = AIMessage(
            content="",
            tool_calls=[
                {"name": "search_steam_store", "args": {"query": "rpg"}, "id": "ok"},
                {"name": "search_steam_store", "args": {"query": "rpg"}, "id": "blocked"},
            ],
        )
        messages = [
            assistant,
            ToolMessage(content='{"results": []}', tool_call_id="ok"),
            ToolMessage(
                content='{"status":"policy_blocked","error":{"code":"duplicate_call","message":"blocked","retryable":false}}',
                tool_call_id="blocked",
            ),
        ]

        self.assertEqual(_extract_executed_tool_calls(messages), ["search_steam_store"])

    def test_after_tools_routes_to_finalizer_at_budget(self):
        calls = []
        for index in range(2):
            calls.extend([
                AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "search_steam_store",
                        "args": {"query": str(index)},
                        "id": str(index),
                    }],
                ),
                SimpleNamespace(type="tool", tool_calls=None),
            ])
        state = {
            "messages": [HumanMessage(content="推荐游戏"), *calls],
            "steam_id": "",
            "user_id": "test-user",
        }

        with patch("steam_agent.graph.tool_policy.AGENT_MAX_TOOL_ROUNDS", 2):
            route = after_tools(state)

        self.assertEqual(route, "finalize")

    def test_finalizer_invokes_plain_llm_without_binding_tools(self):
        llm = Mock()
        llm.invoke.return_value = AIMessage(content="基于现有结果的最终答复")
        state = {
            "messages": [HumanMessage(content="推荐游戏")],
            "steam_id": "",
            "user_id": "test-user",
        }
        with (
            patch("steam_agent.graph.nodes.build_llm", return_value=llm),
            patch(
                "steam_agent.graph.nodes.build_system_prompt",
                return_value=SystemMessage(content="base"),
            ),
        ):
            result = finalize_node(state)

        llm.invoke.assert_called_once()
        self.assertEqual(result["messages"][0].content, "基于现有结果的最终答复")
        sent_prompt = llm.invoke.call_args.args[0][0].content
        self.assertIn("不能再调用工具", sent_prompt)


if __name__ == "__main__":
    unittest.main()
