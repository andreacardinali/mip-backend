import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "llm" / "evidence.py"
SPEC = importlib.util.spec_from_file_location("llm_evidence_under_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
select_sources = MODULE.select_sources


class LLMSourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {
                "sourceIndex": 0,
                "title": "First",
                "uri": "https://example.test/first",
            },
            {
                "sourceIndex": 1,
                "title": "Second",
                "uri": "https://example.test/second",
            },
            {
                "sourceIndex": 2,
                "title": "Third",
                "uri": "https://example.test/third",
            },
        ]

    def test_preserves_requested_order(self):
        selected = select_sources([2, 0], self.sources)
        self.assertEqual(
            [source["sourceIndex"] for source in selected],
            [2, 0],
        )

    def test_removes_duplicates_and_invalid_indices(self):
        selected = select_sources(
            [1, 1, 99, "0", None, True, 0],
            self.sources,
        )
        self.assertEqual(
            [source["sourceIndex"] for source in selected],
            [1, 0],
        )

    def test_ignores_malformed_sources(self):
        selected = select_sources(
            [0, 1],
            [
                {"sourceIndex": "0", "title": "Wrong type"},
                {"title": "Missing index"},
                {"sourceIndex": 1, "title": "Valid"},
            ],
        )
        self.assertEqual(selected, [{"sourceIndex": 1, "title": "Valid"}])

    def test_returns_copies(self):
        selected = select_sources([0], self.sources)
        selected[0]["title"] = "Changed"
        self.assertEqual(self.sources[0]["title"], "First")

    def test_empty_input(self):
        self.assertEqual(select_sources(None, self.sources), [])
        self.assertEqual(select_sources([], self.sources), [])


if __name__ == "__main__":
    unittest.main()
