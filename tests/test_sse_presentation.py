import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from api import routes
from api.schemas import ChatRequest


def game_payload(appid):
    return {
        "appid": str(appid),
        "name": "游戏 " + str(appid),
        "store_url": "https://store.steampowered.com/app/" + str(appid),
        "image_url": "https://shared.akamai.steamstatic.com/header.jpg",
        "reason": "符合你的条件",
    }


class PresentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tokens_match_canonical_reply_and_cards_are_complete(self):
        reply, payload = routes._normalize_reply(json.dumps({
            "summary": '推荐这些游戏：\n包含引号 "、反斜线 \\ 和 🎮',
            "games": [game_payload(10), game_payload(20), game_payload(30)],
        }, ensure_ascii=False))
        with patch.object(routes.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            events = [event async for event in routes._presentation_events(payload)]
        self.assertEqual(events[0], {"event": "presentation", "data": {"total_games": 3}})
        replay = "".join(event["data"] for event in events if event["event"] == "token")
        self.assertEqual(replay, reply)
        cards = [event["data"] for event in events if event["event"] == "card"]
        self.assertEqual(cards, [
            {"index": index, "game": game} for index, game in enumerate(payload["games"])
        ])
        delays = [call.args[0] for call in sleep.await_args_list]
        self.assertEqual(delays.count(routes._SSE_CARD_DELAY_SECONDS), 2)
        prefix = ""
        for event in events:
            if event["event"] == "token":
                prefix += event["data"]
            elif event["event"] == "card":
                self.assertIn(json.dumps(payload["summary"], ensure_ascii=False), prefix)
                self.assertIn(json.dumps(event["data"]["game"], ensure_ascii=False, separators=(",", ":")), prefix)

    async def test_summary_only_and_empty_summary(self):
        for summary in ("请说明你想找的游戏类型。", ""):
            with self.subTest(summary=summary):
                with patch.object(routes.asyncio, "sleep", new_callable=AsyncMock):
                    events = [event async for event in routes._presentation_events({"summary": summary, "games": []})]
                self.assertFalse(any(event["event"] == "card" for event in events))
                self.assertEqual(events[0]["data"]["total_games"], 0)
                self.assertEqual(json.loads("".join(event["data"] for event in events if event["event"] == "token")), {"summary": summary, "games": []})

    async def test_normalization_removes_unsafe_cards_before_presentation(self):
        unsafe = {**game_payload(20), "store_url": "https://example.com/app/20"}
        _, payload = routes._normalize_reply(json.dumps({"summary": "结果", "games": [game_payload(10), unsafe]}))
        with patch.object(routes.asyncio, "sleep", new_callable=AsyncMock):
            events = [event async for event in routes._presentation_events(payload)]
        self.assertEqual(events[0]["data"]["total_games"], 1)
        self.assertEqual([event["data"]["game"]["appid"] for event in events if event["event"] == "card"], ["10"])

    async def test_snapshot_keeps_published_cards_and_deduplicates(self):
        run = routes._ChatRun("test-user", "test-thread", ChatRequest(thread_id="test-thread", message="推荐游戏"), "", time.perf_counter(), "test-run")
        run.publish({"event": "presentation", "data": {"total_games": 2}})
        first = {"event": "card", "data": {"index": 0, "game": game_payload(10)}}
        run.publish(first)
        snapshot = run.snapshot()
        run.publish(first)
        run.publish({"event": "card", "data": {"index": 1, "game": game_payload(20)}})
        self.assertEqual(len(snapshot["presentation"]["games"]), 1)
        self.assertEqual(len(run.snapshot()["presentation"]["games"]), 2)
        self.assertEqual(run.snapshot()["stream_status"], "正在展示推荐 · 2 / 2")
        stream = routes._chat_run_stream(run)
        try:
            resumed = json.loads((await anext(stream)).removeprefix("data: "))
            self.assertEqual(resumed["event"], "snapshot")
            self.assertEqual(len(resumed["data"]["presentation"]["games"]), 2)
        finally:
            await stream.aclose()

    async def test_snapshot_tracks_started_stage_without_completed_overwrite(self):
        run = routes._ChatRun("test-user", "test-thread", ChatRequest(thread_id="test-thread", message="推荐游戏"), "", time.perf_counter(), "test-run")
        run.publish({"event": "stage", "data": {"status": "started", "message": "正在核对游戏信息"}})
        run.publish({"event": "stage", "data": {"status": "completed", "message": ""}})
        self.assertEqual(run.snapshot()["stream_status"], "正在核对游戏信息")

    async def execute_mock_run(self, mode, fail=False):
        timeline = []
        final_reply = json.dumps({"summary": "已核对的回答", "games": [game_payload(10)]}, ensure_ascii=False)

        class FakeGraph:
            async def astream_events(self, initial_state, config, version):
                yield {"event": "on_chain_start", "metadata": {"langgraph_node": "agent"}}
                yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "finalize"}, "data": {"chunk": SimpleNamespace(content="未经验证的草稿与内部协议")}}
                yield {"event": "on_tool_start", "name": "search_steam_store"}
                yield {"event": "on_tool_end", "name": "search_steam_store"}
                yield {"event": "on_chain_start", "metadata": {"langgraph_node": "validate"}}
                if fail:
                    raise ValueError("mock model failure")
                timeline.append("validated")
                yield {"event": "on_chain_end", "metadata": {"langgraph_node": "validate"}}

            async def aget_state(self, config):
                return SimpleNamespace(values={"messages": [AIMessage(content=final_reply)], "validation": {"passed": True}})

        def archive(**kwargs):
            timeline.append("archived")
            return {"status": "complete"}

        async def restore(graph, config, initial_state):
            return initial_state

        with (
            patch.object(routes, "_get_graph", new=AsyncMock(return_value=FakeGraph())),
            patch.object(routes, "_restore_archived_history", side_effect=restore),
            patch.object(routes, "_archive_turn", side_effect=archive),
            patch.object(routes, "record_agent_run"),
            patch.object(routes, "assign_experiment", return_value={}),
            patch.object(routes.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            events = []
            async for event in routes._execute_agent_events(ChatRequest(thread_id="test-execution", message="推荐游戏"), user_id="test-user", steam_id="", mode=mode, started=time.perf_counter()):
                if event["event"] in {"presentation", "token", "card"}:
                    self.assertIn("archived", timeline)
                    if not fail:
                        self.assertIn("validated", timeline)
                events.append(event)
        return events, sleep.await_count

    async def test_execution_replays_only_after_validation_and_archive(self):
        events, _ = await self.execute_mock_run("stream")
        self.assertEqual(events[-1]["event"], "done")
        replay = "".join(event["data"] for event in events if event["event"] == "token")
        self.assertEqual(replay, events[-1]["data"]["reply"])
        self.assertNotIn("未经验证", replay)
        self.assertTrue(any(event["event"] == "status" and "查询已结束" in event["data"] for event in events))
        stage = next(event["data"] for event in events if event["event"] == "stage" and event["data"]["node"] == "validate")
        self.assertIn("核对", stage["message"])

    async def test_sync_response_has_no_presentation_delay(self):
        events, sleep_count = await self.execute_mock_run("sync")
        self.assertEqual(events[-1]["event"], "done")
        self.assertFalse(any(event["event"] in {"presentation", "token", "card"} for event in events))
        self.assertEqual(sleep_count, 0)

    async def test_model_failure_replays_safe_fallback_without_cards(self):
        events, _ = await self.execute_mock_run("stream", fail=True)
        self.assertTrue(any(event["event"] == "error" for event in events))
        self.assertFalse(any(event["event"] == "card" for event in events))
        self.assertEqual(events[-1]["data"]["status"], "degraded")
        self.assertEqual(json.loads(events[-1]["data"]["reply"])["summary"], routes._MODEL_ERROR_REPLY)


if __name__ == "__main__":
    unittest.main()
