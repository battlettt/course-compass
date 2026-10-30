import unittest

from course_compass import prompts
from tests.helpers import ROOT


class DocsTests(unittest.TestCase):
    def test_prompts_doc_contains_every_template_verbatim(self):
        doc = (ROOT / "docs" / "PROMPTS.md").read_text(encoding="utf-8")
        for name in ("ANSWER_SYSTEM", "ANSWER_USER", "QUIZ_SYSTEM", "QUIZ_USER", "REPAIR_USER"):
            self.assertIn(getattr(prompts, name), doc, name)

    def test_required_project_files_exist(self):
        for name in ("README.md", "LICENSE", "SECURITY.md", "pyproject.toml", ".gitignore",
                     "docs/LEARNING.md", "docs/ARCHITECTURE.md", "docs/PROMPTS.md", "demo/index.html"):
            self.assertTrue((ROOT / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
