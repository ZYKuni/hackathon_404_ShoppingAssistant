from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from starter.dense_assets import DenseAssetsError, file_sha256
from starter.onnx_query_encoder import (
    OnnxMiniLMQueryEncoder,
    OnnxQueryEncoderManifest,
    select_onnx_model_filename,
)


class OnnxQueryEncoderTests(unittest.TestCase):
    def test_selects_architecture_specific_quantized_model(self):
        self.assertEqual(select_onnx_model_filename("arm64"), "model_int8_arm64.onnx")
        self.assertEqual(select_onnx_model_filename("aarch64"), "model_int8_arm64.onnx")
        self.assertEqual(select_onnx_model_filename("x86_64"), "model_int8_avx2.onnx")
        self.assertEqual(select_onnx_model_filename("AMD64"), "model_int8_avx2.onnx")
        with self.assertRaisesRegex(DenseAssetsError, "unsupported"):
            select_onnx_model_filename("riscv64")

    def test_rejects_invalid_dimensions_before_loading_dependencies(self):
        with self.assertRaises(TypeError):
            OnnxMiniLMQueryEncoder("missing", dimension=True)
        with self.assertRaises(ValueError):
            OnnxMiniLMQueryEncoder("missing", max_sequence_length=0)

    def test_rejects_missing_or_unsafe_local_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(DenseAssetsError, "invalid ONNX"):
                OnnxMiniLMQueryEncoder(root, model_filename="model.onnx")

            metadata = {
                "schema_version": "1.0.0",
                "model_id": "fake",
                "model_revision": "fixed",
                "embedding_dimension": 384,
                "max_sequence_length": 256,
                "pooling": "mean",
                "normalized": True,
                "tokenizer_file": "../tokenizer.json",
                "models": {
                    "arm64": "onnx/model_int8_arm64.onnx",
                    "avx2": "onnx/model_int8_avx2.onnx",
                },
                "sha256": {
                    "../tokenizer.json": "bad",
                    "onnx/model_int8_arm64.onnx": "bad",
                    "onnx/model_int8_avx2.onnx": "bad",
                },
            }
            (root / "query_encoder_metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            with self.assertRaisesRegex(DenseAssetsError, "unsafe path"):
                OnnxQueryEncoderManifest.load(root)

    def test_manifest_validates_all_portable_assets_and_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "onnx").mkdir()
            files = {
                "tokenizer.json": b"tokenizer",
                "onnx/model_int8_arm64.onnx": b"arm",
                "onnx/model_int8_avx2.onnx": b"x86",
            }
            for relative, content in files.items():
                (root / relative).write_bytes(content)
            metadata = {
                "schema_version": "1.0.0",
                "model_id": "fake",
                "model_revision": "fixed",
                "embedding_dimension": 384,
                "max_sequence_length": 256,
                "pooling": "mean",
                "normalized": True,
                "tokenizer_file": "tokenizer.json",
                "models": {
                    "arm64": "onnx/model_int8_arm64.onnx",
                    "avx2": "onnx/model_int8_avx2.onnx",
                },
                "sha256": {
                    relative: file_sha256(root / relative) for relative in files
                },
            }
            (root / "query_encoder_metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            manifest = OnnxQueryEncoderManifest.load(root)
            manifest.validate_files(root)
            (root / "onnx/model_int8_arm64.onnx").write_bytes(b"corrupt")
            with self.assertRaisesRegex(DenseAssetsError, "checksum mismatch"):
                manifest.validate_files(root)


if __name__ == "__main__":
    unittest.main()
