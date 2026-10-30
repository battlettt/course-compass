"""Local TF-IDF retrieval with cosine similarity (standard library only)."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .chunking import Chunk

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because
been before being below between both but by can could did do does doing down
during each few for from further had has have having he her here hers him his
how i if in into is it its itself just me more most my no nor not now of off on
once only or other our out over own same she should so some such than that the
their them then there these they this those through to too under until up very
was we were what when where which while who whom why will with would you your
explain describe define definition tell give show
""".split())

HEADING_WEIGHT = 2

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def stem(word: str) -> str:
    """A deliberately tiny stemmer: fold common plural endings only."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return [stem(tok) for tok in _TOKEN_RE.findall(text.lower()) if tok not in STOPWORDS]


@dataclass
class Hit:
    chunk: Chunk
    score: float


class TfidfIndex:
    """Cosine-similarity search over TF-IDF vectors of chunks.

    Heading words are counted HEADING_WEIGHT times so a chunk under
    "Errors in testing" ranks well for a question about testing errors.
    """

    def __init__(self, chunks: list[Chunk]):
        self.chunks = list(chunks)
        self.idf: dict[str, float] = {}
        self.vectors: list[dict[str, float]] = []
        self._build()

    def _chunk_tokens(self, chunk: Chunk) -> list[str]:
        return tokenize(chunk.heading) * HEADING_WEIGHT + tokenize(chunk.text)

    def _build(self) -> None:
        token_lists = [self._chunk_tokens(chunk) for chunk in self.chunks]
        n_docs = len(token_lists)
        doc_freq: Counter = Counter()
        for tokens in token_lists:
            doc_freq.update(set(tokens))
        # Smoothed idf, always positive.
        self.idf = {term: math.log((1 + n_docs) / (1 + df)) + 1 for term, df in doc_freq.items()}
        self.vectors = [self._vectorize(tokens) for tokens in token_lists]

    def _vectorize(self, tokens: list[str]) -> dict[str, float]:
        counts = Counter(tok for tok in tokens if tok in self.idf)
        vector = {term: (1 + math.log(tf)) * self.idf[term] for term, tf in counts.items()}
        norm = math.sqrt(sum(v * v for v in vector.values()))
        if norm == 0:
            return {}
        return {term: v / norm for term, v in vector.items()}

    def search(self, query: str, k: int = 4, topic: str | None = None) -> list[Hit]:
        """Return up to k hits with score > 0, best first (ties keep note order)."""
        query_vec = self._vectorize(tokenize(query))
        if not query_vec:
            return []
        hits = []
        for chunk, vector in zip(self.chunks, self.vectors):
            if topic and chunk.topic != topic:
                continue
            score = sum(weight * vector.get(term, 0.0) for term, weight in query_vec.items())
            if score > 0:
                hits.append(Hit(chunk, score))
        hits.sort(key=lambda hit: -hit.score)
        return hits[:k]
