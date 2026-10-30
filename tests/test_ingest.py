import os
import tempfile
import unittest
from pathlib import Path

from course_compass import ingest
from course_compass.ingest import IngestError, guard_path, ingest_paths
from tests.helpers import NOTES, ROOT, sample_chunks


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_sample_notes_ingest(self):
        chunks = sample_chunks()
        self.assertEqual({c.topic for c in chunks},
                         {"cell-respiration", "elasticity", "hypothesis-testing", "water-cycle"})
        self.assertEqual(len({c.id for c in chunks}), len(chunks))

    def test_dotdot_rejected(self):
        with self.assertRaisesRegex(IngestError, r"'\.\.'"):
            guard_path(str(NOTES / ".." / "notes"), [ROOT])

    def test_outside_allowed_root_rejected(self):
        with self.assertRaisesRegex(IngestError, "outside the allowed"):
            guard_path(str(NOTES), [self.root])

    def test_symlink_escape_skipped(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = Path(outside.name) / "secret.md"
        secret.write_text("# Secret\n\nprivate text", encoding="utf-8")
        (self.root / "ok.md").write_text("# Ok\n\nfine text", encoding="utf-8")
        os.symlink(secret, self.root / "link.md")
        chunks = ingest_paths([str(self.root)], [self.root])
        self.assertEqual([c.topic for c in chunks], ["ok"])

    def test_empty_notes_and_no_files(self):
        (self.root / "blank.md").write_text("   \n\n", encoding="utf-8")
        with self.assertRaisesRegex(IngestError, "empty"):
            ingest_paths([str(self.root)], [self.root])
        (self.root / "blank.md").unlink()
        with self.assertRaisesRegex(IngestError, "No .md"):
            ingest_paths([str(self.root)], [self.root])

    def test_size_cap_and_file_type(self):
        big = self.root / "big.md"
        big.write_text("word " * (ingest.MAX_NOTE_BYTES // 4), encoding="utf-8")
        with self.assertRaisesRegex(IngestError, "too large"):
            ingest_paths([str(big)], [self.root])
        pdf = self.root / "notes.pdf"
        pdf.write_bytes(b"%PDF")
        with self.assertRaisesRegex(IngestError, "Unsupported"):
            ingest_paths([str(pdf)], [self.root])

    def test_duplicate_file_names_get_distinct_topics(self):
        for folder in ("a", "b"):
            (self.root / folder).mkdir()
            (self.root / folder / "notes.md").write_text("# N\n\ntext here", encoding="utf-8")
        chunks = ingest_paths([str(self.root)], [self.root])
        self.assertEqual(sorted(c.id for c in chunks), ["notes#1", "notes-2#1"])


if __name__ == "__main__":
    unittest.main()
