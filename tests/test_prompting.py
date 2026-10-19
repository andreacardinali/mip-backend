import tempfile
import unittest
from pathlib import Path

from prompting.repository import FileSystemPromptRepository, PromptNotFoundError


class PromptingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "hello.txt").write_text("Hello {name}", encoding="utf-8")
        self.repo = FileSystemPromptRepository(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_extension_is_optional(self):
        self.assertEqual(self.repo.render("hello", name="MIP"), "Hello MIP")

    def test_template_is_cached(self):
        first = self.repo.get("hello")
        (self.root / "hello.txt").write_text("Changed", encoding="utf-8")
        self.assertIs(first, self.repo.get("hello"))
        self.repo.clear_cache()
        self.assertEqual(self.repo.get("hello").content, "Changed")

    def test_missing_render_value_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "missing render value"):
            self.repo.render("hello")

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.repo.get("../secret")

    def test_missing_prompt_has_specific_error(self):
        with self.assertRaises(PromptNotFoundError):
            self.repo.get("missing")


if __name__ == "__main__":
    unittest.main()
