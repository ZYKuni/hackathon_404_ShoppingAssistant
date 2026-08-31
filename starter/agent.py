from __future__ import annotations

import copy
import heapq
import json
import re
import sqlite3
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from starter.constraint_parser import parse_message
from starter.conversation_state import ConversationState, apply_patch
from starter.attribute_lexicons import normalize_phrase
from starter.dense_retrieval import DenseMode, DenseRouteDiagnostics, DenseSearchBackend
from starter.orchestrator import AgentOrchestrator, OrchestrationResult, RuntimeMode
from starter.pipeline_contracts import RankerProtocol, RetrieverProtocol
from starter.diagnostics import (
    DIAGNOSTIC_TRACE_SCHEMA_VERSION,
    validate_diagnostic_trace,
)
from starter.question_policy import (
    QuestionDecision,
    QuestionPolicy,
    QuestionPolicyDiagnostics,
    QuestionPolicyMode,
    candidate_facets_from_products,
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
    result = re.split(r"\bA key requirement is:\s*|,\s*but\b|\.\s+", message, maxsplit=1, flags=re.I)[0]
    return result.strip()


def _mentioned_attributes(text: str) -> set[str]:
    return {attribute for attribute, pattern in ATTRIBUTE_PATTERNS.items() if pattern.search(text)}


def _category_terms(text: str) -> set[str]:
    return {match.group(1).lower() for match in CATEGORY_RE.finditer(text)}


def _constraint_signature(state: ConversationState) -> tuple[object, ...]:
    def freeze(values: dict[str, object]) -> tuple[tuple[str, object], ...]:
        return tuple(
            (field, tuple(value) if isinstance(value, list) else value)
            for field, value in sorted(values.items())
        )

    return (
        state.category,
        freeze(state.hard_constraints),
        freeze(state.soft_preferences),
        freeze(state.excluded),
    )


@dataclass
class SessionState:
    user_profile: dict
    base_request: str = ""
    # Raw customer evidence remains available to lexical retrieval even when a
    # phrase is outside the deterministic parser's current vocabulary.
    active_messages: list[str] = field(default_factory=list)
    conversation_state: ConversationState = field(default_factory=ConversationState)
    last_asked_attribute: str | None = None
    rounds_without_new_constraints: int = 0
    other_used: bool = False
    last_override_detected: bool = False
    last_question_policy: QuestionPolicyDiagnostics | None = None


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
        use_local_pipeline: bool = True,
        dense_mode: DenseMode | str = DenseMode.OFF,
        dense_backend: DenseSearchBackend | None = None,
        question_policy_mode: QuestionPolicyMode | str = QuestionPolicyMode.SAFE,
        enable_profile_question_hints: bool = True,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        if not self.catalog_path.is_file():
            raise FileNotFoundError(
                f"Catalog not found at {self.catalog_path}. Follow README.md to download catalog.jsonl.gz."
            )
        self.connection: sqlite3.Connection
        self._sessions: dict[str, SessionState] = {}
        self._diagnostic_traces: dict[str, dict] = {}
        self._active_route_capture: list[list[str]] | None = None
        self._fallback_ids: list[str] = []
        self._pipeline_fallbacks: dict[str, tuple[str, ...]] = {}
        self._dense_load_error: str | None = None
        self._question_catalog = None
        self._question_policy_mode = QuestionPolicyMode(question_policy_mode)
        self._question_policy = QuestionPolicy(
            enable_profile_hints=enable_profile_question_hints
        )
        if not isinstance(use_local_pipeline, bool):
            raise TypeError("use_local_pipeline must be bool")
        self._dense_mode = DenseMode(dense_mode)
        if (retriever is None) != (ranker is None):
            raise ValueError("retriever and ranker must be supplied together")
        if retriever is not None and (
            dense_backend is not None or self._dense_mode is not DenseMode.OFF
        ):
            raise ValueError("dense options are only supported by the default local pipeline")
        if not use_local_pipeline and (
            dense_backend is not None or self._dense_mode is not DenseMode.OFF
        ):
            raise ValueError("dense options require use_local_pipeline=True")
        self._build_index()
        if retriever is None and use_local_pipeline:
            from starter.catalog_normalizer import CatalogNormalizer
            from starter.ranker import LocalConstraintRanker
            from starter.retrieval import HybridRetriever

            catalog = CatalogNormalizer.from_jsonl(self.catalog_path)
            self._question_catalog = catalog
            retriever = HybridRetriever(
                backend=self._search_backend,
                dense_backend=dense_backend,
                dense_mode=self._dense_mode,
            )
            ranker = LocalConstraintRanker(catalog=catalog)
        self._orchestrator = (
            AgentOrchestrator(retriever, ranker, runtime_mode=runtime_mode)
            if retriever is not None and ranker is not None
            else None
        )

    @classmethod
    def with_local_pipeline(
        cls,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        runtime_mode: RuntimeMode = RuntimeMode.OFFICIAL,
        dense_mode: DenseMode | str = DenseMode.OFF,
        dense_backend: DenseSearchBackend | None = None,
    ) -> "Agent":
        """Compatibility constructor for the default formal local pipeline."""
        return cls(
            catalog_path,
            runtime_mode=runtime_mode,
            use_local_pipeline=True,
            dense_mode=dense_mode,
            dense_backend=dense_backend,
        )

    @classmethod
    def with_optional_dense_assets(
        cls,
        catalog_path: str | Path,
        *,
        asset_dir: str | Path,
        model_dir: str | Path,
        dense_mode: DenseMode | str = DenseMode.SHADOW,
        runtime_mode: RuntimeMode = RuntimeMode.OFFICIAL,
    ) -> "Agent":
        """Load local dense assets or return the unchanged OFF pipeline."""
        from starter.dense_runtime import load_optional_dense_backend

        loaded = load_optional_dense_backend(
            catalog_path,
            asset_dir,
            model_dir,
            mode=dense_mode,
        )
        agent = cls(
            catalog_path,
            runtime_mode=runtime_mode,
            dense_mode=loaded.effective_mode,
            dense_backend=loaded.backend,
        )
        agent._dense_load_error = loaded.error
        return agent

    @classmethod
    def legacy(cls, catalog_path: str | Path = "data/catalog.jsonl") -> "Agent":
        """Build the deterministic BM25 baseline for controlled ablation only."""
        return cls(catalog_path, use_local_pipeline=False)

    def _build_index(self) -> None:
        from starter.retrieval import SQLiteCatalogSearchIndex

        self._search_backend = SQLiteCatalogSearchIndex(self.catalog_path)
        self.connection = self._search_backend.connection
        # The official safety path must reproduce the published Legacy fallback
        # exactly, including the descending ASIN tie-break used by ``heapq``.
        popularity = sorted(
            self._search_backend.popularity(200),
            key=lambda hit: (float(hit.score or 0.0), hit.parent_asin),
            reverse=True,
        )
        self._fallback_ids = [hit.parent_asin for hit in popularity[:100]]

    def reset(self, session_id: str, user_profile: dict) -> None:
        """Start a clean state; no information may leak between customers."""
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

    def question_policy_diagnostics(
        self, session_id: str
    ) -> QuestionPolicyDiagnostics | None:
        """Return the latest safe/shadow/dynamic clarification decision."""

        state = self._sessions.get(session_id)
        return state.last_question_policy if state is not None else None

    def dense_diagnostics(self, session_id: str, turn: int) -> DenseRouteDiagnostics:
        """Return aggregate dense-route diagnostics without retaining query text."""
        if self._orchestrator is None:
            return DenseRouteDiagnostics(mode=DenseMode.OFF)
        diagnostics = getattr(self._orchestrator.retriever, "dense_diagnostics", None)
        if diagnostics is None:
            return DenseRouteDiagnostics(mode=self._dense_mode)
        return diagnostics(session_id, turn)

    def dense_load_error(self) -> str | None:
        """Return a safe error type when optional dense startup fell back OFF."""
        return self._dense_load_error

    @staticmethod
    def _reset_constraints_for_full_override(state: SessionState) -> None:
        """Drop superseded product constraints while retaining the category anchor."""
        previous = state.conversation_state
        state.conversation_state = ConversationState(category=previous.category, turn=previous.turn)
        state.last_asked_attribute = None

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

    @staticmethod
    def _preserve_compatible_override_details(
        previous: ConversationState,
        current: ConversationState,
    ) -> tuple[str, ...]:
        """Keep same-slot details only when the new explicit value confirms them."""
        confirmed: list[str] = []
        for field in set(current.hard_constraints) | set(current.soft_preferences):
            if field in {"price_min", "price_max"}:
                continue
            new_values = {
                str(value)
                for group in (current.hard_constraints, current.soft_preferences)
                for value in (group.get(field, []) if isinstance(group.get(field, []), list) else [group.get(field)])
                if value is not None
            }
            old_values = [
                str(value)
                for group in (previous.hard_constraints, previous.soft_preferences)
                for value in (group.get(field, []) if isinstance(group.get(field, []), list) else [group.get(field)])
                if value is not None
            ]
            if not ({normalize_phrase(value) for value in new_values} & {
                normalize_phrase(value) for value in old_values
            }):
                continue
            confirmed.extend(new_values)
            extras = [value for value in old_values if value not in new_values]
            if extras:
                target = current.soft_preferences.setdefault(field, [])
                for value in extras:
                    if value not in target:
                        target.append(value)
        return tuple(dict.fromkeys(confirmed))

    @staticmethod
    def _mentions_confirmed_value(message: str, values: tuple[str, ...]) -> bool:
        text = f" {normalize_phrase(message)} "
        return any(
            re.search(rf"(?<![a-z0-9]){re.escape(normalize_phrase(value))}(?![a-z0-9])", text)
            for value in values
            if normalize_phrase(value)
        )

    def _update_state(self, state: SessionState, user_message: str, turn: int) -> None:
        before_signature = _constraint_signature(state.conversation_state)
        if turn == 1 or not state.base_request:
            state.base_request = _base_request(user_message)

        full_override = turn > 1 and bool(FULL_OVERRIDE_RE.search(user_message))
        previous_state = deepcopy(state.conversation_state)
        previous_messages = list(state.active_messages)
        if full_override:
            self._reset_constraints_for_full_override(state)
            state.rounds_without_new_constraints = 0
            state.other_used = False

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
            state.last_override_detected = turn > 1 and bool(OVERRIDE_RE.search(user_message))
            return

        if OVERRIDE_RE.search(user_message) and turn > 1:
            # Retain the category anchor when the customer only changes a constraint.
            # If they explicitly name a different category, the new request replaces
            # the previous category as well.
            old_categories = _category_terms(state.base_request)
            new_categories = _category_terms(user_message)
            category_changed = bool(new_categories and old_categories and new_categories.isdisjoint(old_categories))
            if category_changed:
                state.base_request = _base_request(user_message)
                state.active_messages = [user_message]
            elif full_override:
                confirmed = self._preserve_compatible_override_details(
                    previous_state, state.conversation_state
                )
                compatible_messages = [
                    message
                    for message in previous_messages
                    if message != state.base_request
                    and self._mentions_confirmed_value(message, confirmed)
                ]
                state.active_messages = list(dict.fromkeys(
                    [state.base_request, *compatible_messages, user_message]
                ))
            else:
                state.active_messages = [state.base_request, user_message]
            state.conversation_state.asked_attributes.clear()
            state.conversation_state.no_preference.clear()
            state.last_asked_attribute = None
            state.other_used = False
        else:
            state.active_messages.append(user_message)

        after_signature = _constraint_signature(state.conversation_state)
        state.rounds_without_new_constraints = (
            0
            if turn == 1 or after_signature != before_signature
            else state.rounds_without_new_constraints + 1
        )
        state.last_override_detected = turn > 1 and bool(OVERRIDE_RE.search(user_message))

    def _search(self, text: str, limit: int = 120) -> list[str]:
        identifiers = [
            hit.parent_asin
            for hit in self._search_backend.search_legacy(
                text, limit, stopwords=STOPWORDS
            )
        ]
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

    @staticmethod
    def _apply_question_decision(
        state: SessionState,
        decision: QuestionDecision,
        *,
        over_general: bool,
    ) -> tuple[str, str | None]:
        ask_attribute = decision.ask_attribute
        if ask_attribute is not None:
            if ask_attribute not in state.conversation_state.asked_attributes:
                state.conversation_state.asked_attributes.append(ask_attribute)
            if ask_attribute == "other":
                state.other_used = True
        state.last_asked_attribute = ask_attribute
        message = decision.message
        if over_general and ask_attribute is not None:
            message = f"I found a broad set of possible matches. {message}"
        return message, ask_attribute

    def _baseline_question(
        self,
        state: SessionState,
        user_message: str,
        turn: int,
        *,
        over_general: bool = False,
    ) -> tuple[str, str | None]:
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
            message = QUESTION_TEXT[attribute]
            if over_general:
                message = f"I found a broad set of possible matches. {message}"
            return message, attribute
        state.last_asked_attribute = None
        return "Here are the best matches based on your current preferences.", None

    def _candidate_facets(
        self, candidate_ids: tuple[str, ...]
    ) -> dict[str, tuple[str | None, ...]]:
        if self._question_catalog is None:
            return {}
        products = tuple(
            product
            for parent_asin in candidate_ids
            if (product := self._question_catalog.get(parent_asin)) is not None
        )
        return candidate_facets_from_products(products)

    def _next_question(
        self,
        state: SessionState,
        user_message: str,
        turn: int,
        *,
        over_general: bool = False,
        orchestration_result: OrchestrationResult | None = None,
    ) -> tuple[str, str | None]:
        if (
            self._question_policy_mode is QuestionPolicyMode.SAFE
            or orchestration_result is None
            or self._question_catalog is None
        ):
            message, applied = self._baseline_question(
                state, user_message, turn, over_general=over_general
            )
            state.last_question_policy = QuestionPolicyDiagnostics(
                mode=self._question_policy_mode,
                route=(
                    orchestration_result.request.route_decision.route
                    if orchestration_result is not None
                    else None
                ),
                candidate_count=(
                    orchestration_result.candidate_count
                    if orchestration_result is not None
                    else 0
                ),
                selected_attribute=applied,
                applied_attribute=applied,
                reason="Validated fixed-priority question order.",
            )
            return message, applied

        request = orchestration_result.request
        decision = self._question_policy.choose(
            request.state,
            request.profile,
            request.route_decision,
            self._candidate_facets(orchestration_result.candidate_ids),
            rounds_without_new_constraints=state.rounds_without_new_constraints,
            other_used=state.other_used,
            category_evidence=bool(
                request.state.category
                or re.search(
                    r"\b(?:looking for|shopping for|need|want)\b",
                    state.base_request,
                    re.I,
                )
            ),
        )
        if self._question_policy_mode is QuestionPolicyMode.SHADOW:
            message, applied = self._baseline_question(
                state, user_message, turn, over_general=over_general
            )
        else:
            message, applied = self._apply_question_decision(
                state, decision, over_general=over_general
            )
        state.last_question_policy = QuestionPolicyDiagnostics(
            mode=self._question_policy_mode,
            route=request.route_decision.route,
            candidate_count=orchestration_result.candidate_count,
            selected_attribute=decision.ask_attribute,
            applied_attribute=applied,
            reason=decision.reason,
            scores=decision.scores,
        )
        return message, applied

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
        started = time.perf_counter()
        captured_routes: list[list[str]] = []
        self._active_route_capture = captured_routes
        state = self._sessions[session_id]
        try:
            override_detected = turn > 1 and bool(OVERRIDE_RE.search(user_message))
            self._update_state(state, user_message, turn)
            over_general = False
            orchestration_result: OrchestrationResult | None = None
            if self._orchestrator is None:
                recommendations = self._rank(state, user_message, top_k)
            else:
                orchestration_result = self._orchestrator.execute(
                    session_id=session_id,
                    turn=turn,
                    top_k=top_k,
                    current_message=user_message,
                    raw_context=" ".join(state.active_messages).strip(),
                    base_request=state.base_request,
                    state=state.conversation_state,
                    profile=state.user_profile,
                    override_detected=override_detected,
                    legacy_fallback=lambda: self._rank(state, user_message, top_k),
                )
                recommendations = [
                    {"parent_asin": asin, "score": score}
                    for asin, score in orchestration_result.recommendations
                ]
                self._pipeline_fallbacks[session_id] = orchestration_result.fallbacks
                over_general = orchestration_result.over_general
            message, ask_attribute = self._next_question(
                state,
                user_message,
                turn,
                over_general=over_general,
                orchestration_result=orchestration_result,
            )
            response = {
                "message": message,
                "ask_attribute": ask_attribute,
                "recommendations": recommendations,
                # No LLM is used in this baseline, so token use and external cost are zero.
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }
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
        recommendation_ids = [
            str(item.get("parent_asin", ""))
            for item in recommendations
            if isinstance(item, dict) and item.get("parent_asin")
        ]
        fallback_ids = [asin for asin in recommendation_ids if asin not in scores]
        events = self._pipeline_fallbacks.get(session_id, ())
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
                "recommendations": deepcopy(recommendations),
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
        trace = deepcopy(self._diagnostic_traces[session_id])
        validate_diagnostic_trace(trace)
        return trace
