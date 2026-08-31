"""Deterministic in-memory hybrid lexical retrieval with weighted RRF.

The public output is the immutable ``CandidatePool`` contract.  Route queries,
FTS details, and failure handling remain Aaron-internal implementation details.
"""

from __future__ import annotations

import heapq
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import AbstractSet, Mapping, Protocol, Sequence

from .pipeline_contracts import (
    Candidate,
    CandidatePool,
    IntentRoute,
    RetrievalError,
    RouteEvidence,
    SearchRequest,
)
from .dense_retrieval import DenseMode, DenseRouteDiagnostics, DenseSearchBackend
from .embedding_text import build_query_embedding_text
from .retrieval_types import SearchHit


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "but", "by",
    "do", "does", "for", "from", "have", "i", "in", "is", "it", "me",
    "my", "need", "of", "on", "or", "please", "some", "that", "the",
    "this", "to", "want", "with", "would", "you", "your",
}

ACTIVE_CONTEXT_ROUTE = "active_context_bm25"
CURRENT_TURN_ROUTE = "current_turn_bm25"
CATEGORY_ANCHOR_ROUTE = "category_anchor_bm25"
STRUCTURED_CONSTRAINT_ROUTE = "structured_constraint_bm25"
USE_CASE_ROUTE = "use_case_bm25"
DENSE_SEMANTIC_ROUTE = "dense_semantic"
POPULARITY_ROUTE = "popularity_fallback"

BUYING_ROUTE_WEIGHTS: Mapping[str, float] = {
    ACTIVE_CONTEXT_ROUTE: 1.40,
    CURRENT_TURN_ROUTE: 0.85,
    CATEGORY_ANCHOR_ROUTE: 0.25,
    STRUCTURED_CONSTRAINT_ROUTE: 0.75,
}

BROWSING_ROUTE_WEIGHTS: Mapping[str, float] = {
    ACTIVE_CONTEXT_ROUTE: 1.10,
    CURRENT_TURN_ROUTE: 0.55,
    CATEGORY_ANCHOR_ROUTE: 0.35,
    USE_CASE_ROUTE: 0.75,
}


class RetrievalRouteError(Exception):
    """One optional retrieval route failed while other routes may continue."""


class SearchBackend(Protocol):
    @property
    def valid_asins(self) -> frozenset[str]: ...

    def search(self, query: str, limit: int) -> Sequence[SearchHit]: ...

    def popularity(self, limit: int) -> Sequence[SearchHit]: ...


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _terms(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    ))


