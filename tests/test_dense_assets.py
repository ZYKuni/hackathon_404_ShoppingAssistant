from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.dense_assets import DenseAssetManifest, DenseAssetsError, file_sha256

try:
    import numpy as np
    from analysis.build_dense_assets import build_assets
except ModuleNotFoundError as error:
    if error.name != "numpy":
        raise
    np = None
    build_assets = None


class FakeEncoder:
    dimension = 3

    def encode_documents(self, texts):
        return np.asarray([
            [len(text), text.count("waterproof") + 1, index + 1]
            for index, text in enumerate(texts)
        ], dtype=np.float32)


@unittest.skipIf(np is None, "optional dense tests require NumPy")
class DenseAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.catalog = self.root / "catalog.jsonl"
        products = (
            {
                "parent_asin": "P1",
                "title": "Waterproof Hiking Boot",
                "categories": ["Shoes", "Boots"],
                "features": ["waterproof"],
            },
            {
                "parent_asin": "P2",
                "title": "Cotton Running Shoe",
                "categories": ["Shoes", "Running"],
                "features": ["breathable"],
            },
        )
        self.catalog.write_text(
            "".join(json.dumps(product) + "\n" for product in products),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_builds_catalog_bound_normalized_float16_assets(self):
        before = file_sha256(self.catalog)
        output = self.root / "dense-assets"

        build_assets(
            self.catalog,
            output,
            encoder=FakeEncoder(),
            model_id="fake/minilm",
            model_revision="fixed-revision",
            batch_size=1,
        )

        manifest = DenseAssetManifest.load(output)
        manifest.validate_files(output, self.catalog)
        embeddings = np.load(
            output / manifest.embedding_file,
            mmap_mode="r",
            allow_pickle=False,
        )
        self.assertEqual(embeddings.shape, (2, 3))
        self.assertEqual(embeddings.dtype, np.float16)
        self.assertTrue(np.allclose(
            np.linalg.norm(embeddings.astype(np.float32), axis=1),
            np.ones(2),
            atol=1e-3,
        ))
        self.assertEqual(
            (output / manifest.asin_file).read_text().splitlines(),
            ["P1", "P2"],
        )
        self.assertEqual(file_sha256(self.catalog), before)

    def test_rejects_catalog_mismatch_and_existing_output(self):
        output = self.root / "dense-assets"
        build_assets(
            self.catalog,
            output,
            encoder=FakeEncoder(),
            model_id="fake/minilm",
            model_revision="fixed-revision",
        )
        self.catalog.write_text(
            self.catalog.read_text() + json.dumps({"parent_asin": "P3", "title": "Hat"}) + "\n",
            encoding="utf-8",
        )

        manifest = DenseAssetManifest.load(output)
        with self.assertRaisesRegex(DenseAssetsError, "catalog SHA256"):
            manifest.validate_files(output, self.catalog)
        with self.assertRaises(FileExistsError):
            build_assets(
                self.catalog,
                output,
                encoder=FakeEncoder(),
                model_id="fake/minilm",
                model_revision="fixed-revision",
            )

    def test_rejects_duplicate_asins(self):
        duplicate = {"parent_asin": "P1", "title": "Duplicate"}
        self.catalog.write_text(
            self.catalog.read_text() + json.dumps(duplicate) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "duplicate parent_asin"):
            build_assets(
                self.catalog,
                self.root / "dense-assets",
                encoder=FakeEncoder(),
                model_id="fake/minilm",
                model_revision="fixed-revision",
            )

    def test_rejects_invalid_manifest_types_and_text_schema(self):
        output = self.root / "dense-assets"
        build_assets(
            self.catalog,
            output,
            encoder=FakeEncoder(),
            model_id="fake/minilm",
            model_revision="fixed-revision",
        )
        metadata_path = output / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["normalized"] = "false"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(DenseAssetsError):
            DenseAssetManifest.load(output)

        metadata["normalized"] = True
        metadata["text_schema_version"] = "future-schema"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaises(DenseAssetsError):
            DenseAssetManifest.load(output)

    def test_missing_asset_is_wrapped_as_dense_assets_error(self):
        output = self.root / "dense-assets"
        build_assets(
            self.catalog,
            output,
            encoder=FakeEncoder(),
            model_id="fake/minilm",
            model_revision="fixed-revision",
        )
        manifest = DenseAssetManifest.load(output)
        (output / manifest.embedding_file).unlink()
        with self.assertRaisesRegex(DenseAssetsError, "cannot read dense asset"):
            manifest.validate_files(output, self.catalog)


if __name__ == "__main__":
    unittest.main()
