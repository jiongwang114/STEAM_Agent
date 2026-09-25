import unittest
from unittest.mock import patch

from steam_agent import model_routing


class ModelRoutingTests(unittest.TestCase):
    def test_experiment_assignment_is_stable(self):
        with (
            patch.object(model_routing, "AGENT_EXPERIMENT_NAME", "prompt-v3"),
            patch.object(model_routing, "AGENT_EXPERIMENT_CANDIDATE_PERCENT", 50),
        ):
            first = model_routing.assign_experiment("u1", "t1")
            second = model_routing.assign_experiment("u1", "t1")

        self.assertEqual(first, second)
        self.assertIn(first["variant"], {"control", "candidate"})

    def test_finalize_uses_fast_model_and_candidate_uses_candidate_model(self):
        with (
            patch.object(model_routing, "LLM_FAST_MODEL", "fast"),
            patch.object(model_routing, "LLM_CANDIDATE_MODEL", "candidate"),
        ):
            final = model_routing.select_model("finalize", {"variant": "candidate"})
            agent = model_routing.select_model("agent", {"variant": "candidate"})

        self.assertEqual(final.model, "fast")
        self.assertEqual(agent.model, "candidate")


if __name__ == "__main__":
    unittest.main()
