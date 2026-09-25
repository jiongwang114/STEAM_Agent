import asyncio
import time
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from steam_agent.graph.builder import _compile
from steam_agent.graph.nodes import after_validation, safe_fallback_node, validate_answer_node
from steam_agent.graph.run_context import (
    add_usage,
    budget_reason,
    compact_messages,
    new_run_context,
    run_metadata,
)
from steam_agent.tools.contracts import ToolStatus, normalize_tool_result
from steam_agent.api.schemas import ChatRequest
from steam_agent.api import routes


class RunContextTests(unittest.TestCase):
    def test_real_model_usage_accumulates_across_calls(self):
        context = new_run_context()
        first = AIMessage(content="a")
        first.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        second = AIMessage(content="b")
        second.usage_metadata = {"input_tokens": 80, "output_tokens": 10, "total_tokens": 90}

        usage = add_usage(context["usage"], first)
        usage = add_usage(usage, second)

        self.assertEqual(usage["total_tokens"], 210)
        self.assertEqual(usage["llm_calls"], 2)

    def test_input_token_attribution_scales_to_observed_usage(self):
        message = AIMessage(content="ok")
        message.usage_metadata = {
            "input_tokens": 100,
            "output_tokens": 10,
            "total_tokens": 110,
        }

        usage = add_usage(
            None,
            message,
            {"system": 3, "history": 1, "tool_results": 0, "tool_schema": 0},
        )

        self.assertEqual(usage["input_system_tokens"], 75)
        self.assertEqual(usage["input_history_tokens"], 25)

    def test_budget_reports_token_and_deadline_reasons(self):
        context = new_run_context()
        context["usage"]["total_tokens"] = context["budget"]["max_total_tokens"]
        self.assertEqual(budget_reason(context), "token_budget")

        context["usage"]["total_tokens"] = 0
        context["budget"]["deadline_at"] = time.time() - 1
        self.assertEqual(budget_reason(context), "deadline")

    def test_run_metadata_does_not_expose_absolute_deadline(self):
        metadata = run_metadata(new_run_context())

        self.assertNotIn("deadline_at", metadata["budget"])

    def test_long_history_drops_whole_old_turns(self):
        messages = []
        for index in range(10):
            messages.extend([
                HumanMessage(content=f"turn-{index}-" + "中" * 500),
                AIMessage(content=f"answer-{index}"),
            ])

        compacted, stats = compact_messages(messages, max_tokens=500)

        self.assertGreater(stats["dropped_turns"], 0)
        self.assertEqual(compacted[0].content[:6], "turn-9")
        self.assertEqual(compacted[-1].content, "answer-9")

    def test_chat_schema_rejects_unbounded_user_message(self):
        with self.assertRaises(ValueError):
            ChatRequest(thread_id="t", user_id="u", message="x" * 6001)


class ToolContractTests(unittest.TestCase):
    def test_rag_result_produces_grounding_evidence(self):
        result = normalize_tool_result(
            "rag_search_similar_games",
            {"results": [{"appid": "10", "name": "Game", "similarity_score": 0.8}]},
        )

        self.assertEqual(result.status, ToolStatus.SUCCESS)
        self.assertEqual(result.evidence[0].appid, "10")
        self.assertEqual(result.evidence[0].score, 0.8)

    def test_empty_result_has_distinct_status(self):
        result = normalize_tool_result("search_steam_store", {"results": []})

        self.assertEqual(result.status, ToolStatus.EMPTY)
        self.assertIsNone(result.error)

    def test_tool_error_is_not_mislabeled_as_empty(self):
        result = normalize_tool_result("get_user_playtime", {"error": "timeout"})

        self.assertEqual(result.status, ToolStatus.UPSTREAM_ERROR)
        self.assertEqual(result.error.code, "upstream_error")


