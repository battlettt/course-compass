"""Build the notes index for the hub: LEARN files plus your own notes folder, per course.

Sources, for each course:
  - files downloaded from LEARN content (slides, PDFs, Word, HTML pages)
  - anything you drop into ~/.course-compass/notes/<COURSE CODE>/

Extracted text is cached next to the files (keyed on size and modification
time), so a re-index only re-reads what changed. Chunks keep the existing
`topic#n` ids with the course in the topic (`cs-241-lecture-07#3`) and the
course code as the first part of the source path, so every answer can say
which course, file and page it came from.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..chunking import Chunk, chunk_document, slugify
from ..store import load_db, save_db
from .db import Hub
from .extract import SUPPORTED, ExtractError, extract_file

MAX_CHUNKS_PER_COURSE = 6000


@dataclass
class IndexReport:
    files: int = 0
    chunks: int = 0
    extracted: int = 0
    skipped: list[str] = field(default_factory=list)
    dropped_quiz_items: int = 0


def course_slug(code: str) -> str:
    return slugify(code)


def chunk_course(code: str) -> str:
    """Course code from a hub chunk's source path ("CS 241/Week 3/lecture.pdf" -> "CS 241")."""
    return code.split("/", 1)[0]


def _cache_key(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha1(f"{path}\x00{stat.st_size}\x00{stat.st_mtime_ns}".encode()).hexdigest()[:16]


def _extract_cached(hub: Hub, code: str, path: Path, title: str) -> tuple[str, str]:
    cache_dir = hub.text_dir / course_slug(code)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(path)
    cached = cache_dir / f"{key}.json"
    if cached.exists():
        data = json.loads(cached.read_text(encoding="utf-8"))
        return data["markdown"], data["status"]
    result = extract_file(path, title)
    cached.write_text(json.dumps({"markdown": result.markdown, "status": result.status, "method": result.method,
                                  "source": str(path)}), encoding="utf-8")
    return result.markdown, result.status


def gather_sources(hub: Hub) -> list[tuple[dict, Path, str, str, str | None]]:
    """(course, path, title, display source, files-table uid or None) for every indexable file."""
    out = []
    for course in hub.courses():
        code = course["code"]
        for row in hub.query("SELECT * FROM files WHERE course_id=? AND kind='file' AND local_path != ''",
                             (course["id"],)):
            path = Path(row["local_path"])
            if path.exists() and path.suffix.lower() in SUPPORTED:
                display = f"{code}/{row['module']}/{row['title']}".replace("//", "/")
                out.append((course, path, row["title"], display, row["uid"]))
        own = hub.notes_dir / code
        if own.is_dir():
            root = own.resolve()
            for path in sorted(own.rglob("*")):
                real = path.resolve()
                if path.is_file() and real.is_relative_to(root) and path.suffix.lower() in SUPPORTED:
                    display = f"{code}/My notes/{path.relative_to(own).as_posix()}"
                    out.append((course, real, path.stem.replace("-", " ").replace("_", " "), display, None))
    return out


def build_index(hub: Hub, max_words: int = 80, overlap: int = 20) -> IndexReport:
    report = IndexReport()
    chunks: list[Chunk] = []
    used_topics: set[str] = set()
    per_course: dict[str, int] = {}
    for course, path, title, display, uid in gather_sources(hub):
        code = course["code"]
        report.files += 1
        try:
            markdown, status = _extract_cached(hub, code, path, title)
        except (ExtractError, OSError, UnicodeDecodeError) as exc:
            status = "unsupported" if "unsupported" in str(exc) else "error"
            report.skipped.append(f"{display}: {exc}")
            markdown = ""
        if uid:
            with hub.tx() as conn:
                conn.execute("UPDATE files SET text_status=? WHERE uid=?", (status, uid))
        if status != "ok" or not markdown:
            if status in ("needs-ocr", "empty"):
                report.skipped.append(f"{display}: no text layer (scanned?); install tesseract + poppler for OCR")
            continue
        report.extracted += 1
        doc_chunks = chunk_document(markdown, display, max_words=max_words, overlap=overlap)
        if not doc_chunks:
            continue
        base = f"{course_slug(code)}-{slugify(Path(display).stem)}"[:70].strip("-")
        topic, n = base, 1
        while topic in used_topics:
            n += 1
            topic = f"{base}-{n}"
        used_topics.add(topic)
        room = MAX_CHUNKS_PER_COURSE - per_course.get(code, 0)
        if room <= 0:
            report.skipped.append(f"{display}: course chunk limit reached")
            continue
        for index, chunk in enumerate(doc_chunks[:room], start=1):
            chunk.topic = topic
            chunk.id = f"{topic}#{index}"
            chunk.title = title
            chunks.append(chunk)
        per_course[code] = per_course.get(code, 0) + min(len(doc_chunks), room)
    db = load_db(hub.study_db, must_exist=False)
    db["chunks"] = [chunk.to_dict() for chunk in chunks]
    db["settings"].update({"max_words": max_words, "overlap": overlap})
    known = {chunk.id for chunk in chunks}
    stale = [item_id for item_id, raw in db["quiz_items"].items() if raw["source_chunk_id"] not in known]
    for item_id in stale:
        db["quiz_items"].pop(item_id)
        db["cards"].pop(item_id, None)
    save_db(hub.study_db, db)
    report.chunks = len(chunks)
    report.dropped_quiz_items = len(stale)
    return report


def course_topics(db: dict, code: str) -> list[str]:
    prefix = course_slug(code) + "-"
    return sorted({c["topic"] for c in db["chunks"] if c["topic"].startswith(prefix)})


def topic_titles(db: dict) -> dict[str, dict]:
    """topic -> {"title", "course", "chunks"} for the UI."""
    out: dict[str, dict] = {}
    for chunk in db["chunks"]:
        entry = out.setdefault(chunk["topic"], {"title": chunk["title"], "course": chunk_course(chunk["source"]),
                                                "source": chunk["source"], "chunks": 0})
        entry["chunks"] += 1
    return out
