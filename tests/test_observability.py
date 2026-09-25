import json
import logging
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from steam_agent.observability import (
    JsonFormatter,
    MetricsRegistry,
    get_request_id,
    metrics,
    record_agent_run,
    reset_request_id,
    set_request_id,
)


class MetricsRegistryTests(unittest.TestCase):
    def test_counter_aggregates_only_matching_label_sets(self):
        registry = MetricsRegistry()
        registry.increment("requests", route="/chat", status="200")
        registry.increment("requests", 2, route="/chat", status="200")
        registry.increment("requests", route="/chat", status="500")

        counters = registry.snapshot()["counters"]

        self.assertEqual(len(counters), 2)
        successful = next(item for item in counters if item["labels"]["status"] == "200")
        self.assertEqual(successful["value"], 3)

    def test_histogram_exposes_latency_summary_and_bounds_samples(self):
        registry = MetricsRegistry(max_samples=3)
        for value in (0.1, 0.2, 0.3, 0.4):
            registry.observe("latency", value, mode="stream")

        histogram = registry.snapshot()["histograms"][0]

        self.assertEqual(histogram["count"], 4)
        self.assertAlmostEqual(histogram["sum"], 1.0)
        self.assertEqual(histogram["min"], 0.1)
        self.assertEqual(histogram["max"], 0.4)
        self.assertEqual(histogram["p95"], 0.4)

    def test_agent_run_records_cost_latency_and_tools(self):
        metrics.reset()
        self.addCleanup(metrics.reset)

        record_agent_run(
            mode="sync",
            status="success",
            duration_seconds=1.25,
            tool_calls=["rag_search_similar_games", "search_steam_store"],
            input_tokens=120,
            output_tokens=30,
            ttft_seconds=0.4,
        )
        snapshot = metrics.snapshot()

        values = {
            (item["name"], tuple(sorted(item["labels"].items()))): item["value"]
            for item in snapshot["counters"]
        }
        self.assertEqual(
            values[("agent_requests_total", (("mode", "sync"), ("status", "success")))],
            1,
        )
        self.assertEqual(values[("agent_input_tokens_total", (("mode", "sync"),))], 120)
        self.assertEqual(values[("agent_output_tokens_total", (("mode", "sync"),))], 30)
        self.assertEqual(
            values[("agent_tool_calls_total", (("tool", "rag_search_similar_games"),))],
            1,
        )
        histogram_names = {item["name"] for item in snapshot["histograms"]}
        self.assertEqual(histogram_names, {"agent_duration_seconds", "agent_ttft_seconds"})


class RequestContextTests(unittest.TestCase):
    def test_invalid_incoming_request_id_is_replaced(self):
        request_id, token = set_request_id("contains spaces\r\n")
        try:
            self.assertNotEqual(request_id, "contains spaces\r\n")
            self.assertEqual(len(request_id), 32)
            self.assertEqual(get_request_id(), request_id)
        finally:
            reset_request_id(token)

    def test_json_log_contains_request_id_and_structured_fields(self):
        request_id, token = set_request_id("req-demo-1")
        try:
            record = logging.LogRecord(
                "test", logging.INFO, __file__, 1, "completed", (), None
            )
            record.fields = {"duration_ms": 12.5}
            payload = json.loads(JsonFormatter().format(record))
        finally:
            reset_request_id(token)

        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["duration_ms"], 12.5)
        self.assertEqual(payload["message"], "completed")


class ApiObservabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
        os.environ.setdefault("STEAM_API_KEY", "test-key")
        from fastapi.testclient import TestClient
        from steam_agent.api.main import app

        cls.client = TestClient(app)

    def setUp(self):
        metrics.reset()

    def tearDown(self):
        metrics.reset()

    def test_health_request_has_correlation_header_and_metrics(self):
        response = self.client.get("/health", headers={"X-Request-ID": "integration-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "integration-1")
        snapshot = metrics.snapshot()
        requests = [
            item
            for item in snapshot["counters"]
            if item["name"] == "http_requests_total"
        ]
        self.assertEqual(requests[0]["labels"]["route"], "/health")
        self.assertEqual(requests[0]["labels"]["status"], "200")


class StreamObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        metrics.reset()

    async def asyncTearDown(self):
        metrics.reset()

    async def test_stream_records_ttft_tokens_and_finishes_after_bookkeeping(self):
        from steam_agent.api.routes import chat_stream
        from steam_agent.api.schemas import ChatRequest

        class FakeGraph:
            async def astream_events(self, *_args, **_kwargs):
                yield {
                    "event": "on_chat_model_stream",
                    "metadata": {"langgraph_node": "agent"},
                    "data": {"chunk": SimpleNamespace(content="推荐结果")},
                }
                yield {
                    "event": "on_chat_model_end",
                    "metadata": {"langgraph_node": "agent"},
                    "data": {
                        "output": SimpleNamespace(
                            usage_metadata={"input_tokens": 40, "output_tokens": 8}
                        )
                    },
                }

        request = ChatRequest(
            thread_id="thread-test",
            user_id="user-test",
            message="推荐游戏",
        )
        with (
            patch("steam_agent.api.routes._get_graph", AsyncMock(return_value=FakeGraph())),
            patch("steam_agent.api.routes.archive_conversation") as archive,
            patch("steam_agent.api.routes._maybe_generate_title"),
        ):
            response = await chat_stream(request)
            chunks = [chunk async for chunk in response.body_iterator]

        self.assertIn('"event": "done"', chunks[-1])
        archive.assert_called_once()
        snapshot = metrics.snapshot()
        counters = {item["name"]: item["value"] for item in snapshot["counters"]}
        self.assertEqual(counters["agent_requests_total"], 1)
        self.assertEqual(counters["agent_input_tokens_total"], 40)
        self.assertEqual(counters["agent_output_tokens_total"], 8)
        histogram_names = {item["name"] for item in snapshot["histograms"]}
        self.assertIn("agent_ttft_seconds", histogram_names)


if __name__ == "__main__":
    unittest.main()