class SQLiteCatalogSearchIndex:
    """Read-only catalog projected into an in-memory SQLite FTS5 index."""

    def __init__(self, catalog_path: str | Path) -> None:
        self.catalog_path = Path(catalog_path)
        if not self.catalog_path.is_file():
            raise FileNotFoundError(f"catalog not found: {self.catalog_path}")
        self.connection = sqlite3.connect(":memory:")
        self._valid_asins: frozenset[str] = frozenset()
        self._popular: tuple[SearchHit, ...] = ()
        self._build()

    @property
    def valid_asins(self) -> frozenset[str]:
        return self._valid_asins

    def _build(self) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "CREATE VIRTUAL TABLE products USING fts5("
                "parent_asin UNINDEXED, title, categories, features, details, store, description, "
                "tokenize='porter unicode61 remove_diacritics 2')"
            )
        except sqlite3.Error as error:
            raise RetrievalError("SQLite FTS5 index is unavailable") from error

        batch: list[tuple[str, str, str, str, str, str, str]] = []
        asins: set[str] = set()
        popularity: list[tuple[float, str]] = []
        with self.catalog_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                product = json.loads(line)
                if not isinstance(product, dict):
                    raise RetrievalError(f"catalog line {line_number} is not an object")
                parent_asin = str(product.get("parent_asin") or "").strip()
                if not parent_asin:
                    raise RetrievalError(f"catalog line {line_number} is missing parent_asin")
                if parent_asin in asins:
                    raise RetrievalError(f"duplicate parent_asin: {parent_asin}")
                asins.add(parent_asin)
                batch.append((
                    parent_asin,
                    _text(product.get("title")),
                    _text(product.get("categories")),
                    _text(product.get("features")),
                    _text(product.get("details")),
                    _text(product.get("store")),
                    _text(product.get("description")),
                ))
                rating = float(product.get("average_rating") or 0.0)
                rating_count = int(product.get("rating_number") or 0)
                popularity_score = rating_count * max(rating, 0.1)
                heapq.heappush(popularity, (popularity_score, parent_asin))
                if len(popularity) > 200:
                    heapq.heappop(popularity)
                if len(batch) >= 1000:
                    cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
        if batch:
            cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        self.connection.commit()
        self._valid_asins = frozenset(asins)
        self._popular = tuple(
            SearchHit(asin, score)
            for score, asin in sorted(popularity, key=lambda item: (-item[0], item[1]))
        )

    def search(self, query: str, limit: int) -> Sequence[SearchHit]:
        terms = _terms(query)[:60]
        if not terms:
            return ()
        expression = " OR ".join(f'"{term}"' for term in terms)
        return self._search_expression(expression, int(limit))

    @lru_cache(maxsize=8192)
    def _search_expression(self, expression: str, limit: int) -> tuple[SearchHit, ...]:
        """Cache deterministic searches against the frozen in-memory catalog."""
        try:
            rows = self.connection.execute(
                "SELECT parent_asin, bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) "
                "FROM products WHERE products MATCH ? ORDER BY 2, parent_asin LIMIT ?",
                (expression, limit),
            ).fetchall()
        except sqlite3.Error as error:
            raise RetrievalRouteError(str(error)) from error
        return tuple(SearchHit(str(row[0]), float(row[1])) for row in rows)

    def search_legacy(
        self,
        query: str,
        limit: int,
        *,
        stopwords: AbstractSet[str] = frozenset(STOPWORDS),
    ) -> Sequence[SearchHit]:
        """Preserve the published Legacy BM25 ordering for guarded Top-K recall."""
        terms = tuple(dict.fromkeys(
            token.lower()
            for token in TOKEN_RE.findall(query)
            if len(token) > 1 and token.lower() not in stopwords
        ))[:60]
        if not terms:
            return ()
        expression = " OR ".join(f'"{term}"' for term in terms)
        return self._search_legacy_expression(expression, int(limit))

    @lru_cache(maxsize=8192)
    def _search_legacy_expression(
        self, expression: str, limit: int
    ) -> tuple[SearchHit, ...]:
        try:
            rows = self.connection.execute(
                "SELECT parent_asin, bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) "
                "FROM products WHERE products MATCH ? ORDER BY 2 LIMIT ?",
                (expression, limit),
            ).fetchall()
        except sqlite3.Error as error:
            raise RetrievalRouteError(str(error)) from error
        return tuple(SearchHit(str(row[0]), float(row[1])) for row in rows)

    def popularity(self, limit: int) -> Sequence[SearchHit]:
        return self._popular[:limit]


@dataclass(frozen=True)
class _RouteSpec:
    name: str
    query: str
    weight: float
    dense: bool = False


