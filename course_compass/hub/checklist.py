"""Break assignment instructions into a checklist, and search across everything in the hub."""

from __future__ import annotations

import re

from ..retrieval import TfidfIndex
from .db import Hub

_STEP_RES = [
    re.compile(r"^\s*(?:question|problem|q|exercise|task|part)\s*\(?(\d+|[a-z])\)?[.:)\-]?\s+(.{3,})$", re.I),
    re.compile(r"^\s*\(?(\d{1,2}|[a-h])[.)]\s+(.{3,})$", re.I),
    re.compile(r"^\s*[-*•]\s+(.{6,})$"),
]
DELIVERABLE_RE = re.compile(r"\b(submit|upload|include|hand in|deliverable|must|required|write|implement|"
                            r"answer|complete|prepare)\b", re.I)


def checklist_from_text(text: str, limit: int = 20) -> list[str]:
    """Numbered questions, parts and bullets from assignment instructions, else sentences with a deliverable."""
    steps: list[str] = []
    for line in text.splitlines():
        for regex in _STEP_RES:
            match = regex.match(line)
            if match:
                label = match.group(1) if match.lastindex and match.lastindex > 1 else ""
                body = match.group(match.lastindex).strip()
                steps.append((f"{label}. " if label and len(label) <= 2 else "") + body[:200])
                break
    if len(steps) < 2:
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
        steps = [s[:200] for s in sentences if DELIVERABLE_RE.search(s) and len(s) > 15]
    if len(steps) < 2:
        return []      # one sentence is not a checklist; let the student write their own steps
    seen, out = set(), []
    for step in steps:
        key = step.lower()
        if key not in seen:
            seen.add(key)
            out.append(step)
    out = out[:limit]
    out.append("Proofread, then submit on LEARN and check the confirmation")
    return out


def search(hub: Hub, query: str, index: TfidfIndex | None, limit: int = 30) -> list[dict]:
    """One search box for work items, announcements, files and note passages."""
    query = query.strip()
    if len(query) < 2:
        return []
    like = f"%{query}%"
    results: list[dict] = []
    for row in hub.query("""SELECT items.uid, items.title, items.kind, items.due, courses.code AS code FROM items
                            LEFT JOIN courses ON courses.id = items.course_id
                            WHERE items.hidden=0 AND (items.title LIKE ? OR items.details LIKE ?)
                            ORDER BY items.due LIMIT 10""", (like, like)):
        results.append({"type": "item", "id": row["uid"], "title": row["title"], "course": row["code"],
                        "meta": row["kind"], "when": row["due"]})
    for row in hub.query("""SELECT a.uid, a.title, a.posted, a.body_text, courses.code AS code FROM announcements a
                            LEFT JOIN courses ON courses.id = a.course_id
                            WHERE a.title LIKE ? OR a.body_text LIKE ? ORDER BY a.posted DESC LIMIT 8""",
                         (like, like)):
        results.append({"type": "announcement", "id": row["uid"], "title": row["title"], "course": row["code"],
                        "meta": row["body_text"][:120], "when": row["posted"]})
    for row in hub.query("""SELECT f.uid, f.title, f.module, courses.code AS code FROM files f
                            LEFT JOIN courses ON courses.id = f.course_id
                            WHERE f.title LIKE ? OR f.module LIKE ? LIMIT 8""", (like, like)):
        results.append({"type": "file", "id": row["uid"], "title": row["title"], "course": row["code"],
                        "meta": row["module"], "when": None})
    if index is not None and index.chunks:
        for hit in index.search(query, k=6):
            if hit.score < 0.1:
                continue
            results.append({"type": "note", "id": hit.chunk.id, "title": f"{hit.chunk.title} > {hit.chunk.heading}",
                            "course": hit.chunk.source.split("/", 1)[0], "meta": hit.chunk.text[:160],
                            "when": None, "score": round(hit.score, 3)})
    return results[:limit]