class GroundingGraphTests(unittest.TestCase):
    def test_unsupported_store_link_routes_to_repair(self):
        state = {
            "messages": [
                HumanMessage(content="recommend"),
                AIMessage(content="[Unknown](https://store.steampowered.com/app/20/)"),
            ],
            "evidence": [{"evidence_id": "rag:10", "appid": "10"}],
            "repair_attempts": 0,
        }

        update = validate_answer_node(state)
        state.update(update)

        self.assertFalse(state["validation"]["passed"])
        self.assertEqual(state["validation"]["unsupported_appids"], ["20"])
        self.assertEqual(after_validation(state), "repair")

    def test_supported_store_link_completes(self):
        state = {
            "messages": [AIMessage(content="[Known](https://store.steampowered.com/app/10/)")],
            "evidence": [{"evidence_id": "rag:10", "appid": "10"}],
        }

        update = validate_answer_node(state)

        self.assertTrue(update["validation"]["passed"])
        self.assertEqual(update["termination_reason"], "completed")

    def test_price_claim_must_match_tool_evidence(self):
        state = {
            "messages": [
                AIMessage(
                    content="[Known](https://store.steampowered.com/app/10/) - ￥99"
                )
            ],
            "evidence": [{
                "evidence_id": "store:10",
                "appid": "10",
                "payload": {"price": {"initial": 6800, "final": 3400}},
            }],
        }

        update = validate_answer_node(state)

        self.assertFalse(update["validation"]["passed"])
        self.assertEqual(
            update["validation"]["unsupported_prices"],
            [{"appid": "10", "claimed": 99.0}],
        )

    def test_initial_and_final_prices_are_both_supported(self):
        state = {
            "messages": [
                AIMessage(
                    content="[Known](https://store.steampowered.com/app/10/) - ￥68，现价 ￥34"
                )
            ],
            "evidence": [{
                "evidence_id": "store:10",
                "appid": "10",
                "payload": {"price": {"initial": 6800, "final": 3400}},
            }],
        }

        self.assertTrue(validate_answer_node(state)["validation"]["passed"])

    def test_score_and_discount_claims_must_match_evidence(self):
        state = {
            "messages": [AIMessage(content=(
                "[Known](https://store.steampowered.com/app/10/) "
                "Metacritic: 99，折扣 80%"
            ))],
            "evidence": [{
                "evidence_id": "store:10",
                "appid": "10",
                "payload": {
                    "metacritic": 90,
                    "price": {"discount_percent": 40},
                },
            }],
        }

        validation = validate_answer_node(state)["validation"]

        self.assertFalse(validation["passed"])
        self.assertEqual(validation["unsupported_scores"][0]["claimed"], 99)
        self.assertEqual(validation["unsupported_discounts"][0]["claimed"], 80)

    def test_internal_tool_protocol_is_never_accepted_as_final_answer(self):
        state = {
            "messages": [AIMessage(content='<｜｜DSML｜｜tool_calls><invoke name="rag">')],
            "evidence": [],
        }

        validation = validate_answer_node(state)["validation"]

        self.assertFalse(validation["passed"])
        self.assertTrue(validation["protocol_leak"])
        self.assertIn("internal_tool_protocol_leak", validation["violations"])

    def test_failed_repair_uses_safe_fallback(self):
        state = {"validation": {"passed": False}, "repair_attempts": 1}

        self.assertEqual(after_validation(state), "safe_fallback")
        result = safe_fallback_node(state)
        self.assertEqual(result["termination_reason"], "insufficient_evidence")

    def test_compiled_graph_contains_validation_and_repair_nodes(self):
        graph = _compile(MemorySaver()).get_graph()

        self.assertTrue({"validate", "repair", "safe_fallback"}.issubset(graph.nodes))


class ApiDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_chat_returns_structured_deadline_fallback(self):
        graph = Mock()

        async def hang(*args, **kwargs):
            await asyncio.sleep(0.05)

        graph.ainvoke = hang
        request = ChatRequest(
            thread_id="deadline-test",
            user_id="user-1",
            message="推荐游戏",
        )
        with (
            patch.object(routes, "AGENT_MAX_WALL_SECONDS", 0.001),
            patch.object(routes, "_get_graph", return_value=graph),
            patch.object(routes, "archive_conversation"),
            patch.object(routes, "_maybe_generate_title"),
        ):
            response = await routes.chat(request)

        self.assertEqual(response.run_metadata["termination_reason"], "deadline")
        self.assertIn("时间预算", response.reply)

    async def test_same_thread_requests_are_serialized(self):
        graph = Mock()
        active = 0
        max_active = 0

        async def invoke(state, config):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {
                **state,
                "messages": [*state["messages"], AIMessage(content="done")],
                "termination_reason": "completed",
            }

        graph.ainvoke = invoke
        requests = [
            ChatRequest(thread_id="shared-thread", user_id="u1", message=f"message {index}")
            for index in range(2)
        ]
        with (
            patch.object(routes, "_get_graph", return_value=graph),
            patch.object(routes, "archive_conversation"),
            patch.object(routes, "_maybe_generate_title"),
        ):
            await asyncio.gather(*(routes.chat(request) for request in requests))

        self.assertEqual(max_active, 1)

    async def test_model_exception_returns_honest_degradation(self):
        graph = Mock()

        async def fail(*args, **kwargs):
            raise ConnectionError("model unavailable")

        graph.ainvoke = fail
        request = ChatRequest(thread_id="model-error", user_id="u1", message="推荐游戏")
        with (
            patch.object(routes, "_get_graph", return_value=graph),
            patch.object(routes, "archive_conversation"),
            patch.object(routes, "_maybe_generate_title"),
        ):
            response = await routes.chat(request)

        self.assertEqual(response.run_metadata["termination_reason"], "model_error")
        self.assertIn("不会", response.reply)


if __name__ == "__main__":
    unittest.main()