class HybridRetriever:
    """Run route-specific lexical retrieval and return a pre-filter Top-200 pool."""

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        backend: SearchBackend | None = None,
        dense_backend: DenseSearchBackend | None = None,
        dense_mode: DenseMode | str = DenseMode.OFF,
        per_route_limit: int = 120,
        dense_route_limit: int = 120,
        dense_route_weight: float = 0.35,
        rrf_k: float = 60.0,
    ) -> None:
        if per_route_limit < 1:
            raise ValueError("per_route_limit must be positive")
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        if dense_route_limit < 1:
            raise ValueError("dense_route_limit must be positive")
        if isinstance(dense_route_weight, bool) or not isinstance(
            dense_route_weight, (int, float)
        ):
            raise TypeError("dense_route_weight must be numeric")
        if not 0.0 <= float(dense_route_weight) <= 2.0:
            raise ValueError("dense_route_weight must be between 0 and 2")
        self.backend = backend or SQLiteCatalogSearchIndex(catalog_path)
        self.dense_backend = dense_backend
        self.dense_mode = DenseMode(dense_mode)
        self.per_route_limit = per_route_limit
        self.dense_route_limit = dense_route_limit
        self.dense_route_weight = float(dense_route_weight)
        self.rrf_k = float(rrf_k)
        self._dense_diagnostics: dict[tuple[str, int], DenseRouteDiagnostics] = {}

    def dense_diagnostics(self, session_id: str, turn: int) -> DenseRouteDiagnostics:
        """Return aggregate diagnostics for one request, defaulting to OFF."""
        return self._dense_diagnostics.get(
            (session_id, turn), DenseRouteDiagnostics(mode=self.dense_mode)
        )

    @staticmethod
    def _constraint_query(request: SearchRequest, field: str | None = None) -> str:
        values: list[str] = []
        for terms in (request.state.hard_constraints, request.state.soft_preferences):
            for term in terms:
                if field is None or term.field == field:
                    values.extend(str(value).replace("_", " ") for value in term.values)
        return " ".join(dict.fromkeys(values))

    @classmethod
    def _route_specs(
        cls,
        request: SearchRequest,
        *,
        include_dense: bool = False,
        dense_route_weight: float = 0.35,
    ) -> tuple[_RouteSpec, ...]:
        active = request.raw_context.strip() or request.structured_query.strip() or request.current_message
        category = (
            request.state.category.replace("_", " ")
            if request.state.category
            else request.base_request
        )
        if request.route_decision.route is IntentRoute.BUYING:
            queries = {
                ACTIVE_CONTEXT_ROUTE: active,
                CURRENT_TURN_ROUTE: request.current_message,
                CATEGORY_ANCHOR_ROUTE: category,
                STRUCTURED_CONSTRAINT_ROUTE: (
                    request.structured_query.strip() or cls._constraint_query(request)
                ),
            }
            weights = BUYING_ROUTE_WEIGHTS
        else:
            use_case = cls._constraint_query(request, "use_case")
            queries = {
                ACTIVE_CONTEXT_ROUTE: active,
                CURRENT_TURN_ROUTE: request.current_message,
                CATEGORY_ANCHOR_ROUTE: " ".join(
                    dict.fromkeys(value for value in (category, request.base_request) if value)
                ),
                USE_CASE_ROUTE: use_case or request.base_request,
            }
            weights = BROWSING_ROUTE_WEIGHTS
        specs = tuple(
            _RouteSpec(name, queries[name], weight) for name, weight in weights.items()
        )
        if include_dense and request.route_decision.route is IntentRoute.BROWSING:
            specs += (_RouteSpec(
                DENSE_SEMANTIC_ROUTE,
                build_query_embedding_text(request),
                float(dense_route_weight),
                dense=True,
            ),)
        return specs

    def retrieve(self, request: SearchRequest) -> CandidatePool:
        if not isinstance(request, SearchRequest):
            raise TypeError("request must be a SearchRequest")
        started = time.perf_counter()
        dense_enabled = (
            self.dense_mode is not DenseMode.OFF and self.dense_backend is not None
        )
        specs = self._route_specs(
            request,
            include_dense=dense_enabled,
            dense_route_weight=self.dense_route_weight,
        )
        nonempty_specs = tuple(spec for spec in specs if _terms(spec.query))
        if not nonempty_specs:
            candidates = self._popularity_candidates(request.candidate_limit)
            return CandidatePool(
                candidates=candidates,
                requested_limit=request.candidate_limit,
                route=request.route_decision.route,
                retrieval_latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        route_results: list[tuple[_RouteSpec, tuple[SearchHit, ...]]] = []
        lexical_results: list[tuple[_RouteSpec, tuple[SearchHit, ...]]] = []
        dense_result: tuple[_RouteSpec, tuple[SearchHit, ...]] | None = None
        lexical_failures = 0
        lexical_route_count = sum(not spec.dense for spec in nonempty_specs)
        dense_diagnostic = DenseRouteDiagnostics(mode=self.dense_mode)
        for spec in nonempty_specs:
            if spec.dense:
                dense_started = time.perf_counter()
                try:
                    assert self.dense_backend is not None
                    hits = tuple(
                        self.dense_backend.search(spec.query, self.dense_route_limit)
                    )
                    dense_result = (spec, hits)
                    dense_diagnostic = DenseRouteDiagnostics(
                        mode=self.dense_mode,
                        attempted=True,
                        returned_count=len(hits),
                        latency_ms=(time.perf_counter() - dense_started) * 1000.0,
                    )
                except Exception as error:
                    dense_diagnostic = DenseRouteDiagnostics(
                        mode=self.dense_mode,
                        attempted=True,
                        latency_ms=(time.perf_counter() - dense_started) * 1000.0,
                        error=type(error).__name__,
                    )
                continue
            try:
                hits = tuple(self.backend.search(spec.query, self.per_route_limit))
            except RetrievalRouteError:
                lexical_failures += 1
                continue
            lexical_results.append((spec, hits))
        if lexical_route_count and lexical_failures == lexical_route_count:
            raise RetrievalError("all usable retrieval routes failed")

        route_results.extend(lexical_results)
        if dense_result is not None and self.dense_mode is DenseMode.ON:
            route_results.append(dense_result)

        scores: dict[str, float] = {}
        evidence: dict[str, list[RouteEvidence]] = {}
        valid_asins = self.backend.valid_asins
        lexical_asins = {
            hit.parent_asin
            for _, hits in lexical_results
            for hit in hits
            if hit.parent_asin in valid_asins
        }
        if dense_result is not None:
            _, dense_hits = dense_result
            dense_exclusive = {
                hit.parent_asin
                for hit in dense_hits
                if hit.parent_asin in valid_asins and hit.parent_asin not in lexical_asins
            }
            dense_diagnostic = DenseRouteDiagnostics(
                mode=dense_diagnostic.mode,
                attempted=dense_diagnostic.attempted,
                returned_count=dense_diagnostic.returned_count,
                exclusive_count=len(dense_exclusive),
                latency_ms=dense_diagnostic.latency_ms,
                error=dense_diagnostic.error,
            )
        for spec, hits in route_results:
            seen_route: set[str] = set()
            for rank, hit in enumerate(hits, 1):
                asin = hit.parent_asin
                if asin in seen_route or asin not in valid_asins:
                    continue
                seen_route.add(asin)
                scores[asin] = scores.get(asin, 0.0) + spec.weight / (self.rrf_k + rank)
                evidence.setdefault(asin, []).append(
                    RouteEvidence(spec.name, rank=rank, score=hit.score)
                )

        if not scores:
            candidates = self._popularity_candidates(request.candidate_limit)
        else:
            ordered_asins = sorted(scores, key=lambda asin: (-scores[asin], asin))
            candidates = tuple(
                Candidate(asin, tuple(evidence[asin]), scores[asin])
                for asin in ordered_asins[:request.candidate_limit]
            )
        pool = CandidatePool(
            candidates=candidates,
            requested_limit=request.candidate_limit,
            route=request.route_decision.route,
            retrieval_latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        contributed = sum(
            any(item.route_name == DENSE_SEMANTIC_ROUTE for item in candidate.evidence)
            for candidate in pool.candidates
        )
        if dense_diagnostic.attempted:
            dense_diagnostic = DenseRouteDiagnostics(
                mode=dense_diagnostic.mode,
                attempted=True,
                returned_count=dense_diagnostic.returned_count,
                exclusive_count=dense_diagnostic.exclusive_count,
                contributed_count=contributed,
                latency_ms=dense_diagnostic.latency_ms,
                error=dense_diagnostic.error,
            )
        self._dense_diagnostics[(request.session_id, request.turn)] = dense_diagnostic
        return pool

    def _popularity_candidates(self, limit: int) -> tuple[Candidate, ...]:
        candidates: list[Candidate] = []
        seen: set[str] = set()
        for rank, hit in enumerate(self.backend.popularity(limit), 1):
            asin = hit.parent_asin
            if asin in seen or asin not in self.backend.valid_asins:
                continue
            seen.add(asin)
            candidates.append(Candidate(
                parent_asin=asin,
                evidence=(RouteEvidence(POPULARITY_ROUTE, rank, hit.score),),
                rrf_score=1.0 / (self.rrf_k + rank),
            ))
        if not candidates:
            raise RetrievalError("candidate pool could not be built")
        return tuple(candidates)


__all__ = [
    "ACTIVE_CONTEXT_ROUTE",
    "BROWSING_ROUTE_WEIGHTS",
    "BUYING_ROUTE_WEIGHTS",
    "CATEGORY_ANCHOR_ROUTE",
    "CURRENT_TURN_ROUTE",
    "DENSE_SEMANTIC_ROUTE",
    "DenseMode",
    "DenseRouteDiagnostics",
    "DenseSearchBackend",
    "HybridRetriever",
    "POPULARITY_ROUTE",
    "RetrievalRouteError",
    "SQLiteCatalogSearchIndex",
    "STRUCTURED_CONSTRAINT_ROUTE",
    "SearchHit",
    "USE_CASE_ROUTE",
]
