from __future__ import annotations

import copy
import heapq
import json
import re
import sqlite3
import time
from pathlib import Path

from starter.conversation_state import ConversationState
from starter.orchestrator import (
    AgentOrchestrator,
    LegacyQuestionPolicyAdapter,
    LegacyRankerAdapter,
    LegacyRetrieverAdapter,
    OrchestrationSession,
    RuntimeMode,
    base_request_from_message,
    update_session_state,
)
from starter.state_adapter import build_structured_query, to_state_snapshot
from starter.constraint_parser import parse_message
from starter.conversation_state import ConversationState, apply_patch
from starter.orchestrator import AgentOrchestrator, RuntimeMode
from starter.pipeline_contracts import RankerProtocol, RetrieverProtocol
from starter.diagnostics import (
    DIAGNOSTIC_TRACE_SCHEMA_VERSION,
    validate_diagnostic_trace,
)


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a", "about", "additional", "an", "and", "are", "as", "at", "be", "but", "by",
    "closest", "do", "does", "for", "found", "from", "have", "here", "i", "in", "is",
    "it", "key", "looking", "matches", "matters", "me", "my", "need", "not", "of", "on",
    "options", "or", "please", "preference", "requirement", "right", "some", "that", "the",
    "these", "this", "those", "to", "use", "want", "what", "with", "would", "you", "your",
}

ATTRIBUTE_PATTERNS: dict[str, re.Pattern[str]] = {
    "material": re.compile(
        r"\b(cotton|polyester|nylon|leather|wool|spandex|silk|rayon|fabric|linen|denim)\b", re.I
    ),
    "color": re.compile(
        r"\b(black|white|blue|red|pink|green|brown|gray|grey|purple|yellow|orange|beige)\b", re.I
    ),
    "size": re.compile(r"\b(size|sizing|width|wide|narrow|small|medium|large|xl|xxl)\b", re.I),
    "style": re.compile(r"\b(style|fit|fitted|loose|sleeve|neck|casual|formal|vintage)\b", re.I),
    "brand": re.compile(r"\b(brand|made by|manufacturer)\b", re.I),
    "budget": re.compile(r"(?:\$\s*\d|\b(?:budget|under|below|less than|up to)\s+\$?\d)", re.I),
    "use_case": re.compile(
        r"\b(hiking|running|walking|gym|winter|summer|outdoor|work|wedding|travel|sports?)\b", re.I
    ),
}

# These fields reveal useful constraints most often in the supplied simulator.  The
# final "other" is a safe escape hatch when a useful preference has no known slot.
QUESTION_ORDER = (
    "material", "feature", "color", "style", "size", "use_case", "budget", "brand", "other"
)
QUESTION_TEXT = {
    "material": "Do you have a preferred material?",
    "feature": "Which product feature matters most to you?",
    "color": "Do you have a color preference?",
    "style": "What style or fit would you prefer?",
    "size": "Do you have a size or width requirement?",
    "use_case": "What occasion or use case is this for?",
    "budget": "What budget range should I use?",
    "brand": "Do you have a preferred brand?",
    "other": "Is there one other must-have requirement I should prioritize?",
}


