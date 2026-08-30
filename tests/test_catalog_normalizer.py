import copy
import unittest

from starter.catalog_normalizer import (
    CatalogNormalizer,
    ExtractedValue,
    normalize_product,
)


def product(**overrides):
    value = {
        "parent_asin": "A1",
        "title": "Women's lightweight grey jacket",
        "features": ["95% Polyester, 5% Spandex"],
        "description": [],
        "price": 20,
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Jackets"],
        "details": {},
        "average_rating": 4.5,
        "rating_number": 10,
        "store": "Example Brand",
    }
    value.update(overrides)
    return value


class CatalogNormalizerTests(unittest.TestCase):
    def test_does_not_mutate_input_and_preserves_asin(self):
        raw = product()
        before = copy.deepcopy(raw)
        normalized = normalize_product(raw)
        self.assertEqual(raw, before)
        self.assertEqual(normalized.parent_asin, "A1")

    def test_source_priority_and_canonical_deduplication(self):
        normalized = normalize_product(product(
            title="Grey cotton jacket",
            features=["100% cotton", "color: grey"],
            details={"Material": "Cotton", "Color": "Gray", "Department": "Womens"},
        ))
        self.assertEqual([(v.value, v.confidence) for v in normalized.materials], [("cotton", 0.95)])
        self.assertEqual([(v.value, v.confidence) for v in normalized.colors], [("gray", 0.95)])
        self.assertEqual(normalized.audiences[0].value, "women")

    def test_material_composition_and_faux_leather_are_distinct(self):
        normalized = normalize_product(product(
            title="Faux leather bag",
            features=["95% Polyester, 5% Spandex"],
            categories=["Bags"],
        ))
        values = {v.value for v in normalized.materials}
        self.assertTrue({"faux_leather", "polyester", "spandex"} <= values)
        self.assertNotIn("leather", values)

    def test_unknown_structured_values_are_preserved(self):
        normalized = normalize_product(product(details={
            "Material": "Moon Fiber", "Color": "Aurora", "Style": "Neo Classic",
            "Size": "petite 4", "Brand": "Odd & Co",
        }))
        self.assertEqual(normalized.materials[0].value, "moon_fiber")
        self.assertEqual(normalized.colors[0].value, "aurora")
        self.assertEqual(normalized.styles[0].value, "neo classic")
        self.assertEqual(normalized.sizes[0].value, "PETITE 4")

    def test_price_number_string_missing_and_invalid(self):
        self.assertEqual(normalize_product(product(price=12.99)).price.value, 12.99)
        self.assertEqual(normalize_product(product(price="$12.99")).price.value, 12.99)
        for value in (None, "from $12.99", "varies", -1, float("nan"), True):
            self.assertIsNone(normalize_product(product(price=value)).price)

    def test_category_leaf_and_compact_features(self):
        normalized = normalize_product(product(
            features=["Water proof and breathable for running"],
            categories=["Clothing", "Road Running"],
        ))
        self.assertEqual(normalized.category_path[-1], "running_shoes")
        self.assertEqual(normalized.leaf_categories[0].value, "running_shoes")
        self.assertTrue({"waterproof", "breathable", "running"} <= set(normalized.features))

    def test_unknown_feature_text_is_preserved_for_open_vocabulary_ranking(self):
        normalized = normalize_product(product(features=["Solar-charged luminous trim"]))
        self.assertIn("solar charged luminous trim", normalized.features)

    def test_confidence_validation(self):
        with self.assertRaises(ValueError):
            ExtractedValue("x", "test", 1.1)

    def test_index_rejects_duplicate_asin(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            CatalogNormalizer([product(), product()])


if __name__ == "__main__":
    unittest.main()
