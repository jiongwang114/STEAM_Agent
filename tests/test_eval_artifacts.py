import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from evals.artifacts import write_companion_artifacts


class EvaluationArtifactTests(unittest.TestCase):
    def test_writes_human_and_ci_readable_companions(self):
        report = {
            "manifest": {"run_id": "r1", "model": "test", "git_sha": "abc"},
            "summary": {"task_success": 0.5},
            "cases": [
                {"case_id": "ok", "passed": True},
                {"case_id": "bad", "passed": False, "failures": ["missing:rag"]},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"

            write_companion_artifacts(report, path)

            markdown = path.with_suffix(".md").read_text(encoding="utf-8")
            suite = ET.parse(path.with_suffix(".xml")).getroot()
        self.assertIn("task_success", markdown)
        self.assertIn("missing:rag", markdown)
        self.assertEqual(suite.attrib["tests"], "2")
        self.assertEqual(suite.attrib["failures"], "1")


if __name__ == "__main__":
    unittest.main()
