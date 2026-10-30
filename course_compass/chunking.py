"""Heading-aware chunking of markdown notes.

A note is split into sections at markdown headings. Each section's blocks
(paragraphs and bullet lines) are packed into chunks of at most `max_words`
words. Consecutive chunks inside one section share `overlap` words; the
carried-over words are marked with a leading ellipsis so they are never
mistaken for a fresh definition line.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

DEFAULT_MAX_WORDS = 80
DEFAULT_OVERLAP = 20
OVERLAP_MARK = "… "

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")


@dataclass
class Chunk:
    id: str
    source: str
    topic: str
    title: str
    heading: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Chunk":
        return cls(**{name: data[name] for name in cls.__dataclass_fields__})


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "note"


def split_sections(markdown: str) -> list[tuple[str, str, list[str]]]:
    """Return (title, heading, blocks) per section.

    `title` is the first level-1 heading (or "" if none). `heading` is the
    nearest heading above the section. Blocks are paragraphs (joined lines)
    and individual bullet lines.
    """
    title = ""
    heading = ""
    sections: list[tuple[str, str, list[str]]] = []
    blocks: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(" ".join(paragraph))
            paragraph.clear()

    def flush_section() -> None:
        flush_paragraph()
        if blocks:
            sections.append((title, heading, list(blocks)))
            blocks.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        match = _HEADING_RE.match(line)
        if match:
            flush_section()
            heading = match.group(2).strip()
            if len(match.group(1)) == 1 and not title:
                title = heading
            continue
        if not line:
            flush_paragraph()
        elif _BULLET_RE.match(line):
            flush_paragraph()
            blocks.append(line)
        else:
            paragraph.append(line)
    flush_section()
    return sections


def _split_long_block(block: str, max_words: int) -> list[str]:
    words = block.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


def pack_blocks(blocks: list[str], max_words: int, overlap: int) -> list[str]:
    """Greedily pack blocks into chunk texts of at most `max_words` words
    (plus up to `overlap` carried-over words from the previous chunk)."""
    if max_words < 1:
        raise ValueError("max_words must be at least 1")
    if overlap < 0 or overlap >= max_words:
        raise ValueError("overlap must be between 0 and max_words - 1")

    pieces: list[str] = []
    for block in blocks:
        if len(block.split()) > max_words:
            pieces.extend(_split_long_block(block, max_words))
        else:
            pieces.append(block)

    chunks: list[list[str]] = []
    current: list[str] = []
    count = 0
    for piece in pieces:
        size = len(piece.split())
        if current and count + size > max_words:
            chunks.append(current)
            current, count = [], 0
        current.append(piece)
        count += size
    if current:
        chunks.append(current)

    texts: list[str] = []
    previous_words: list[str] = []
    for lines in chunks:
        body = "\n".join(lines)
        if overlap and previous_words:
            tail = " ".join(previous_words[-overlap:])
            body = f"{OVERLAP_MARK}{tail}\n{body}"
        texts.append(body)
        previous_words = " ".join(lines).split()
    return texts


def chunk_document(markdown: str, source: str, max_words: int = DEFAULT_MAX_WORDS,
                   overlap: int = DEFAULT_OVERLAP) -> list[Chunk]:
    """Split one markdown note into chunks with stable ids like `elasticity#3`."""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", source.replace("\\", "/").split("/")[-1])
    topic = slugify(stem)
    chunks: list[Chunk] = []
    for title, heading, blocks in split_sections(markdown):
        for text in pack_blocks(blocks, max_words, overlap):
            chunks.append(Chunk(
                id=f"{topic}#{len(chunks) + 1}",
                source=source,
                topic=topic,
                title=title or stem,
                heading=heading,
                text=text,
            ))
    return chunks
