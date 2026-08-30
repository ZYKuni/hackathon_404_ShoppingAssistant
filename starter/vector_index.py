"""Compact, dependency-free in-memory TF-IDF index for semantic retrieval.

The index stores sparse postings in typed arrays rather than Python tuples so the
50,000-product catalog remains practical in memory.  It is shared by Browsing
retrieval and the optional Top-N semantic reranker.
"""

from __future__ import annotations

import heapq
import json
import math
import re
import time
from array import array
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "but", "by",
    "do", "does", "for", "from", "have", "i", "in", "is", "it", "me",
    "my", "need", "of", "on", "or", "please", "some", "that", "the",
    "this", "to", "want", "with", "would", "you", "your",
}
GENERIC_CATEGORIES = {
    "clothing", "shoes", "jewelry", "clothing shoes jewelry",
    "clothing shoes & jewelry", "clothing, shoes & jewelry",
}


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    ]


def _category_key(value: object) -> str:
    categories = value if isinstance(value, list) else [value]
    pieces: list[str] = []
    for category in categories:
        pieces.extend(part.strip().lower() for part in str(category).split(","))
    useful = [item for item in pieces if item and item not in GENERIC_CATEGORIES]
    return useful[-1] if useful else "unknown"


@dataclass(frozen=True)
class VectorHit:
    parent_asin: str
    score: float


@dataclass(frozen=True)
class VectorIndexStats:
    document_count: int
    vocabulary_size: int
    posting_count: int
    estimated_posting_bytes: int
    initialization_ms: float


class InMemoryTfidfIndex:
    """Sparse word TF-IDF cosine index with deterministic ranking."""

    def __init__(
        self,
        catalog_path: str | Path,
        *,
        max_terms_per_document: int = 96,
        min_document_frequency: int = 2,
        max_document_frequency_ratio: float = 0.85,
    ) -> None:
        if max_terms_per_document < 1:
            raise ValueError("max_terms_per_document must be positive")
        if min_document_frequency < 1:
            raise ValueError("min_document_frequency must be positive")
        if not 0.0 < max_document_frequency_ratio <= 1.0:
            raise ValueError("max_document_frequency_ratio must be in (0, 1]")
        self.catalog_path = Path(catalog_path)
        if not self.catalog_path.is_file():
            raise FileNotFoundError(f"catalog not found: {self.catalog_path}")
        self.max_terms_per_document = max_terms_per_document
        self.min_document_frequency = min_document_frequency
        self.max_document_frequency_ratio = max_document_frequency_ratio
        self._asins: list[str] = []
        self._doc_by_asin: dict[str, int] = {}
        self._categories: dict[str, str] = {}
        self._postings: dict[str, tuple[array, array]] = {}
        self._idf: dict[str, float] = {}
        self._norms = array("f")
        started = time.perf_counter()
        posting_count = self._build()
        self.stats = VectorIndexStats(
            document_count=len(self._asins),
            vocabulary_size=len(self._postings),
            posting_count=posting_count,
            estimated_posting_bytes=posting_count * 8 + len(self._norms) * 4,
            initialization_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _build(self) -> int:
        mutable: dict[str, tuple[array, array]] = {}
        with self.catalog_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                product = json.loads(line)
                asin = str(product.get("parent_asin") or "").strip()
                if not asin:
                    raise ValueError(f"catalog line {line_number} is missing parent_asin")
                if asin in self._doc_by_asin:
                    raise ValueError(f"duplicate parent_asin: {asin}")
                doc_id = len(self._asins)
                self._asins.append(asin)
                self._doc_by_asin[asin] = doc_id
                self._categories[asin] = _category_key(product.get("categories"))
                # Repetition supplies a small, explicit field prior while TF-IDF
                # remains the common vector representation.
                document = " ".join((
                    _text(product.get("title")),
                    _text(product.get("title")),
                    _text(product.get("categories")),
                    _text(product.get("categories")),
                    _text(product.get("features")),
                    _text(product.get("description")),
                ))
                counts = Counter(_tokens(document))
                selected = heapq.nlargest(
                    self.max_terms_per_document,
                    counts.items(),
                    key=lambda item: (item[1], item[0]),
                )
                for token, count in selected:
                    doc_ids, weights = mutable.setdefault(
                        token, (array("I"), array("f"))
                    )
                    doc_ids.append(doc_id)
                    weights.append(1.0 + math.log(float(count)))

        document_count = len(self._asins)
        max_df = max(1, int(document_count * self.max_document_frequency_ratio))
        self._norms = array("f", [0.0]) * document_count
        posting_count = 0
        for token, (doc_ids, weights) in mutable.items():
            document_frequency = len(doc_ids)
            if not self.min_document_frequency <= document_frequency <= max_df:
                continue
            idf = math.log((1.0 + document_count) / (1.0 + document_frequency)) + 1.0
            self._idf[token] = idf
            for index, doc_id in enumerate(doc_ids):
                weight = float(weights[index]) * idf
                weights[index] = weight
                self._norms[doc_id] += weight * weight
            self._postings[token] = (doc_ids, weights)
            posting_count += document_frequency
        for index, squared_norm in enumerate(self._norms):
            self._norms[index] = math.sqrt(float(squared_norm))
        return posting_count

    @staticmethod
    def _query_weights(text: str, idf: dict[str, float]) -> tuple[dict[str, float], float]:
        counts = Counter(_tokens(text))
        weights = {
            token: (1.0 + math.log(float(count))) * idf[token]
            for token, count in counts.items()
            if token in idf
        }
        norm = math.sqrt(sum(value * value for value in weights.values()))
        return weights, norm

    def search(self, query: str, limit: int) -> tuple[VectorHit, ...]:
        if limit < 1:
            return ()
        query_weights, query_norm = self._query_weights(query, self._idf)
        if query_norm == 0.0:
            return ()
        dots: dict[int, float] = {}
        for token, query_weight in query_weights.items():
            doc_ids, weights = self._postings[token]
            for doc_id, document_weight in zip(doc_ids, weights):
                dots[doc_id] = dots.get(doc_id, 0.0) + query_weight * document_weight
        scored = [
            VectorHit(self._asins[doc_id], dot / (query_norm * self._norms[doc_id]))
            for doc_id, dot in dots.items()
            if self._norms[doc_id] > 0.0
        ]
        scored.sort(key=lambda item: (-item.score, item.parent_asin))
        return tuple(scored[:limit])

    def score_many(self, query: str, parent_asins: Iterable[str]) -> dict[str, float]:
        targets = {
            self._doc_by_asin[asin]: asin
            for asin in parent_asins
            if asin in self._doc_by_asin
        }
        query_weights, query_norm = self._query_weights(query, self._idf)
        if not targets or query_norm == 0.0:
            return {}
        dots = {doc_id: 0.0 for doc_id in targets}
        for token, query_weight in query_weights.items():
            doc_ids, weights = self._postings[token]
            for doc_id, document_weight in zip(doc_ids, weights):
                if doc_id in targets:
                    dots[doc_id] += query_weight * document_weight
        return {
            targets[doc_id]: dot / (query_norm * self._norms[doc_id])
            for doc_id, dot in dots.items()
            if dot > 0.0 and self._norms[doc_id] > 0.0
        }

    def category_key(self, parent_asin: str) -> str:
        return self._categories.get(parent_asin, "unknown")


__all__ = ["InMemoryTfidfIndex", "VectorHit", "VectorIndexStats"]
