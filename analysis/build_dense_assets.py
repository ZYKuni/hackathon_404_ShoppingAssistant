"""Build normalized float16 catalog embeddings with immutable metadata."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

from starter.dense_assets import file_sha256
from starter.embedding_text import TEXT_SCHEMA_VERSION, build_product_embedding_text


DEFAULT_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


class DocumentEncoder(Protocol):
    @property
    def dimension(self) -> int: ...

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray: ...


class SentenceTransformerEncoder:
    """Build-time encoder; sentence-transformers is not a base MVP dependency."""

    def __init__(self, model_id: str, revision: str, max_sequence_length: int) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "install requirements-dense-build.txt to generate dense assets"
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
            raise RuntimeError("embedding model did not expose its dimension")
        self._dimension = int(dimension)

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(self.model.encode(
            list(texts),
            batch_size=len(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ))


def _iter_products(catalog_path: Path) -> Iterator[dict]:
    with catalog_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                product = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid catalog JSON at line {line_number}") from error
            if not isinstance(product, dict):
                raise ValueError(f"catalog line {line_number} is not an object")
            yield product


def _catalog_size(catalog_path: Path) -> int:
    seen: set[str] = set()
    count = 0
    for product in _iter_products(catalog_path):
        asin = str(product.get("parent_asin") or "").strip()
        if not asin:
            raise ValueError("catalog product is missing parent_asin")
        if asin in seen:
            raise ValueError(f"duplicate parent_asin: {asin}")
        seen.add(asin)
        count += 1
    if count < 1:
        raise ValueError("catalog is empty")
    return count


def _normalized(values: np.ndarray, expected_rows: int, dimension: int) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.shape != (expected_rows, dimension):
        raise ValueError(
            f"encoder returned {array.shape}, expected {(expected_rows, dimension)}"
        )
    if not np.isfinite(array).all():
        raise ValueError("encoder returned NaN or infinite values")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise ValueError("encoder returned a zero-length embedding")
    return array / norms


def build_assets(
    catalog_path: str | Path,
    output_dir: str | Path,
    *,
    encoder: DocumentEncoder,
    model_id: str,
    model_revision: str,
    batch_size: int = 128,
    max_sequence_length: int = 256,
) -> Path:
    catalog = Path(catalog_path)
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"dense asset directory already exists: {output}")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    dimension = int(encoder.dimension)
    if dimension < 1:
        raise ValueError("encoder dimension must be positive")

    row_count = _catalog_size(catalog)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    embeddings_name = "catalog_embeddings.f16.npy"
    asins_name = "asins.txt"
    try:
        embeddings = np.lib.format.open_memmap(
            temporary / embeddings_name,
            mode="w+",
            dtype=np.float16,
            shape=(row_count, dimension),
        )
        row = 0
        asin_handle = (temporary / asins_name).open("w", encoding="utf-8")
        try:
            batch_texts: list[str] = []
            batch_asins: list[str] = []
            for product in _iter_products(catalog):
                batch_asins.append(str(product["parent_asin"]).strip())
                batch_texts.append(build_product_embedding_text(product))
                if len(batch_texts) < batch_size:
                    continue
                encoded = _normalized(encoder.encode_documents(batch_texts), len(batch_texts), dimension)
                embeddings[row:row + len(batch_texts)] = encoded.astype(np.float16)
                asin_handle.write("".join(f"{asin}\n" for asin in batch_asins))
                row += len(batch_texts)
                batch_texts.clear()
                batch_asins.clear()
            if batch_texts:
                encoded = _normalized(encoder.encode_documents(batch_texts), len(batch_texts), dimension)
                embeddings[row:row + len(batch_texts)] = encoded.astype(np.float16)
                asin_handle.write("".join(f"{asin}\n" for asin in batch_asins))
                row += len(batch_texts)
        finally:
            asin_handle.close()
        embeddings.flush()
        del embeddings
        if row != row_count:
            raise RuntimeError(f"wrote {row} embeddings for {row_count} catalog rows")

        metadata = {
            "schema_version": "1.0.0",
            "text_schema_version": TEXT_SCHEMA_VERSION,
            "catalog_sha256": file_sha256(catalog),
            "catalog_rows": row_count,
            "model_id": model_id,
            "model_revision": model_revision,
            "embedding_shape": [row_count, dimension],
            "embedding_dtype": "float16",
            "normalized": True,
            "similarity": "dot_product",
            "max_sequence_length": max_sequence_length,
            "pooling": "mean",
            "embedding_file": embeddings_name,
            "asin_file": asins_name,
        }
        (temporary / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksums = {
            embeddings_name: file_sha256(temporary / embeddings_name),
            asins_name: file_sha256(temporary / asins_name),
        }
        (temporary / "checksums.json").write_text(
            json.dumps(checksums, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--max-sequence-length", type=int, default=256)
    args = parser.parse_args()
    encoder = SentenceTransformerEncoder(
        args.model_id,
        args.model_revision,
        args.max_sequence_length,
    )
    output = build_assets(
        args.catalog,
        args.output,
        encoder=encoder,
        model_id=args.model_id,
        model_revision=args.model_revision,
        batch_size=args.batch_size,
        max_sequence_length=args.max_sequence_length,
    )
    print(output)


if __name__ == "__main__":
    main()
