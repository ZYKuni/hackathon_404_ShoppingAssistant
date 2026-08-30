from __future__ import annotations

import heapq
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from starter.constraint_parser import parse_message
from starter.conversation_state import ConversationState, apply_patch
from starter.context_distillation import (
    ProfileContext,
    constraint_signature,
    distill_context,
    distill_profile,
)
from starter.question_policy import (
    QUESTION_TEXT,
    QuestionDecision,
    QuestionPolicy,
    candidate_facets_from_rows,
    infer_route,
)


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
OVERRIDE_RE = re.compile(
    r"\b(?:actually|instead|ignore\s+(?:my\s+)?earlier|changed?\s+my\s+mind|what\s+i\s+need)\b",
    re.IGNORECASE,
)
FULL_OVERRIDE_RE = re.compile(
    r"\b(?:ignore\s+(?:my\s+)?earlier\s+preference|forget\s+(?:my\s+)?earlier|"
    r"changed?\s+my\s+mind)\b",
    re.IGNORECASE,
)
NO_PREFERENCE_RE = re.compile(
    r"\b(?:no|don['’]?t\s+have|without)\b.{0,30}\bpreference\b",
    re.IGNORECASE,
)
NO_MATCH_RE = re.compile(r"\b(?:not quite right|ask me about)\b", re.IGNORECASE)
CATEGORY_RE = re.compile(
    r"\b(shoes?|boots?|sneakers?|sandals?|slippers?|dress(?:es)?|shirts?|tops?|tees?|"
    r"pants?|jeans?|shorts?|skirts?|jackets?|coats?|sweaters?|hoodies?|socks?|underwear|"
    r"bras?|swimwear|jewelry|earrings?|necklaces?|bracelets?|rings?|watches?|bags?|hats?)\b",
    re.IGNORECASE,
)

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

CONSERVATIVE_QUESTION_ORDER = (
    "material", "feature", "color", "style", "size", "use_case", "budget", "brand", "other"
)

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
    result = re.split(r"\bA key requirement is:\s*|,\s*but\b|\.\s+", message, maxsplit=1, flags=re.I)[0]
    return result.strip()


def _mentioned_attributes(text: str) -> set[str]:
    return {attribute for attribute, pattern in ATTRIBUTE_PATTERNS.items() if pattern.search(text)}


def _category_terms(text: str) -> set[str]:
    return {match.group(1).lower() for match in CATEGORY_RE.finditer(text)}


