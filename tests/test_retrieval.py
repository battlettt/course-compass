import unittest

from course_compass.chunking import Chunk
from course_compass.retrieval import STOPWORDS, TfidfIndex, stem, tokenize
from tests.helpers import sample_chunks


def make(cid, heading, text, topic="t"):
    return Chunk(id=cid, source="t.md", topic=topic, title="T", heading=heading, text=text)


class TokenizeTests(unittest.TestCase):
    def test_stopwords_removed_and_lowercased(self):
        self.assertEqual(tokenize("What is THE p-value?"), ["p", "value"])

    def test_question_words_are_stopwords(self):
        for word in ("explain", "define", "what"):
            self.assertIn(word, STOPWORDS)

    def test_plural_folding(self):
        self.assertEqual(stem("hypotheses"), "hypothese")
        self.assertEqual(stem("studies"), "study")
        self.assertEqual(stem("analysis"), "analysis")
        self.assertEqual(tokenize("errors"), tokenize("error"))


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = TfidfIndex(sample_chunks())

    def test_known_query_returns_known_chunk_first(self):
        hit = self.index.search("What is a Type I error?")[0]
        self.assertEqual(hit.chunk.id, "hypothesis-testing#4")
        self.assertIn("Type I error", hit.chunk.text)

    def test_runoff_query_ranks_water_cycle(self):
        hit = self.index.search("Why do urban areas flood more?")[0]
        self.assertEqual(hit.chunk.topic, "water-cycle")
        self.assertIn("impervious", hit.chunk.text)

    def test_results_sorted_and_limited(self):
        hits = self.index.search("elasticity demand price", k=3)
        self.assertEqual(len(hits), 3)
        scores = [h.score for h in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(0 < s <= 1.0001 for s in scores))

    def test_only_stopwords_returns_nothing(self):
        self.assertEqual(self.index.search("what is the and of"), [])

    def test_unknown_words_return_nothing(self):
        self.assertEqual(self.index.search("sourdough bread baking"), [])

    def test_topic_filter(self):
        hits = self.index.search("percentage change", topic="elasticity")
        self.assertTrue(hits)
        self.assertTrue(all(h.chunk.topic == "elasticity" for h in hits))

    def test_empty_corpus(self):
        self.assertEqual(TfidfIndex([]).search("anything"), [])

    def test_heading_words_boost_ranking(self):
        index = TfidfIndex([make("t#1", "Notes", "osmosis moves water"),
                            make("t#2", "Osmosis", "water moves across membranes")])
        self.assertEqual(index.search("osmosis")[0].chunk.id, "t#2")


if __name__ == "__main__":
    unittest.main()