def _text(value: object) -> str:
    """Flatten the heterogeneous catalog fields into searchable text."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _terms(text: str) -> list[str]:
    """Return stable, de-duplicated FTS query terms."""
    terms = (
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    )
    return list(dict.fromkeys(terms))


def _base_request(message: str) -> str:
    """Keep the original product category while dropping early preferences."""
    return base_request_from_message(message)


def _mentioned_attributes(text: str) -> set[str]:
    return {attribute for attribute, pattern in ATTRIBUTE_PATTERNS.items() if pattern.search(text)}


SessionState = OrchestrationSession


class Agent:
    """Offline conversational retrieval baseline.

    The implementation deliberately uses only Python's standard library.  SQLite
    FTS5 supplies BM25 retrieval; lightweight session state supplies multi-turn
    memory, clarification, and intent-override handling.
    """

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        retriever: RetrieverProtocol | None = None,
        ranker: RankerProtocol | None = None,
        runtime_mode: RuntimeMode = RuntimeMode.OFFICIAL,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        if not self.catalog_path.is_file():
            raise FileNotFoundError(
                f"Catalog not found at {self.catalog_path}. Follow README.md to download catalog.jsonl.gz."
            )
        self.connection = sqlite3.connect(":memory:")
        self._sessions: dict[str, SessionState] = {}
        self._diagnostic_traces: dict[str, dict] = {}
        self._active_route_capture: list[list[str]] | None = None
        self._fallback_ids: list[str] = []
        self._pipeline_fallbacks: dict[str, tuple[str, ...]] = {}
        if (retriever is None) != (ranker is None):
            raise ValueError("retriever and ranker must be supplied together")
        self._orchestrator = (
            AgentOrchestrator(retriever, ranker, runtime_mode=runtime_mode)
            if retriever is not None and ranker is not None
            else None
        )
        self._build_index()
        legacy_retriever = LegacyRetrieverAdapter(self._search, self._fallback_ids)
        legacy_ranker = LegacyRankerAdapter()
        legacy_question_policy = LegacyQuestionPolicyAdapter(
            _mentioned_attributes, QUESTION_ORDER, QUESTION_TEXT
        )
        self.orchestrator = AgentOrchestrator(
            legacy_retriever,
            legacy_ranker,
            legacy_question_policy,
            fallback_retriever=legacy_retriever,
            fallback_question_policy=legacy_question_policy,
            runtime_mode=RuntimeMode.OFFICIAL,
        )
        # Kept as a compatibility view for the existing tests and local debugging.
        self._sessions = self.orchestrator.sessions

    @classmethod
    def with_local_pipeline(
        cls,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        runtime_mode: RuntimeMode = RuntimeMode.OFFICIAL,
    ) -> "Agent":
        """Build the formal Aaron retrieval/ranking pipeline behind the public API."""
        from starter.catalog_normalizer import CatalogNormalizer
        from starter.ranker import LocalConstraintRanker
        from starter.retrieval import HybridRetriever

        catalog = CatalogNormalizer.from_jsonl(catalog_path)
        return cls(
            catalog_path,
            retriever=HybridRetriever(catalog_path),
            ranker=LocalConstraintRanker(catalog=catalog),
            runtime_mode=runtime_mode,
        )

    def _build_index(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, title, categories, features, details, store, description, "
            "tokenize='porter unicode61 remove_diacritics 2')"
        )
        batch: list[tuple[str, str, str, str, str, str, str]] = []
        popularity: list[tuple[float, str]] = []
        with self.catalog_path.open(encoding="utf-8") as handle:
            for line in handle:
                product = json.loads(line)
                parent_asin = str(product["parent_asin"])
                batch.append(
                    (
                        parent_asin,
                        _text(product.get("title")),
                        _text(product.get("categories")),
                        _text(product.get("features")),
                        _text(product.get("details")),
                        _text(product.get("store")),
                        _text(product.get("description")),
                    )
                )
                rating = float(product.get("average_rating") or 0.0)
                rating_count = int(product.get("rating_number") or 0)
                heapq.heappush(popularity, (rating_count * max(rating, 0.1), parent_asin))
                if len(popularity) > 100:
                    heapq.heappop(popularity)
                if len(batch) >= 1000:
                    cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
        if batch:
            cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        self.connection.commit()
        self._fallback_ids = [asin for _, asin in sorted(popularity, reverse=True)]

    def reset(self, session_id: str, user_profile: dict) -> None:
        """Start a clean state; no information may leak between customers."""
        self.orchestrator.reset(session_id, user_profile)
        self._sessions[session_id] = SessionState(
            user_profile=dict(user_profile or {}),
            conversation_state=ConversationState(),
        )
        self._pipeline_fallbacks.pop(session_id, None)
        self._diagnostic_traces[session_id] = {
            "schema_version": DIAGNOSTIC_TRACE_SCHEMA_VERSION,
            "session_id": session_id,
            "turns": [],
        }

    def pipeline_fallbacks(self, session_id: str) -> tuple[str, ...]:
        """Return machine-readable fallback events from the session's last turn."""
        return self._pipeline_fallbacks.get(session_id, ())

    @staticmethod
    def _reset_constraints_for_full_override(state: SessionState) -> None:
        """Drop superseded product constraints while retaining the category anchor."""
        previous = state.conversation_state
        state.conversation_state = ConversationState(category=previous.category, turn=previous.turn)
        state.last_asked_attribute = None

    @staticmethod
    def _structured_query(conversation_state: ConversationState) -> str:
        """Render positive structured state as supplementary lexical evidence."""
        return build_structured_query(to_state_snapshot(conversation_state))

    def _update_state(self, state: SessionState, user_message: str, turn: int) -> None:
        update_session_state(state, user_message, turn)

    def _search(self, text: str, limit: int = 120) -> list[str]:
        terms = _terms(text)[:60]
        if not terms:
            return []
        expression = " OR ".join(f'"{term}"' for term in terms)
        rows = self.connection.execute(
            "SELECT parent_asin FROM products WHERE products MATCH ? "
            "ORDER BY bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) LIMIT ?",
            (expression, limit),
        ).fetchall()
        identifiers = [str(row[0]) for row in rows]
        if self._active_route_capture is not None:
            self._active_route_capture.append(list(identifiers))
        return identifiers

    def _rank(self, state: SessionState, user_message: str, top_k: int) -> list[dict]:
        """Fuse current-turn, cumulative-session, and category-anchor retrieval."""
        raw_context = " ".join(state.active_messages).strip()
        structured_context = self._structured_query(state.conversation_state)
        # Raw evidence stays authoritative for the lexical baseline because it
        # preserves open-vocabulary feature phrases.  Structured state is a safe
        # fallback when no positive raw evidence is available; later catalog-side
        # normalization can consume it directly for filtering and reranking.
        active_context = raw_context or structured_context
        routes = (
            (self._search(active_context), 1.40),
            (self._search(user_message), 0.85),
            (self._search(state.base_request), 0.25),
        )
        scores: dict[str, float] = {}
        for ranked_ids, weight in routes:
            for rank, parent_asin in enumerate(ranked_ids, start=1):
                scores[parent_asin] = scores.get(parent_asin, 0.0) + weight / (60.0 + rank)

        ranked = sorted(scores, key=lambda asin: (-scores[asin], asin))
        if len(ranked) < top_k:
            ranked.extend(asin for asin in self._fallback_ids if asin not in scores)
        return [{"parent_asin": asin, "score": scores.get(asin, 0.0)} for asin in ranked[:top_k]]

    def _next_question(self, state: SessionState, user_message: str, turn: int) -> tuple[str, str | None]:
        if turn >= 10:
            state.last_asked_attribute = None
            return "Here are the best matches based on your current preferences.", None

        conversation_state = state.conversation_state
        known = _mentioned_attributes(" ".join(state.active_messages))
        for attribute in QUESTION_ORDER:
            if attribute in conversation_state.asked_attributes:
                continue
            if attribute in conversation_state.no_preference:
                continue
            if attribute in known and attribute != "feature":
                continue
            conversation_state.asked_attributes.append(attribute)
            state.last_asked_attribute = attribute
            return QUESTION_TEXT[attribute], attribute
        state.last_asked_attribute = None
        return "Here are the best matches based on your current preferences.", None

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        started = time.perf_counter()
        captured_routes: list[list[str]] = []
        self._active_route_capture = captured_routes
        try:
            response = self.orchestrator.respond(session_id, user_message, turn, top_k)
        finally:
            self._active_route_capture = None

        route_specs = (
            ("active_context", 1.40),
            ("current_turn", 0.85),
            ("category_anchor", 0.25),
        )
        routes = [
            {"name": name, "weight": weight, "candidate_ids": candidate_ids}
            for (name, weight), candidate_ids in zip(route_specs, captured_routes)
        ]
        scores: dict[str, float] = {}
        for route in routes:
            weight = float(route["weight"])
            for rank, parent_asin in enumerate(route["candidate_ids"], start=1):
                scores[parent_asin] = scores.get(parent_asin, 0.0) + weight / (60.0 + rank)
        candidate_pool = sorted(scores, key=lambda asin: (-scores[asin], asin))
        recommendations = response.get("recommendations", [])
        recommendation_ids = [
            str(item.get("parent_asin", ""))
            for item in recommendations
            if isinstance(item, dict) and item.get("parent_asin")
        ]
        fallback_ids = [asin for asin in recommendation_ids if asin not in scores]
        state = self._sessions[session_id]
        events = tuple(self.orchestrator.diagnostics(session_id).events)
        self._pipeline_fallbacks[session_id] = events
        self._diagnostic_traces[session_id]["turns"].append({
            "turn": turn,
            "user_message": user_message,
            "state": {
                "base_request": state.base_request,
                "active_messages": list(state.active_messages),
                "asked_attributes": sorted(state.conversation_state.asked_attributes),
                "unavailable_attributes": sorted(state.conversation_state.no_preference),
                "conversation_state": state.conversation_state.to_dict(),
            },
            "ranking": {
                "routes": routes,
                "candidate_pool": candidate_pool,
                "recommendations": copy.deepcopy(recommendations),
            },
            "response": {
                "message": response.get("message", ""),
                "ask_attribute": response.get("ask_attribute"),
                "recommendations": recommendation_ids,
            },
            "fallback": {
                "used": bool(events or fallback_ids),
                "reason": ",".join(events) if events else (
                    "popularity_fallback" if fallback_ids else None
                ),
                "added_ids": fallback_ids,
            },
            "timing_ms": {
                "pipeline": round((time.perf_counter() - started) * 1000.0, 3),
            },
        })
        return response

    def get_diagnostic_trace(self, session_id: str) -> dict:
        """Return a detached development trace without affecting official output."""
        if session_id not in self._diagnostic_traces:
            raise KeyError(f"unknown diagnostic session: {session_id}")
        trace = copy.deepcopy(self._diagnostic_traces[session_id])
        validate_diagnostic_trace(trace)
        return trace
