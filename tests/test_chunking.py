import unittest

from course_compass.chunking import (OVERLAP_MARK, Chunk, chunk_document, pack_blocks, slugify,
                                     split_sections)

NOTE = """# Biology

## Cells

Cells are the basic unit of life.

- **Nucleus**: holds the genetic material.
- **Membrane**: controls what enters the cell.

## Energy

ATP carries energy.
"""


class SplitSectionsTests(unittest.TestCase):
    def test_splits_at_headings_and_keeps_title(self):
        sections = split_sections(NOTE)
        self.assertEqual([heading for _, heading, _ in sections], ["Cells", "Energy"])
        self.assertTrue(all(title == "Biology" for title, _, _ in sections))

    def test_bullets_become_separate_blocks(self):
        _, _, blocks = split_sections(NOTE)[0]
        self.assertEqual(blocks[0], "Cells are the basic unit of life.")
        self.assertEqual(blocks[1], "- **Nucleus**: holds the genetic material.")
        self.assertEqual(len(blocks), 3)

    def test_multi_line_paragraph_is_joined(self):
        sections = split_sections("# T\n\nline one\nline two\n")
        self.assertEqual(sections[0][2], ["line one line two"])


class PackBlocksTests(unittest.TestCase):
    def test_respects_max_words(self):
        blocks = [" ".join(["w"] * 10)] * 5
        texts = pack_blocks(blocks, max_words=25, overlap=0)
        self.assertEqual(len(texts), 3)
        self.assertTrue(all(len(t.split()) <= 25 for t in texts))

    def test_overlap_carries_previous_tail_with_marker(self):
        blocks = ["a1 a2 a3 a4 a5", "b1 b2 b3 b4 b5"]
        texts = pack_blocks(blocks, max_words=5, overlap=2)
        self.assertEqual(texts[0], "a1 a2 a3 a4 a5")
        self.assertTrue(texts[1].startswith(OVERLAP_MARK + "a4 a5\n"))
        self.assertTrue(texts[1].endswith("b1 b2 b3 b4 b5"))

    def test_long_block_is_split(self):
        texts = pack_blocks([" ".join(str(i) for i in range(12))], max_words=5, overlap=0)
        self.assertEqual(len(texts), 3)

    def test_rejects_bad_overlap(self):
        with self.assertRaises(ValueError):
            pack_blocks(["x"], max_words=5, overlap=5)
        with self.assertRaises(ValueError):
            pack_blocks(["x"], max_words=0, overlap=0)


class ChunkDocumentTests(unittest.TestCase):
    def test_empty_input_gives_no_chunks(self):
        self.assertEqual(chunk_document("", "empty.md"), [])
        self.assertEqual(chunk_document("# Only a heading\n\n   \n", "h.md"), [])

    def test_ids_topic_and_heading(self):
        chunks = chunk_document(NOTE, "notes/Bio Basics.md")
        self.assertEqual([c.id for c in chunks], ["bio-basics#1", "bio-basics#2"])
        self.assertEqual(chunks[0].heading, "Cells")
        self.assertEqual(chunks[1].heading, "Energy")
        self.assertEqual(chunks[0].title, "Biology")

    def test_overlap_never_crosses_sections(self):
        chunks = chunk_document(NOTE, "bio.md", max_words=80, overlap=20)
        self.assertFalse(chunks[1].text.startswith(OVERLAP_MARK))

    def test_round_trip_dict(self):
        chunk = chunk_document(NOTE, "bio.md")[0]
        self.assertEqual(Chunk.from_dict(chunk.to_dict()), chunk)

    def test_slugify(self):
        self.assertEqual(slugify("Hypothesis Testing!"), "hypothesis-testing")
        self.assertEqual(slugify("???"), "note")


if __name__ == "__main__":
    unittest.main()
