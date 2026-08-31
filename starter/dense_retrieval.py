"""Dependency-free contracts for optional local dense semantic retrieval.

The stable MVP imports this module even when NumPy, ONNX Runtime, and model
assets are absent.  Runtime implementations must therefore keep heavyweight
imports lazy and live behind ``DenseMode``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

from .retrieval_types import SearchHit


class DenseMode(str, Enum):
    """Control whether dense retrieval is disabled, observed, or fused."""

    OFF = "off"
    SHADOW = "shadow"
    ON = "on"


class DenseSearchBackend(Protocol):
    """Minimal query-time interface implemented by a local vector index."""

    def search(self, query: str, limit: int) -> Sequence[SearchHit]: ...


@dataclass(frozen=True)
class DenseRouteDiagnostics:
    """Safe aggregate diagnostics; no query or user text is retained."""

    mode: DenseMode
    attempted: bool = False
    returned_count: int = 0
    exclusive_count: int = 0
    contributed_count: int = 0
    latency_ms: float = 0.0
    error: str | None = None


__all__ = ["DenseMode", "DenseRouteDiagnostics", "DenseSearchBackend"]
