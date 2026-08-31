from __future__ import annotations

from typing import Protocol, runtime_checkable


DIAGNOSTIC_TRACE_SCHEMA_VERSION = "1.0.0"


@runtime_checkable
class DiagnosticTraceProvider(Protocol):
    """Optional development-only interface implemented by diagnosable agents."""

    def get_diagnostic_trace(self, session_id: str) -> dict:
        """Return a detached snapshot for one session without mutating Agent state."""
        ...


def validate_diagnostic_trace(trace: object) -> None:
    """Validate the stable core shared by all Agent implementations.

    Module-specific fields are deliberately allowed so future retrievers, filters,
    rerankers, and dialogue policies can attach additional diagnostics.
    """
    if not isinstance(trace, dict):
        raise ValueError("diagnostic trace must be an object")
    if trace.get("schema_version") != DIAGNOSTIC_TRACE_SCHEMA_VERSION:
        raise ValueError("unsupported diagnostic trace schema_version")
    if not isinstance(trace.get("session_id"), str) or not trace["session_id"]:
        raise ValueError("diagnostic trace requires a non-empty session_id")
    turns = trace.get("turns")
    if not isinstance(turns, list):
        raise ValueError("diagnostic trace requires a turns list")

    previous_turn = 0
    for item in turns:
        if not isinstance(item, dict):
            raise ValueError("every diagnostic turn must be an object")
        turn = item.get("turn")
        if not isinstance(turn, int) or isinstance(turn, bool) or turn <= previous_turn:
            raise ValueError("diagnostic turns must be strictly increasing positive integers")
        previous_turn = turn
        if not isinstance(item.get("user_message"), str):
            raise ValueError("every diagnostic turn requires user_message")
        if not isinstance(item.get("state"), dict):
            raise ValueError("every diagnostic turn requires a state object")
        if not isinstance(item.get("response"), dict):
            raise ValueError("every diagnostic turn requires a response object")
        ranking = item.get("ranking")
        if not isinstance(ranking, dict):
            raise ValueError("every diagnostic turn requires a ranking object")
        routes = ranking.get("routes")
        if not isinstance(routes, list):
            raise ValueError("ranking requires a routes list")
        for route in routes:
            if not isinstance(route, dict) or not isinstance(route.get("name"), str):
                raise ValueError("every route requires a name")
            candidate_ids = route.get("candidate_ids")
            if not isinstance(candidate_ids, list) or not all(
                isinstance(parent_asin, str) for parent_asin in candidate_ids
            ):
                raise ValueError("every route requires a candidate_ids string list")
        candidate_pool = ranking.get("candidate_pool")
        if not isinstance(candidate_pool, list) or not all(
            isinstance(parent_asin, str) for parent_asin in candidate_pool
        ):
            raise ValueError("ranking requires a candidate_pool string list")