@dataclass
class SessionState:
    user_profile: dict
    profile_context: ProfileContext = field(default_factory=ProfileContext)
    base_request: str = ""
    # Raw customer evidence remains available to lexical retrieval even when a
    # phrase is outside the deterministic parser's current vocabulary.
    active_messages: list[str] = field(default_factory=list)
    conversation_state: ConversationState = field(default_factory=ConversationState)
    last_asked_attribute: str | None = None
    rounds_without_new_constraints: int = 0
    other_used: bool = False
    last_override_detected: bool = False
    override_active: bool = False
    last_question_decision: QuestionDecision | None = None


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
        enable_profile_context: bool = True,
        question_policy_mode: str = "safe",
    ) -> None:
        self.catalog_path = Path(catalog_path)
        if not self.catalog_path.is_file():
            raise FileNotFoundError(
                f"Catalog not found at {self.catalog_path}. Follow README.md to download catalog.jsonl.gz."
            )
        if question_policy_mode not in {"safe", "dynamic", "fixed"}:
            raise ValueError("question_policy_mode must be safe, dynamic, or fixed")
        self.connection = sqlite3.connect(":memory:")
        self._sessions: dict[str, SessionState] = {}
        self._fallback_ids: list[str] = []
        self._question_policy_mode = question_policy_mode
        self._question_policy = QuestionPolicy(enable_profile_hints=enable_profile_context)
        self._build_index()

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
        profile_payload = dict(user_profile or {})
        self._sessions[session_id] = SessionState(
            user_profile=profile_payload,
            profile_context=distill_profile(profile_payload),
            conversation_state=ConversationState(),
        )

    @staticmethod
    def _reset_constraints_for_full_override(state: SessionState) -> None:
        """Drop superseded product constraints while retaining the category anchor."""
        previous = state.conversation_state
        state.conversation_state = ConversationState(category=previous.category, turn=previous.turn)
        state.last_asked_attribute = None
        state.other_used = False

    @staticmethod
    def _structured_query(conversation_state: ConversationState) -> str:
        """Render positive structured state as supplementary lexical evidence."""
        values: list[str] = []
        if conversation_state.category:
            values.append(conversation_state.category.replace("_", " "))
        for constraints in (
            conversation_state.hard_constraints,
            conversation_state.soft_preferences,
        ):
            for value in constraints.values():
                items = value if isinstance(value, list) else [value]
                values.extend(str(item).replace("_", " ") for item in items)
        # Exclusions are deliberately omitted: feeding a negated value such as
        # "white" into BM25 would incorrectly reward white products.
        return " ".join(dict.fromkeys(value for value in values if value))

    def _update_state(self, state: SessionState, user_message: str, turn: int) -> None:
        before_signature = constraint_signature(state.conversation_state)
        if turn == 1 or not state.base_request:
            state.base_request = _base_request(user_message)

        full_override = turn > 1 and bool(FULL_OVERRIDE_RE.search(user_message))
        override_detected = turn > 1 and bool(OVERRIDE_RE.search(user_message))
        if full_override:
            self._reset_constraints_for_full_override(state)

        patch = parse_message(user_message, state.conversation_state, turn)
        state.conversation_state = apply_patch(state.conversation_state, patch)

        no_preference = NO_PREFERENCE_RE.search(user_message) or NO_MATCH_RE.search(user_message)
        if no_preference:
            # The parser handles explicit replies such as "no preference for
            # material".  This fallback also covers generic Boundary replies by
            # associating them with the attribute asked on the preceding turn.
            last_asked = state.last_asked_attribute
            if last_asked and last_asked not in state.conversation_state.no_preference:
                state.conversation_state.no_preference.append(last_asked)
            # The previous question has already been recorded, so the response adds
            # no positive retrieval evidence and should not pollute the query.
            state.rounds_without_new_constraints += 1
            state.last_override_detected = override_detected
            return

        if override_detected:
            # Retain the category anchor when the customer only changes a constraint.
            # If they explicitly name a different category, the new request replaces
            # the previous category as well.
            old_categories = _category_terms(state.base_request)
            new_categories = _category_terms(user_message)
            category_changed = bool(new_categories and old_categories and new_categories.isdisjoint(old_categories))
            if category_changed:
                state.base_request = _base_request(user_message)
                state.active_messages = [user_message]
            else:
                state.active_messages = [state.base_request, user_message]
            state.conversation_state.asked_attributes.clear()
            state.conversation_state.no_preference.clear()
            state.last_asked_attribute = None
            state.other_used = False
            state.override_active = True
        else:
            state.active_messages.append(user_message)

        after_signature = constraint_signature(state.conversation_state)
        # The first customer request is not a reply to one of our questions, so
        # it must not consume one of the two ineffective-reply slots that unlock
        # the one-time ``other`` escape hatch.
        state.rounds_without_new_constraints = (
            0
            if turn == 1 or after_signature != before_signature
            else state.rounds_without_new_constraints + 1
        )
        state.last_override_detected = override_detected

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
        return [str(row[0]) for row in rows]

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

    def _candidate_facets(self, recommendations: list[dict]) -> dict[str, tuple[str | None, ...]]:
        identifiers = [str(item["parent_asin"]) for item in recommendations]
        if not identifiers:
            return {}
        placeholders = ", ".join("?" for _ in identifiers)
        rows = self.connection.execute(
            "SELECT parent_asin, title, categories, features, details, store, description "
            f"FROM products WHERE parent_asin IN ({placeholders})",
            identifiers,
        ).fetchall()
        by_identifier = {
            str(row[0]): {
                "parent_asin": row[0],
                "title": row[1],
                "categories": row[2],
                "features": row[3],
                "details": row[4],
                "store": row[5],
                "description": row[6],
            }
            for row in rows
        }
        ordered_rows = [by_identifier[identifier] for identifier in identifiers if identifier in by_identifier]
        return candidate_facets_from_rows(ordered_rows)

    def _next_question(
        self,
        state: SessionState,
        user_message: str,
        recommendations: list[dict],
    ) -> tuple[str, str | None]:
        context = distill_context(
            state.conversation_state,
            state.profile_context,
            override_detected=state.last_override_detected,
        )
        active_route_evidence = " ".join(state.active_messages) or user_message
        # Rollout eligibility uses session evidence, while the score weights use
        # the current turn so a newly supplied constraint can immediately change
        # the Buying/Browsing emphasis.
        route = infer_route(user_message, context)
        # Candidate facets are currently reliable enough for exploratory and
        # override flows.  Explicit Buying flows keep the validated conservative
        # order until the normalized Top-200 pool supplies hard-conflict signals.
        explicit_buying_request = bool(
            context.short_term.hard_fields
            or re.search(r"\bkey requirement\b", active_route_evidence, re.I)
        )
        use_dynamic_policy = self._question_policy_mode == "dynamic" and (
            state.override_active or not explicit_buying_request
        )
        if not use_dynamic_policy:
            if state.conversation_state.turn >= 10:
                decision = QuestionDecision(
                    ask_attribute=None,
                    message="Here are the best matches based on your current preferences.",
                    reason="Turn 10 must end without another clarification question.",
                )
            else:
                known = _mentioned_attributes(" ".join(state.active_messages))
                ask_attribute = next(
                    (
                        attribute
                        for attribute in CONSERVATIVE_QUESTION_ORDER
                        if attribute not in state.conversation_state.asked_attributes
                        and attribute not in state.conversation_state.no_preference
                        and (attribute not in known or attribute == "feature")
                    ),
                    None,
                )
                decision = QuestionDecision(
                    ask_attribute=ask_attribute,
                    message=(
                        QUESTION_TEXT[ask_attribute]
                        if ask_attribute is not None
                        else "Here are the best matches based on your current preferences."
                    ),
                    reason="Conservative rollout guard used the validated baseline order.",
                )
            state.last_question_decision = decision
            ask_attribute = decision.ask_attribute
            if ask_attribute is not None:
                if ask_attribute not in state.conversation_state.asked_attributes:
                    state.conversation_state.asked_attributes.append(ask_attribute)
                if ask_attribute == "other":
                    state.other_used = True
            state.last_asked_attribute = ask_attribute
            return decision.message, ask_attribute

        decision = self._question_policy.choose(
            context,
            route,
            self._candidate_facets(recommendations),
            rounds_without_new_constraints=state.rounds_without_new_constraints,
            other_used=state.other_used,
            # A raw request may name a valid long-tail category outside the
            # deterministic parser's deliberately small category lexicon.
            category_evidence=bool(
                state.conversation_state.category
                or re.search(r"\b(?:looking for|shopping for|need|want)\b", state.base_request, re.I)
            ),
        )
        state.last_question_decision = decision
        ask_attribute = decision.ask_attribute
        if ask_attribute is not None:
            if ask_attribute not in state.conversation_state.asked_attributes:
                state.conversation_state.asked_attributes.append(ask_attribute)
            if ask_attribute == "other":
                state.other_used = True
        state.last_asked_attribute = ask_attribute
        return decision.message, ask_attribute

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        if session_id not in self._sessions:
            raise RuntimeError("reset must be called before respond")
        if not 1 <= turn <= 10:
            raise ValueError("turn must be between 1 and 10")
        top_k = max(1, min(int(top_k), 10))

        state = self._sessions[session_id]
        self._update_state(state, user_message, turn)
        recommendations = self._rank(state, user_message, top_k)
        message, ask_attribute = self._next_question(state, user_message, recommendations)
        return {
            "message": message,
            "ask_attribute": ask_attribute,
            "recommendations": recommendations,
            # No LLM is used in this baseline, so token use and external cost are zero.
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
