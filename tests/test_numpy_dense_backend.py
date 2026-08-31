from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from analysis.build_dense_assets import build_assets
from starter.dense_assets import DenseAssetsError
from starter.numpy_dense_backend import NumpyDenseSearchIndex


class DocumentEncoder:
    dimension = 3

    def encode_documents(self, texts):
        vectors = {
            "Red Cotton Shirt": [1.0, 0.0, 0.0],
            "Blue Hiking Boot": [0.0, 1.0, 0.0],
            "Green Trail Shoe": [0.0, 0.8, 0.6],
        }
        return np.asarray([
            vectors[next(title for title in vectors if title in text)]
            for text in texts
        ], dtype=np.float32)


class QueryEncoder:
    dimension = 3

    def __init__(self):
        self.calls = []

    def encode_query(self, text):
        self.calls.append(text)
        return np.asarray([0.0, 1.0, 0.0], dtype=np.float32)


class NumpyDenseSearchIndexTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.catalog = self.root / "catalog.jsonl"
        products = (
            {"parent_asin": "P_SHIRT", "title": "Red Cotton Shirt"},
            {"parent_asin": "P_BOOT", "title": "Blue Hiking Boot"},
            {"parent_asin": "P_TRAIL", "title": "Green Trail Shoe"},
        )
        self.catalog.write_text(
            "".join(json.dumps(product) + "\n" for product in products),
            encoding="utf-8",
        )
        self.assets = self.root / "assets"
        build_assets(
            self.catalog,
            self.assets,
            encoder=DocumentEncoder(),
            model_id="fake/minilm",
            model_revision="fixed",
            batch_size=2,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_search_uses_mmap_dot_product_top_k_and_cache(self):
        encoder = QueryEncoder()
        backend = NumpyDenseSearchIndex(
            self.assets,
            self.catalog,
            encoder=encoder,
            block_size=2,
        )

        first = backend.search("hiking footwear", 2)
        second = backend.search("hiking footwear", 2)

        self.assertIsInstance(backend.embeddings, np.memmap)
        self.assertEqual([item.parent_asin for item in first], ["P_BOOT", "P_TRAIL"])
        self.assertGreater(first[0].score, first[1].score)
        self.assertEqual(first, second)
        self.assertEqual(encoder.calls, ["hiking footwear"])

    def test_empty_query_and_limit_validation(self):
        backend = NumpyDenseSearchIndex(
            self.assets,
            self.catalog,
            encoder=QueryEncoder(),
        )
        self.assertEqual(backend.search("", 2), ())
        with self.assertRaises(ValueError):
            backend.search("shoe", 0)
        with self.assertRaises(TypeError):
            backend.search("shoe", True)

    def test_rejects_query_encoder_dimension_mismatch(self):
        encoder = QueryEncoder()
        encoder.dimension = 4
        with self.assertRaisesRegex(DenseAssetsError, "dimension"):
            NumpyDenseSearchIndex(self.assets, self.catalog, encoder=encoder)

    def test_stable_asin_tie_break(self):
        class TieQueryEncoder(QueryEncoder):
            def encode_query(self, text):
                return np.asarray([1.0, 0.0, 0.0], dtype=np.float32)

        embeddings = np.load(self.assets / "catalog_embeddings.f16.npy", mmap_mode="r+")
        embeddings[0] = np.asarray([1.0, 0.0, 0.0], dtype=np.float16)
        embeddings[1] = np.asarray([1.0, 0.0, 0.0], dtype=np.float16)
        embeddings.flush()
        del embeddings
        backend = NumpyDenseSearchIndex(
            self.assets,
            self.catalog,
            encoder=TieQueryEncoder(),
            validate_checksums=False,
        )

        hits = backend.search("tie", 2)

        self.assertEqual([item.parent_asin for item in hits], ["P_BOOT", "P_SHIRT"])


if __name__ == "__main__":
    unittest.main()
