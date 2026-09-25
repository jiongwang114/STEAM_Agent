import time
import unittest

from steam_agent.tools.contracts import ToolStatus
from steam_agent.tools.executor import ToolCircuitBreaker, execute_tool


class ToolExecutorTests(unittest.TestCase):
    def setUp(self):
        self.circuit = ToolCircuitBreaker(failure_threshold=3, cooldown_seconds=30)

    def test_empty_result_is_terminal_and_not_retried(self):
        calls = 0

        def empty():
            nonlocal calls
            calls += 1
            return {"results": []}

        result = execute_tool(
            "search",
            empty,
            {},
            timeout_seconds=0.1,
            max_retries=2,
            circuit_breaker=self.circuit,
        )

        self.assertEqual(result.status, ToolStatus.EMPTY)
        self.assertEqual(calls, 1)
        self.assertEqual(result.meta["attempts"], 1)

    def test_timeout_is_bounded_and_retried_once(self):
        calls = 0

        def slow():
            nonlocal calls
            calls += 1
            time.sleep(0.05)
            return {"results": [{"appid": 1}]}

        started = time.perf_counter()
        result = execute_tool(
            "slow",
            slow,
            {},
            timeout_seconds=0.005,
            max_retries=1,
            circuit_breaker=self.circuit,
        )

        self.assertEqual(result.status, ToolStatus.TIMEOUT)
        self.assertEqual(calls, 2)
        self.assertEqual(result.meta["attempts"], 2)
        self.assertLess(time.perf_counter() - started, 0.04)

    def test_expired_agent_deadline_does_not_start_tool(self):
        calls = 0

        def tool():
            nonlocal calls
            calls += 1

        result = execute_tool(
            "deadline",
            tool,
            {},
            deadline_at=time.time() - 1,
            max_retries=3,
            circuit_breaker=self.circuit,
        )

        self.assertEqual(result.status, ToolStatus.TIMEOUT)
        self.assertEqual(calls, 0)
        self.assertEqual(result.meta["attempts"], 1)

    def test_circuit_opens_after_repeated_failures_and_recovers(self):
        circuit = ToolCircuitBreaker(failure_threshold=2, cooldown_seconds=10)
        circuit.record("steam", success=False, now=1)
        self.assertTrue(circuit.allow("steam", now=2))
        circuit.record("steam", success=False, now=2)
        self.assertFalse(circuit.allow("steam", now=3))
        self.assertTrue(circuit.allow("steam", now=12))

    def test_large_tool_result_is_structurally_truncated(self):
        result = execute_tool(
            "large",
            lambda: {"results": [{"appid": i, "description": "x" * 4000} for i in range(30)]},
            {},
            timeout_seconds=0.1,
            max_retries=0,
            circuit_breaker=self.circuit,
        )

        self.assertTrue(result.meta["result_truncated"])
        self.assertLessEqual(len(result.data["results"]), 10)


if __name__ == "__main__":
    unittest.main()
