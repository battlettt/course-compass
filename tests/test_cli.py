import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from course_compass import cli
from tests.helpers import NOTES, ROOT

NO_KEY_ENV = {k: v for k, v in os.environ.items() if not k.startswith("COURSE_COMPASS_")}


@mock.patch.dict(os.environ, NO_KEY_ENV, clear=True)
class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "db.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            code = cli.main([*argv, "--db", self.db], out=out)
        return code, out.getvalue(), err.getvalue()

    def ingest(self):
        code, out, _ = self.run_cli("ingest", str(NOTES), "--allow-dir", str(ROOT))
        self.assertEqual(code, 0)
        return out

    def db_data(self):
        return json.loads(Path(self.db).read_text(encoding="utf-8"))

    def test_end_to_end_ingest_ask_quiz_review_plan_export(self):
        self.assertIn("Ingested 4 note file(s) into 21 chunks", self.ingest())

        code, out, _ = self.run_cli("ask", "What is a p-value?")
        self.assertEqual(code, 0)
        self.assertIn("LOCAL FALLBACK", out)
        self.assertIn("[hypothesis-testing#", out)
        self.assertIn("Sources:", out)

        code, out, _ = self.run_cli("quiz", "--topic", "elasticity", "--n", "5", "--today", "2026-10-30")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("\nQ"), 5)
        self.assertEqual(len(self.db_data()["cards"]), 5)

        code, out, _ = self.run_cli("quiz", "--topic", "elasticity", "--n", "5",
                                    "--answers", "A,A,A,A,A", "--today", "2026-10-30")
        self.assertEqual(code, 0)
        self.assertIn("Score:", out)
        self.assertEqual(len(self.db_data()["attempts"]), 5)

        code, out, _ = self.run_cli("review", "--today", "2026-10-31")
        self.assertEqual(code, 0)
        self.assertIn("5 card(s) due on 2026-10-31", out)

        code, out, _ = self.run_cli("review", "--today", "2026-10-31", "--answers", "B,B")
        self.assertIn("Reviewed 2 card(s)", out)
        self.assertEqual(len(self.db_data()["attempts"]), 7)

        code, out, _ = self.run_cli("plan", "--today", "2026-10-31", "--minutes", "40")
        self.assertEqual(code, 0)
        self.assertIn("elasticity", out)
        total = sum(int(line.split()[2]) for line in out.splitlines() if " min " in line)
        self.assertEqual(total, 40)

        sheet = Path(self.tmp.name) / "sheet.md"
        code, out, _ = self.run_cli("export", "--format", "markdown", "--out", str(sheet))
        self.assertEqual(code, 0)
        text = sheet.read_text(encoding="utf-8")
        for section in ("## Weak topics", "## Key facts", "## Quiz misses"):
            self.assertIn(section, text)
        self.assertIn("**P-value**", text)

    def test_ask_refuses_off_topic_question(self):
        self.ingest()
        code, out, _ = self.run_cli("ask", "Who won the 1998 football world cup?", "--local")
        self.assertEqual(code, 0)
        self.assertIn("Not in your notes.", out)
        self.assertNotIn("Sources:", out)

    def test_llm_flag_without_key_exits_2(self):
        self.ingest()
        code, _, err = self.run_cli("ask", "What is a p-value?", "--llm")
        self.assertEqual(code, 2)
        self.assertIn("COURSE_COMPASS_API_KEY", err)

    def test_missing_db_and_bad_topic_exit_2(self):
        code, _, err = self.run_cli("ask", "What is ATP?")
        self.assertEqual(code, 2)
        self.assertIn("Run `ingest` first", err)
        self.ingest()
        code, _, err = self.run_cli("quiz", "--topic", "chemistry")
        self.assertEqual(code, 2)
        self.assertIn("Known topics", err)

    def test_ingest_path_traversal_rejected(self):
        code, _, err = self.run_cli("ingest", "../outside", "--allow-dir", str(ROOT))
        self.assertEqual(code, 2)
        self.assertIn("'..'", err)
        self.assertFalse(Path(self.db).exists())

    def test_bad_answers_value(self):
        self.ingest()
        code, _, err = self.run_cli("quiz", "--answers", "A,Z")
        self.assertEqual(code, 2)
        self.assertIn("A-D", err)

    def test_interactive_quiz_reads_stdin(self):
        self.ingest()
        with mock.patch("sys.stdin", io.StringIO("A\nx\nB\n\n")):
            code, out, _ = self.run_cli("quiz", "--n", "3", "-i")
        self.assertEqual(code, 0)
        self.assertIn("Please type A, B, C or D", out)
        self.assertIn("Score:", out)
        self.assertEqual(len(self.db_data()["attempts"]), 2)

    def test_review_with_nothing_due(self):
        self.ingest()
        code, out, _ = self.run_cli("review")
        self.assertEqual(code, 0)
        self.assertIn("Nothing due", out)

    def test_export_json(self):
        self.ingest()
        code, out, _ = self.run_cli("export", "--format", "json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data["weak_topics"]), 4)
        self.assertTrue(data["facts"])

    def test_llm_mode_uses_client_when_key_set(self):
        self.ingest()
        reply = json.dumps({"in_notes": True, "answer": "Rejecting a true null [hypothesis-testing#4].",
                            "citations": ["hypothesis-testing#4"]})
        fake = mock.Mock()
        fake.chat.return_value = reply
        with mock.patch.dict(os.environ, {"COURSE_COMPASS_API_KEY": "k"}), \
                mock.patch.object(cli, "LLMClient", return_value=fake):
            code, out, _ = self.run_cli("ask", "What is a Type I error?")
        self.assertEqual(code, 0)
        self.assertIn("LLM answer, grounded in your notes", out)
        self.assertIn("[hypothesis-testing#4] examples/notes/hypothesis-testing.md", out)


class FakeClaude:
    label = "Claude CLI: sonnet"

    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, messages, temperature=0.0):
        return self.replies.pop(0)


