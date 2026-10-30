import json
import tempfile
import unittest
from pathlib import Path

from course_compass.store import StoreError, empty_db, load_db, save_db


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "db.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        db = empty_db()
        db["attempts"].append({"item_id": "x", "topic": "t", "correct": True})
        db["cards"]["x"] = {"ease": 2.5, "interval": 1, "repetitions": 1, "due": "2026-10-31"}
        save_db(self.path, db)
        self.assertEqual(load_db(self.path), db)

    def test_missing_db(self):
        with self.assertRaisesRegex(StoreError, "ingest"):
            load_db(self.path)
        self.assertEqual(load_db(self.path, must_exist=False), empty_db())

    def test_corrupt_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{ not json", encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "corrupt"):
            load_db(self.path)

    def test_wrong_version_or_shape(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"version": 99}), encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "unknown format"):
            load_db(self.path)
        bad = empty_db()
        bad["cards"] = []
        self.path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "cards"):
            load_db(self.path)

    def test_atomic_save_leaves_no_temp_files(self):
        save_db(self.path, empty_db())
        save_db(self.path, empty_db())
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["db.json"])


if __name__ == "__main__":
    unittest.main()
