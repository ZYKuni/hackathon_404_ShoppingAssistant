"""Small dependency-free value objects shared by retrieval backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    parent_asin: str
    score: float | None = None


__all__ = ["SearchHit"]
