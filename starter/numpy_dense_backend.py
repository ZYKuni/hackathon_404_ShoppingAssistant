"""Optional NumPy/mmap dense search backend loaded only when explicitly requested."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

from .dense_assets import DenseAssetManifest, DenseAssetsError
from .retrieval_types import SearchHit


class QueryEncoder(Protocol):
    @property
    def dimension(self) -> int: ...

    def encode_query(self, text: str) -> np.ndarray: ...


class SentenceTransformerQueryEncoder:
    """Prototype local encoder; imports the optional dependency lazily."""

    def __init__(self, model_id: str, revision: str, max_sequence_length: int) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise DenseAssetsError(
                "sentence-transformers is unavailable for dense query encoding"
            ) from error
        self.model = SentenceTransformer(model_id, revision=revision)
        self.model.max_seq_length = max_sequence_length
        get_dimension = getattr(
            self.model,
            "get_embedding_dimension",
            self.model.get_sentence_embedding_dimension,
        )
        dimension = get_dimension()
        if dimension is None:
            raise DenseAssetsError("query encoder did not expose its dimension")
        self._dimension = int(dimension)

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode_query(self, text: str) -> np.ndarray:
        return np.asarray(self.model.encode(
            [text],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0])


class NumpyDenseSearchIndex:
    """Search normalized catalog vectors with deterministic dot-product Top-K."""

    def __init__(
        self,
        asset_dir: str | Path,
        catalog_path: str | Path,
        *,
        encoder: QueryEncoder,
        block_size: int = 8192,
        validate_checksums: bool = True,
    ) -> None:
        if block_size < 1:
            raise ValueError("block_size must be positive")
        self.asset_dir = Path(asset_dir)
        self.manifest = DenseAssetManifest.load(self.asset_dir)
        if validate_checksums:
            self.manifest.validate_files(self.asset_dir, catalog_path)
        if int(encoder.dimension) != self.manifest.embedding_dimension:
            raise DenseAssetsError("query encoder dimension does not match catalog embeddings")
        self.encoder = encoder
        self.block_size = int(block_size)
        self.asins = tuple(
            line.strip()
            for line in (self.asset_dir / self.manifest.asin_file).read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        )
        if len(self.asins) != self.manifest.embedding_rows:
            raise DenseAssetsError("ASIN count does not match embedding rows")
        if len(set(self.asins)) != len(self.asins):
            raise DenseAssetsError("dense assets contain duplicate ASINs")
        try:
            self.embeddings = np.load(
                self.asset_dir / self.manifest.embedding_file,
                mmap_mode="r",
                allow_pickle=False,
            )
        except (OSError, ValueError) as error:
            raise DenseAssetsError("could not load dense embedding matrix") from error
        if self.embeddings.shape != (
            self.manifest.embedding_rows,
            self.manifest.embedding_dimension,
        ):
            raise DenseAssetsError("embedding matrix shape does not match manifest")
        if self.embeddings.dtype != np.float16:
            raise DenseAssetsError("embedding matrix must use float16 storage")

    @lru_cache(maxsize=1024)
    def search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        if not isinstance(query, str):
            raise TypeError("dense query must be a string")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("dense limit must be an integer")
        if limit < 1:
            raise ValueError("dense limit must be positive")
        if not query.strip():
            return ()

        vector = np.asarray(self.encoder.encode_query(query), dtype=np.float32)
        if vector.shape != (self.manifest.embedding_dimension,):
            raise DenseAssetsError("query encoder returned an invalid shape")
        if not np.isfinite(vector).all():
            raise DenseAssetsError("query encoder returned NaN or infinite values")
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm <= 0:
            raise DenseAssetsError("query encoder returned a zero-length vector")
        vector /= norm

        row_count = self.manifest.embedding_rows
        scores = np.empty(row_count, dtype=np.float32)
        for start in range(0, row_count, self.block_size):
            stop = min(start + self.block_size, row_count)
            block = np.asarray(self.embeddings[start:stop], dtype=np.float32)
            scores[start:stop] = block @ vector

        count = min(limit, row_count)
        if count == row_count:
            indices = np.arange(row_count)
        else:
            indices = np.argpartition(scores, -count)[-count:]
        ordered = sorted(
            (int(index) for index in indices),
            key=lambda index: (-float(scores[index]), self.asins[index]),
        )
        return tuple(
            SearchHit(self.asins[index], float(scores[index])) for index in ordered
        )


__all__ = [
    "NumpyDenseSearchIndex",
    "QueryEncoder",
    "SentenceTransformerQueryEncoder",
]