TYPE_I_REPLY = json.dumps({"in_notes": True, "answer": "Rejecting a true null [hypothesis-testing#4].",
                           "citations": ["hypothesis-testing#4"]})


@mock.patch.dict(os.environ, NO_KEY_ENV, clear=True)
class ProviderSelectionTests(unittest.TestCase):
    setUp = CLITests.setUp
    tearDown = CLITests.tearDown
    run_cli = CLITests.run_cli
    ingest = CLITests.ingest

    def args(self, *argv):
        return cli.build_parser().parse_args(["ask", "q", *argv])

    def test_requested_provider_precedence(self):
        pick = cli.requested_provider
        self.assertIsNone(pick(self.args(), {}))
        self.assertEqual(pick(self.args("--llm"), {}), "openai")
        self.assertEqual(pick(self.args("--local"), {"COURSE_COMPASS_PROVIDER": "claude"}), "local")
        self.assertEqual(pick(self.args(), {"COURSE_COMPASS_PROVIDER": "Claude"}), "claude")
        self.assertEqual(pick(self.args("--llm"), {"COURSE_COMPASS_PROVIDER": "claude"}), "claude")
        self.assertEqual(pick(self.args("--provider", "openai"), {"COURSE_COMPASS_PROVIDER": "claude"}), "openai")
        self.assertEqual(pick(self.args("--provider", "local", "--local"), {}), "local")

    def test_conflicting_or_bad_choices_are_errors(self):
        for argv, env in [(("--local", "--provider", "claude"), {}),
                          (("--llm", "--provider", "local"), {}),
                          (("--llm",), {"COURSE_COMPASS_PROVIDER": "local"}),
                          ((), {"COURSE_COMPASS_PROVIDER": "gpt"})]:
            with self.assertRaises(cli.CLIError, msg=str((argv, env))):
                cli.requested_provider(self.args(*argv), env)

    def test_claude_provider_answer_is_labelled_as_claude(self):
        self.ingest()
        with mock.patch.object(cli, "ClaudeCLIClient", return_value=FakeClaude(TYPE_I_REPLY)), \
                mock.patch.object(cli.ClaudeCLIConfig, "from_env", return_value=None):
            code, out, _ = self.run_cli("ask", "What is a Type I error?", "--provider", "claude")
        self.assertEqual(code, 0)
        self.assertIn("== LLM answer (Claude CLI: sonnet), grounded in your notes ==", out)
        self.assertNotIn("LOCAL", out)

    def test_claude_provider_via_env_var_for_quiz(self):
        self.ingest()
        item = {"question": "Which term is the share of income response?", "correct": "Income elasticity",
                "distractors": ["Cross-price elasticity", "Unit elastic", "Elastic demand"],
                "source_chunk_id": "elasticity#3"}
        fake = FakeClaude(json.dumps({"items": [item]}))
        with mock.patch.dict(os.environ, {"COURSE_COMPASS_PROVIDER": "claude"}), \
                mock.patch.object(cli, "ClaudeCLIClient", return_value=fake), \
                mock.patch.object(cli.ClaudeCLIConfig, "from_env", return_value=None):
            code, out, _ = self.run_cli("quiz", "--topic", "elasticity", "--n", "1")
        self.assertEqual(code, 0, out)
        self.assertIn("== LLM quiz (Claude CLI: sonnet) (validated", out)

    def test_unavailable_claude_fails_instead_of_falling_back(self):
        self.ingest()
        env = {"COURSE_COMPASS_CLAUDE_BIN": str(Path(self.tmp.name) / "no-such-claude")}
        for argv, extra_env in [(("--provider", "claude"), env),
                                ((), {**env, "COURSE_COMPASS_PROVIDER": "claude"}),
                                (("--llm",), {**env, "COURSE_COMPASS_PROVIDER": "claude"})]:
            with mock.patch.dict(os.environ, extra_env):
                code, out, err = self.run_cli("ask", "What is a p-value?", *argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("Claude CLI provider unavailable", err)
            self.assertNotIn("LOCAL", out)
            self.assertNotIn("Sources:", out)

    def test_openai_provider_without_key_fails(self):
        self.ingest()
        for argv, env in [(("--provider", "openai"), {}), ((), {"COURSE_COMPASS_PROVIDER": "openai"})]:
            with mock.patch.dict(os.environ, env):
                code, out, err = self.run_cli("ask", "What is a p-value?", *argv)
            self.assertEqual(code, 2)
            self.assertIn("COURSE_COMPASS_API_KEY", err)
            self.assertEqual(out, "")

    def test_local_provider_is_labelled_local(self):
        self.ingest()
        code, out, _ = self.run_cli("ask", "What is a p-value?", "--provider", "local")
        self.assertEqual(code, 0)
        self.assertIn("LOCAL FALLBACK", out)
        with mock.patch.dict(os.environ, {"COURSE_COMPASS_PROVIDER": "local", "COURSE_COMPASS_API_KEY": "k"}):
            code, out, _ = self.run_cli("ask", "What is a p-value?")
        self.assertIn("COURSE_COMPASS_PROVIDER=local: using the LOCAL fallback (no LLM).", out)
        self.assertIn("LOCAL FALLBACK", out)

    def test_key_set_without_provider_still_uses_openai_client(self):
        self.ingest()
        fake = mock.Mock()
        fake.chat.return_value = TYPE_I_REPLY
        with mock.patch.dict(os.environ, {"COURSE_COMPASS_API_KEY": "k"}), \
                mock.patch.object(cli, "LLMClient", return_value=fake) as made, \
                mock.patch.object(cli, "ClaudeCLIClient") as claude:
            code, out, _ = self.run_cli("ask", "What is a Type I error?")
        self.assertEqual(code, 0)
        made.assert_called_once()
        claude.assert_not_called()
        self.assertIn("== LLM answer, grounded in your notes ==", out)


class ModuleEntryPointTests(unittest.TestCase):
    def test_python_dash_m_runs(self):
        result = subprocess.run([sys.executable, "-m", "course_compass", "--version"],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0)
        self.assertIn("course-compass", result.stdout)

    def test_no_command_prints_help_and_exits_2(self):
        out = io.StringIO()
        self.assertEqual(cli.main([], out=out), 2)
        self.assertIn("ingest", out.getvalue())


if __name__ == "__main__":
    unittest.main()
