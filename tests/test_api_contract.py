import json
import unittest
from pathlib import Path

from steam_agent.api.schemas import ChatRequest, ChatResponse


ROOT = Path(__file__).resolve().parent.parent


class ApiCompatibilityTests(unittest.TestCase):
    def test_chat_schema_preserves_versioned_contract(self):
        contract = json.loads(
            (ROOT / "evals" / "contracts" / "chat_api.v1.json").read_text(encoding="utf-8")
        )
        for model, section in ((ChatRequest, "request"), (ChatResponse, "response")):
            schema = model.model_json_schema()
            self.assertTrue(set(contract[section]["required"]).issubset(schema.get("required", [])))
            for name, expected in contract[section]["properties"].items():
                self.assertIn(name, schema["properties"])
                actual = _json_types(schema["properties"][name])
                expected_types = {expected} if isinstance(expected, str) else set(expected)
                self.assertTrue(expected_types.issubset(actual), f"{name}: {actual}")


def _json_types(schema: dict) -> set[str]:
    if isinstance(schema.get("type"), list):
        return set(schema["type"])
    if schema.get("type"):
        return {schema["type"]}
    return {
        item["type"]
        for item in schema.get("anyOf", [])
        if "type" in item
    }


if __name__ == "__main__":
    unittest.main()
