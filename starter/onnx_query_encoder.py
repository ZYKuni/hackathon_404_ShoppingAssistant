"""Lightweight offline MiniLM query encoder using ONNX Runtime directly."""

from __future__ import annotations

import math
import platform
import json
from dataclasses import dataclass
from pathlib import Path

from .dense_assets import DenseAssetsError, file_sha256


def select_onnx_model_filename(machine: str | None = None) -> str:
    architecture = (machine or platform.machine()).strip().lower()
    if architecture in {"arm64", "aarch64"}:
        return "model_int8_arm64.onnx"
    if architecture in {"x86_64", "amd64"}:
        return "model_int8_avx2.onnx"
    raise DenseAssetsError(f"unsupported ONNX query-encoder architecture: {architecture}")


@dataclass(frozen=True)
class OnnxQueryEncoderManifest:
    model_id: str
    model_revision: str
    dimension: int
    max_sequence_length: int
    tokenizer_file: str
    models: dict[str, str]
    checksums: dict[str, str]

    @classmethod
    def load(cls, model_dir: str | Path) -> "OnnxQueryEncoderManifest":
        path = Path(model_dir) / "query_encoder_metadata.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            normalized = payload["normalized"]
            if not isinstance(normalized, bool):
                raise TypeError("normalized must be a boolean")
            if payload["schema_version"] != "1.0.0":
                raise ValueError("unsupported schema")
            if payload["pooling"] != "mean" or not normalized:
                raise ValueError("unsupported pooling")
            models = payload["models"]
            checksums = payload["sha256"]
            if not isinstance(models, dict) or not isinstance(checksums, dict):
                raise TypeError("models and sha256 must be objects")
            manifest = cls(
                model_id=str(payload["model_id"]),
                model_revision=str(payload["model_revision"]),
                dimension=int(payload["embedding_dimension"]),
                max_sequence_length=int(payload["max_sequence_length"]),
                tokenizer_file=str(payload["tokenizer_file"]),
                models={str(key): str(value) for key, value in models.items()},
                checksums={str(key): str(value) for key, value in checksums.items()},
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise DenseAssetsError(f"invalid ONNX query-encoder manifest: {path}") from error
        manifest.validate()
        return manifest

    @staticmethod
    def _safe_path(value: str) -> bool:
        path = Path(value)
        return bool(value) and not path.is_absolute() and ".." not in path.parts

    def validate(self) -> None:
        if not self.model_id or not self.model_revision:
            raise DenseAssetsError("query-encoder model identity is missing")
        if self.dimension < 1 or self.max_sequence_length < 1:
            raise DenseAssetsError("query-encoder dimensions must be positive")
        if set(self.models) != {"arm64", "avx2"}:
            raise DenseAssetsError("query-encoder manifest must contain arm64 and avx2")
        required = {self.tokenizer_file, *self.models.values()}
        if any(not self._safe_path(value) for value in required):
            raise DenseAssetsError("query-encoder manifest contains an unsafe path")
        if any(not self.checksums.get(value) for value in required):
            raise DenseAssetsError("query-encoder manifest is missing a checksum")

    def validate_files(self, model_dir: str | Path) -> None:
        directory = Path(model_dir)
        for relative in (self.tokenizer_file, *self.models.values()):
            try:
                actual = file_sha256(directory / relative)
            except OSError as error:
                raise DenseAssetsError(f"cannot read query-encoder asset: {relative}") from error
            if actual != self.checksums[relative]:
                raise DenseAssetsError(f"query-encoder checksum mismatch: {relative}")


class OnnxMiniLMQueryEncoder:
    """Encode one query with local tokenizer + int8 ONNX mean pooling."""

    def __init__(
        self,
        model_dir: str | Path,
        *,
        dimension: int = 384,
        max_sequence_length: int = 256,
        model_filename: str | None = None,
    ) -> None:
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise TypeError("dimension must be an integer")
        if isinstance(max_sequence_length, bool) or not isinstance(max_sequence_length, int):
            raise TypeError("max_sequence_length must be an integer")
        if dimension < 1 or max_sequence_length < 1:
            raise ValueError("dimension and max_sequence_length must be positive")
        directory = Path(model_dir)
        manifest = OnnxQueryEncoderManifest.load(directory)
        manifest.validate_files(directory)
        if manifest.dimension != dimension:
            raise DenseAssetsError("query-encoder dimension does not match dense assets")
        if manifest.max_sequence_length != max_sequence_length:
            raise DenseAssetsError("query-encoder sequence length does not match dense assets")
        self._dimension = dimension
        self.max_sequence_length = max_sequence_length
        tokenizer_path = directory / manifest.tokenizer_file
        filename = model_filename or select_onnx_model_filename()
        if Path(filename).name != filename:
            raise DenseAssetsError("ONNX model filename must be a local filename")
        model_path = directory / "onnx" / filename
        expected_relative = str(model_path.relative_to(directory))
        if expected_relative not in manifest.models.values():
            raise DenseAssetsError("ONNX model is not declared by the manifest")
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:
            raise DenseAssetsError(
                "install requirements-dense-runtime.txt for ONNX query encoding"
            ) from error

        try:
            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            tokenizer.no_padding()
            tokenizer.enable_truncation(max_length=max_sequence_length)
            session = ort.InferenceSession(
                str(model_path),
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:
            raise DenseAssetsError("could not load local ONNX query encoder") from error
        input_names = {value.name for value in session.get_inputs()}
        required = {"input_ids", "attention_mask"}
        if not required.issubset(input_names):
            raise DenseAssetsError("ONNX query encoder has incompatible inputs")
        outputs = session.get_outputs()
        if not outputs or outputs[0].name != "last_hidden_state":
            raise DenseAssetsError("ONNX query encoder has incompatible outputs")
        output_dimension = outputs[0].shape[-1]
        if isinstance(output_dimension, int) and output_dimension != dimension:
            raise DenseAssetsError("ONNX output dimension does not match dense assets")
        self._np = np
        self._tokenizer = tokenizer
        self._session = session
        self._input_names = input_names

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode_query(self, text: str):
        if not isinstance(text, str):
            raise TypeError("query text must be a string")
        if not text.strip():
            raise ValueError("query text must not be empty")
        encoding = self._tokenizer.encode(text)
        np = self._np
        attention_mask = np.asarray([encoding.attention_mask], dtype=np.int64)
        inputs = {
            "input_ids": np.asarray([encoding.ids], dtype=np.int64),
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self._input_names:
            inputs["token_type_ids"] = np.asarray([encoding.type_ids], dtype=np.int64)
        try:
            hidden = np.asarray(
                self._session.run(["last_hidden_state"], inputs)[0],
                dtype=np.float32,
            )
        except Exception as error:
            raise DenseAssetsError("ONNX query encoding failed") from error
        if hidden.ndim != 3 or hidden.shape[:2] != attention_mask.shape:
            raise DenseAssetsError("ONNX query encoder returned an invalid shape")
        mask = attention_mask[..., None].astype(np.float32)
        denominator = float(mask.sum())
        if denominator <= 0:
            raise DenseAssetsError("tokenizer returned an empty attention mask")
        vector = (hidden * mask).sum(axis=1)[0] / denominator
        if vector.shape != (self._dimension,) or not np.isfinite(vector).all():
            raise DenseAssetsError("ONNX query encoder returned an invalid vector")
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm <= 0:
            raise DenseAssetsError("ONNX query encoder returned a zero-length vector")
        return vector / norm


__all__ = [
    "OnnxMiniLMQueryEncoder",
    "OnnxQueryEncoderManifest",
    "select_onnx_model_filename",
]
