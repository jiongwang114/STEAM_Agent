import unittest

from steam_agent.tools.registry import FixtureToolRegistry


class FixtureRegistryTests(unittest.TestCase):
    def test_routes_response_by_exact_and_contains_arguments(self):
        registry = FixtureToolRegistry({
            "tools": {
                "search": {
                    "routes": [{
                        "when": {"user_id": "u1", "query_contains_any": ["Hades", "肉鸽"]},
                        "result": {"results": ["personalized"]},
                    }],
                    "default": {"results": ["generic"]},
                }
            }
        })
        search = registry.as_tool_map(["search"])["search"]

        self.assertEqual(
            search(user_id="u1", query="games like Hades")["results"],
            ["personalized"],
        )
        self.assertEqual(search(user_id="u2", query="Hades")["results"], ["generic"])


if __name__ == "__main__":
    unittest.main()
