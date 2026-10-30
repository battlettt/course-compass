"""CLI tests for simplify, examples, due, export --format ics, progress, plan trends and cram."""

import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from course_compass import cli
from course_compass.reminders import parse_ics
from tests.test_cli import NO_KEY_ENV, CLITests


class FakeClaude:
    label = "Claude CLI: sonnet"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, temperature=0.0):
        self.calls += 1
        return self.replies.pop(0)


def with_claude(fake):
    return (mock.patch.object(cli, "ClaudeCLIClient", return_value=fake),
            mock.patch.object(cli.ClaudeCLIConfig, "from_env", return_value=None))


SIMPLIFY_REPLY = json.dumps({
    "simple": "Elasticity is how much buying changes when the price changes [elasticity#1].",
    "full": "Price elasticity of demand is the percentage change in quantity demanded divided by the "
            "percentage change in price [elasticity#2].",
    "citations": ["elasticity#1", "elasticity#2"]})
EXAMPLES_REPLY = json.dumps({"in_notes": True, "examples": [
    {"kind": "illustrative", "text": "Insulin buyers barely cut back when its price rises.", "citations": [],
     "illustrates": "elasticity#2"},
    {"kind": "grounded", "text": "Necessities such as basic medicine tend to be inelastic.",
     "citations": ["elasticity#4"]}]})


