"""Local JSON storage for chunks, quiz items, review cards and attempts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1


class StoreError(Exception):
    pass


def empty_db() -> dict:
    return {"version": SCHEMA_VERSION, "settings": {}, "chunks": [],
            "quiz_items": {}, "cards": {}, "attempts": []}


def load_db(path: str | Path, must_exist: bool = True) -> dict:
    path = Path(path)
    if not path.exists():
        if must_exist:
            raise StoreError(f"No database at {path}. Run `ingest` first.")
        return empty_db()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreError(f"Database {path} is corrupt ({exc}). Delete it and re-run `ingest`.") from None
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        raise StoreError(f"Database {path} has an unknown format. Delete it and re-run `ingest`.")
    for key, value in empty_db().items():
        if not isinstance(data.get(key), type(value)):
            raise StoreError(f"Database {path} is corrupt (bad {key!r} field).")
    return data


def save_db(path: str | Path, data: dict) -> None:
    """Write atomically so a crash never leaves a half-written database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".db-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
