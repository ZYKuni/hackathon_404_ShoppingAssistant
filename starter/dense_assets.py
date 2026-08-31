"""Validation helpers for immutable, catalog-bound dense retrieval assets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from starter.embedding_text import TEXT_SCHEMA_VERSION


class DenseAssetsError(RuntimeError):
    """Dense assets are absent, corrupt, or incompatible with the catalog."""


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class DenseAssetManifest:
    schema_version: str
    text_schema_version: str
    catalog_sha256: str
    catalog_rows: int
    model_id: str
    model_revision: str
    embedding_rows: int
    embedding_dimension: int
    embedding_dtype: str
    normalized: bool
    similarity: str
    max_sequence_length: int
    embedding_file: str
    asin_file: str

    @classmethod
    def load(cls, asset_dir: str | Path) -> "DenseAssetManifest":
        path = Path(asset_dir) / "metadata.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            shape = payload["embedding_shape"]
            normalized = payload["normalized"]
            if not isinstance(normalized, bool):
                raise TypeError("normalized must be a boolean")
            manifest = cls(
                schema_version=str(payload["schema_version"]),
                text_schema_version=str(payload["text_schema_version"]),
                catalog_sha256=str(payload["catalog_sha256"]),
                catalog_rows=int(payload["catalog_rows"]),
                model_id=str(payload["model_id"]),
                model_revision=str(payload["model_revision"]),
                embedding_rows=int(shape[0]),
                embedding_dimension=int(shape[1]),
                embedding_dtype=str(payload["embedding_dtype"]),
                normalized=normalized,
                similarity=str(payload["similarity"]),
                max_sequence_length=int(payload["max_sequence_length"]),
                embedding_file=str(payload["embedding_file"]),
                asin_file=str(payload["asin_file"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise DenseAssetsError(f"invalid dense asset manifest: {path}") from error
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version != "1.0.0":
            raise DenseAssetsError(f"unsupported dense schema: {self.schema_version}")
        if self.text_schema_version != TEXT_SCHEMA_VERSION:
            raise DenseAssetsError(
                f"unsupported embedding text schema: {self.text_schema_version}"
            )
        if self.catalog_rows < 1 or self.embedding_rows != self.catalog_rows:
            raise DenseAssetsError("catalog and embedding row counts must match")
        if self.embedding_dimension < 1:
            raise DenseAssetsError("embedding dimension must be positive")
        if self.embedding_dtype != "float16":
            raise DenseAssetsError("embedding dtype must be float16")
        if not self.normalized or self.similarity != "dot_product":
            raise DenseAssetsError("dense assets must use normalized dot product")
        if self.max_sequence_length < 1:
            raise DenseAssetsError("max sequence length must be positive")
        for value in (
            self.catalog_sha256,
            self.model_id,
            self.model_revision,
            self.embedding_file,
            self.asin_file,
        ):
            if not value:
                raise DenseAssetsError("dense manifest contains an empty required value")
        if Path(self.embedding_file).name != self.embedding_file:
            raise DenseAssetsError("embedding_file must be a local filename")
        if Path(self.asin_file).name != self.asin_file:
            raise DenseAssetsError("asin_file must be a local filename")

    def validate_files(self, asset_dir: str | Path, catalog_path: str | Path) -> None:
        directory = Path(asset_dir)
        try:
            catalog_digest = file_sha256(catalog_path)
        except OSError as error:
            raise DenseAssetsError(f"cannot read catalog: {catalog_path}") from error
        if catalog_digest != self.catalog_sha256:
            raise DenseAssetsError("catalog SHA256 does not match dense assets")
        checksums_path = directory / "checksums.json"
        try:
            checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DenseAssetsError("invalid dense asset checksums") from error
        for filename in (self.embedding_file, self.asin_file):
            path = directory / filename
            expected = checksums.get(filename)
            try:
                actual = file_sha256(path)
            except OSError as error:
                raise DenseAssetsError(f"cannot read dense asset: {filename}") from error
            if not isinstance(expected, str) or actual != expected:
                raise DenseAssetsError(f"checksum mismatch: {filename}")


__all__ = [
    "DenseAssetManifest",
    "DenseAssetsError",
    "file_sha256",
]