@mock.patch.dict(os.environ, NO_KEY_ENV, clear=True)
class SchoolFeatureCLITests(unittest.TestCase):
    setUp = CLITests.setUp
    tearDown = CLITests.tearDown
    run_cli = CLITests.run_cli
    ingest = CLITests.ingest
    db_data = CLITests.db_data

    # ------------------------------------------------------------ simplify

    def test_simplify_with_claude_prints_both_layers_and_provider(self):
        self.ingest()
        fake = FakeClaude(SIMPLIFY_REPLY)
        a, b = with_claude(fake)
        with a, b:
            code, out, _ = self.run_cli("simplify", "--topic", "elasticity", "--provider", "claude")
        self.assertEqual(code, 0, out)
        self.assertIn('== SIMPLIFY by LLM (Claude CLI: sonnet), grounded in your notes: topic "elasticity" ==', out)
        layer1 = out.index("-- Layer 1: the simple version --")
        layer2 = out.index("-- Layer 2: the full-depth version --")
        self.assertLess(layer1, layer2)
        self.assertLess(out.index("Elasticity is how much"), layer2)
        self.assertGreater(out.index("Price elasticity of demand is"), layer2)
        self.assertIn("[elasticity#2] examples/notes/elasticity.md > Key terms", out)
        self.assertNotIn("LOCAL", out)

    def test_simplify_without_llm_is_labelled_extractive(self):
        self.ingest()
        code, out, _ = self.run_cli("simplify", "--chunk", "elasticity#5")
        self.assertEqual(code, 0)
        self.assertIn("No COURSE_COMPASS_API_KEY set: using the LOCAL fallback (no LLM).", out)
        self.assertIn("LOCAL EXTRACTIVE APPROXIMATION (no LLM, not a simplification)", out)
        self.assertIn("-- Layer 1: key sentences (extracted, not simplified) --", out)
        self.assertNotIn("by LLM", out)

    def test_simplify_requiring_an_unavailable_llm_fails_cleanly(self):
        self.ingest()
        code, out, err = self.run_cli("simplify", "--topic", "elasticity", "--llm")
        self.assertEqual(code, 2)
        self.assertIn("COURSE_COMPASS_API_KEY", err)
        self.assertNotIn("SIMPLIFY", out)

    def test_simplify_off_topic_query_refuses(self):
        self.ingest()
        code, out, _ = self.run_cli("simplify", "the capital of Australia")
        self.assertEqual(code, 0)
        self.assertIn("== Refusal ==", out)
        self.assertIn("Not in your notes.", out)

    def test_simplify_rejected_llm_output_exits_2(self):
        self.ingest()
        a, b = with_claude(FakeClaude("nope", "still nope"))
        with a, b:
            code, out, err = self.run_cli("simplify", "--topic", "elasticity", "--provider", "claude")
        self.assertEqual(code, 2)
        self.assertIn("rejected after one repair attempt", err)

    # ------------------------------------------------------------ examples

    def test_examples_label_grounded_and_illustrative(self):
        self.ingest()
        a, b = with_claude(FakeClaude(EXAMPLES_REPLY))
        with a, b:
            code, out, _ = self.run_cli("examples", "inelastic demand", "--n", "2", "--provider", "claude")
        self.assertEqual(code, 0, out)
        self.assertIn("== EXAMPLES by LLM (Claude CLI: sonnet)", out)
        self.assertIn("1. [FROM YOUR NOTES: elasticity#4]", out)
        self.assertIn("2. [ILLUSTRATIVE, not from your notes; illustrates elasticity#2]", out)

    def test_examples_local_fallback_quotes_notes(self):
        self.ingest()
        code, out, _ = self.run_cli("examples", "fermentation", "--local")
        self.assertEqual(code, 0)
        self.assertIn("LOCAL FALLBACK (extractive, no LLM)", out)
        self.assertIn("[FROM YOUR NOTES: cell-respiration#4]", out)
        self.assertNotIn("ILLUSTRATIVE", out.split("No illustrative")[0])

    # ------------------------------------------------------------ due + ics

    def quiz_and_answer(self):
        self.ingest()
        self.run_cli("quiz", "--topic", "elasticity", "--n", "4", "--answers", "A,A,A,A", "--today", "2026-10-30")

    def test_due_digest_groups_by_topic(self):
        self.quiz_and_answer()
        code, out, _ = self.run_cli("due", "--today", "2026-11-02")
        self.assertEqual(code, 0)
        self.assertIn("== Due for review on 2026-11-02 (local data, no LLM", out)
        self.assertIn("elasticity:", out)
        self.assertIn("overdue", out)
        code, out, _ = self.run_cli("due", "--today", "2026-10-29")
        self.assertIn("Nothing is due today.", out)

    def test_export_ics_round_trips(self):
        self.quiz_and_answer()
        path = Path(self.tmp.name) / "reviews.ics"
        code, out, _ = self.run_cli("export", "--format", "ics", "--today", "2026-10-30", "--days", "30",
                                    "--out", str(path))
        self.assertEqual(code, 0)
        self.assertIn("local data, no LLM", out)
        raw = path.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\r\r\n", raw)
        events = parse_ics(raw.decode("utf-8"))
        cards = self.db_data()["cards"]
        expected_days = {card["due"].replace("-", "") for card in cards.values()}
        self.assertEqual({e["DTSTART"] for e in events}, expected_days)
        self.assertTrue(all(e["SUMMARY"].startswith("Review elasticity: ") for e in events))
        self.assertEqual(sum(int(e["SUMMARY"].split(": ")[1].split()[0]) for e in events), 4)

    # ------------------------------------------------------------ progress + plan

    def test_progress_without_history_says_so(self):
        self.ingest()
        code, out, _ = self.run_cli("progress", "--today", "2026-10-30")
        self.assertEqual(code, 0)
        self.assertIn("No quiz attempts yet, so there is no history to show trends from.", out)
        self.assertNotIn("improving", out)
        self.assertNotIn("trend:", out)

    def test_progress_and_plan_show_trend_with_history(self):
        self.ingest()
        self.run_cli("quiz", "--topic", "elasticity", "--n", "4", "--answers", "A,A,A,A", "--today", "2026-10-30")
        data = self.db_data()
        items = list(data["quiz_items"].values())
        from course_compass.quiz import QuizItem
        right = ",".join(QuizItem.from_dict(raw).correct_letter() for raw in items)
        # answer the same cards correctly the next day through review
        code, out, _ = self.run_cli("review", "--today", "2026-10-31", "--answers", right)
        self.assertEqual(code, 0, out)
        code, out, _ = self.run_cli("progress", "--today", "2026-10-31")
        self.assertIn("8 attempt(s) since 2026-10-30", out)
        self.assertIn("by day: 2026-10-30", out)
        self.assertRegex(out, r"trend: (improving|steady|slipping) \d+% -> \d+%")
        code, out, _ = self.run_cli("plan", "--today", "2026-10-31")
        self.assertRegex(out, r"elasticity .*trend: ")
        self.assertIn("Run `progress`", out)

    # ------------------------------------------------------------ cram

    def test_cram_picks_weakest_topics_and_records_sm2(self):
        self.ingest()
        # make elasticity strong: answer 3/3 correctly
        from course_compass.quiz import local_quiz
        from tests.helpers import sample_chunks
        items = local_quiz(sample_chunks(), "elasticity", 3, seed=0)  # same questions `quiz` prints
        letters = ",".join(item.correct_letter() for item in items)
        self.run_cli("quiz", "--topic", "elasticity", "--n", "3", "--answers", letters, "--today", "2026-10-30")
        before = len(self.db_data()["attempts"])

        code, out, _ = self.run_cli("cram", "--n", "4", "--topics", "2", "--today", "2026-10-31")
        self.assertEqual(code, 0, out)
        self.assertIn("== CRAM: 4 rapid-fire question(s), weakest topics first:", out)
        self.assertIn("== LOCAL FALLBACK quiz (template questions built from your definitions, no LLM) ==", out)
        self.assertNotIn("[elasticity]", out)  # the strong topic is not drilled
        topics = [line.split("]")[0].split("[")[1] for line in out.splitlines() if line.startswith("Q")]
        self.assertEqual(topics, ["cell-respiration", "cell-respiration", "hypothesis-testing", "hypothesis-testing"])

        code, out, _ = self.run_cli("cram", "--n", "4", "--topics", "2", "--today", "2026-10-31",
                                    "--answers", "A,B,C,D")
        self.assertIn("Cram score:", out)
        data = self.db_data()
        new = data["attempts"][before:]
        self.assertEqual(len(new), 4)
        self.assertTrue(all(a["session"] == "cram" and a["date"] == "2026-10-31" for a in new))
        for a in new:
            card = data["cards"][a["item_id"]]
            self.assertEqual(card["due"], "2026-11-01")          # SM-2: 1 day after a first pass or a fail
            self.assertEqual(card["repetitions"], 1 if a["correct"] else 0)
        code, out, _ = self.run_cli("progress", "--today", "2026-10-31")
        self.assertIn("from cram", out)

    def test_cram_with_claude_uses_llm_quiz_and_its_label(self):
        self.ingest()
        item = {"question": "Which term is the share of income response?", "correct": "Income elasticity",
                "distractors": ["Cross-price elasticity", "Unit elastic", "Elastic demand"],
                "source_chunk_id": "elasticity#3"}
        fake = FakeClaude(json.dumps({"items": [item]}))
        a, b = with_claude(fake)
        with a, b, mock.patch.object(cli, "cram_topics", return_value=["elasticity"]):
            code, out, _ = self.run_cli("cram", "--n", "1", "--provider", "claude")
        self.assertEqual(code, 0, out)
        self.assertIn("== LLM quiz (Claude CLI: sonnet) (validated", out)
        self.assertEqual(fake.calls, 1)

    def test_cram_limits(self):
        self.ingest()
        code, _, err = self.run_cli("cram", "--n", "11")
        self.assertEqual(code, 2)
        self.assertIn("between 1 and 10", err)

    # ------------------------------------------------------------ help

    def test_help_lists_new_commands(self):
        out = io.StringIO()
        cli.build_parser().print_help(out)
        for name in ("simplify", "examples", "due", "progress", "cram"):
            self.assertIn(name, out.getvalue())
        self.assertIn("ics", cli.build_parser()._subparsers._group_actions[0].choices["export"].format_help())


if __name__ == "__main__":
    unittest.main()
