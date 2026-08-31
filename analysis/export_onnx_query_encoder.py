"""Export a minimal, checksummed, architecture-aware int8 query encoder."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from starter.dense_assets import file_sha256

from analysis.build_dense_assets import DEFAULT_MODEL_ID, DEFAULT_MODEL_REVISION


def export_query_encoder(
    output_dir: str | Path,
    *,
    model_id: str = DEFAULT_MODEL_ID,
    model_revision: str = DEFAULT_MODEL_REVISION,
    max_sequence_length: int = 256,
) -> Path:
    try:
        from sentence_transformers import SentenceTransformer
        from sentence_transformers.backend import export_dynamic_quantized_onnx_model
    except ImportError as error:
        raise RuntimeError(
            "install requirements-dense-export.txt to export the ONNX query encoder"
        ) from error
    if max_sequence_length < 1:
        raise ValueError("max_sequence_length must be positive")
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"query encoder directory already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    export_work = temporary / "_export_work"
    try:
        model = SentenceTransformer(
            model_id,
            revision=model_revision,
            backend="onnx",
            model_kwargs={"export": True},
        )
        model.max_seq_length = max_sequence_length
        dimension = model.get_sentence_embedding_dimension()
        if dimension is None:
            raise RuntimeError("embedding model did not expose its dimension")
        model.save_pretrained(export_work)
        shutil.copy2(export_work / "tokenizer.json", temporary / "tokenizer.json")
        models = {
            "arm64": "onnx/model_int8_arm64.onnx",
            "avx2": "onnx/model_int8_avx2.onnx",
        }
        export_dynamic_quantized_onnx_model(
            model,
            "arm64",
            temporary,
            file_suffix="int8_arm64",
        )
        export_dynamic_quantized_onnx_model(
            model,
            "avx2",
            temporary,
            file_suffix="int8_avx2",
        )
        shutil.rmtree(export_work)
        required_files = ["tokenizer.json", *models.values()]
        checksums = {
            filename: file_sha256(temporary / filename)
            for filename in required_files
        }
        metadata = {
            "schema_version": "1.0.0",
            "model_id": model_id,
            "model_revision": model_revision,
            "embedding_dimension": int(dimension),
            "max_sequence_length": max_sequence_length,
            "pooling": "mean",
            "normalized": True,
            "tokenizer_file": "tokenizer.json",
            "models": models,
            "sha256": checksums,
        }
        (temporary / "query_encoder_metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--max-sequence-length", type=int, default=256)
    args = parser.parse_args()
    print(export_query_encoder(
        args.output,
        model_id=args.model_id,
        model_revision=args.model_revision,
        max_sequence_length=args.max_sequence_length,
    ))


if __name__ == "__main__":
    main()
