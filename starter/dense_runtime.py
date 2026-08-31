"""Fail-open loader for optional, fully local dense retrieval assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dense_retrieval import DenseMode, DenseSearchBackend


@dataclass(frozen=True)
class DenseRuntimeLoad:
    requested_mode: DenseMode
    effective_mode: DenseMode
    backend: DenseSearchBackend | None = None
    error: str | None = None


def load_optional_dense_backend(
    catalog_path: str | Path,
    asset_dir: str | Path,
    model_dir: str | Path,
    *,
    mode: DenseMode | str = DenseMode.SHADOW,
) -> DenseRuntimeLoad:
    requested = DenseMode(mode)
    if requested is DenseMode.OFF:
        return DenseRuntimeLoad(requested, DenseMode.OFF)
    try:
        # Validate the lightweight, catalog-bound assets before importing the
        # optional NumPy/ONNX runtime.  This keeps the fail-open reason stable
        # across dependency-free and dense-enabled environments: missing or
        # corrupt assets are always reported as ``DenseAssetsError`` rather
        # than whichever optional import happens to fail first.
        from .dense_assets import DenseAssetManifest

        manifest = DenseAssetManifest.load(asset_dir)
        manifest.validate_files(asset_dir, catalog_path)

        from .numpy_dense_backend import NumpyDenseSearchIndex
        from .onnx_query_encoder import OnnxMiniLMQueryEncoder

        encoder = OnnxMiniLMQueryEncoder(model_dir)
        backend = NumpyDenseSearchIndex(
            asset_dir,
            catalog_path,
            encoder=encoder,
        )
    except Exception as error:
        return DenseRuntimeLoad(
            requested_mode=requested,
            effective_mode=DenseMode.OFF,
            error=type(error).__name__,
        )
    return DenseRuntimeLoad(
        requested_mode=requested,
        effective_mode=requested,
        backend=backend,
    )


__all__ = ["DenseRuntimeLoad", "load_optional_dense_backend"]
