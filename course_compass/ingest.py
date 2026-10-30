"""Safe note ingestion: allowed directories only, size caps, no path escapes."""

from __future__ import annotations

from pathlib import Path

from .chunking import DEFAULT_MAX_WORDS, DEFAULT_OVERLAP, Chunk, chunk_document

NOTE_SUFFIXES = (".md", ".markdown", ".txt")
MAX_NOTE_BYTES = 200_000
MAX_TOTAL_BYTES = 2_000_000
MAX_FILES = 200


class IngestError(Exception):
    pass


def guard_path(raw: str, allowed_roots: list[Path]) -> tuple[Path, Path]:
    """Return (resolved path, root it lives under) or raise IngestError.

    `..` components are rejected outright, and the resolved path (after
    following symlinks) must sit inside one of the allowed roots.
    """
    if not raw or "\x00" in raw:
        raise IngestError("Empty or invalid path.")
    if ".." in Path(raw).parts:
        raise IngestError(f"Refusing {raw!r}: '..' path components are not allowed.")
    resolved = Path(raw).resolve()
    for root in allowed_roots:
        root = root.resolve()
        if resolved == root or resolved.is_relative_to(root):
            return resolved, root
    roots = ", ".join(str(r) for r in allowed_roots)
    raise IngestError(f"Refusing {raw!r}: outside the allowed directories ({roots}). Use --allow-dir.")


def collect_files(raw_paths: list[str], allowed_roots: list[Path]) -> list[tuple[Path, Path]]:
    files: list[tuple[Path, Path]] = []
    for raw in raw_paths:
        path, root = guard_path(raw, allowed_roots)
        if not path.exists():
            raise IngestError(f"No such file or directory: {raw}")
        if path.is_dir():
            candidates = sorted(p for p in path.rglob("*") if p.suffix.lower() in NOTE_SUFFIXES)
        elif path.suffix.lower() in NOTE_SUFFIXES:
            candidates = [path]
        else:
            raise IngestError(f"Unsupported file type {path.suffix!r}; use .md or .txt notes.")
        for candidate in candidates:
            real = candidate.resolve()
            if real.is_file() and (real == root or real.is_relative_to(root)):
                files.append((real, root))
    if len(files) > MAX_FILES:
        raise IngestError(f"Too many note files ({len(files)}; limit {MAX_FILES}).")
    return files


def ingest_paths(raw_paths: list[str], allowed_roots: list[Path], max_words: int = DEFAULT_MAX_WORDS,
                 overlap: int = DEFAULT_OVERLAP) -> list[Chunk]:
    files = collect_files(raw_paths, allowed_roots)
    if not files:
        raise IngestError("No .md or .txt notes found.")
    chunks: list[Chunk] = []
    total = 0
    used_topics: set[str] = set()
    for path, root in files:
        size = path.stat().st_size
        if size > MAX_NOTE_BYTES:
            raise IngestError(f"{path.name} is too large ({size} bytes; limit {MAX_NOTE_BYTES}).")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise IngestError(f"Notes exceed the total size limit of {MAX_TOTAL_BYTES} bytes.")
        text = path.read_text(encoding="utf-8", errors="replace")
        source = path.relative_to(root).as_posix() if path != root else path.name
        doc_chunks = chunk_document(text, source, max_words=max_words, overlap=overlap)
        doc_chunks = _dedupe_topic(doc_chunks, used_topics)
        chunks.extend(doc_chunks)
    if not chunks:
        raise IngestError("The notes are empty: no text found to chunk.")
    return chunks


def _dedupe_topic(chunks: list[Chunk], used: set[str]) -> list[Chunk]:
    """Two files with the same name in different folders get topic, topic-2, ..."""
    if not chunks:
        return chunks
    base = chunks[0].topic
    topic, n = base, 1
    while topic in used:
        n += 1
        topic = f"{base}-{n}"
    used.add(topic)
    if topic != base:
        for chunk in chunks:
            chunk.topic = topic
            chunk.id = f"{topic}#{chunk.id.split('#')[1]}"
    return chunks
