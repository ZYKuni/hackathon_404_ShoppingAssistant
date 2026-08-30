from __future__ import annotations

import unittest

from starter.catalog_normalizer import ExtractedValue, NormalizedProduct, normalize_product
from starter.constraint_matcher import ConstraintMatcher, MatchState
from starter.pipeline_contracts import ConstraintTerm, StateSnapshot


def raw_product(**overrides):
    value = {
        "parent_asin": "A1",
        "title": "Black running shoes",
        "features": ["Lightweight and breathable", "95% polyester, 5% spandex"],
        "description": [],
        "price": 100,
        "categories": ["Clothing, Shoes & Jewelry", "Women", "Running Shoes"],
        "details": {"Department": "Womens", "Color": "Black"},
        "average_rating": 4.5,
        "rating_number": 100,
        "store": "Acme",
    }
    value.update(overrides)
    return value


def state(*, category=None, hard=(), soft=(), excluded=()):
    return StateSnapshot(
        schema_version="0.1.0",
        turn=1,
        category=category,
        hard_constraints=hard,
        soft_preferences=soft,
        excluded=excluded,
    )


class ConstraintMatcherTests(unittest.TestCase):
    def setUp(self):
        self.matcher = ConstraintMatcher()
        self.product = normalize_product(raw_product())

    def test_match_mismatch_and_unknown(self):
        black = self.matcher.match_term(ConstraintTerm("color", ("black",)), self.product)
        white = self.matcher.match_term(ConstraintTerm("color", ("white",)), self.product)
        unknown = self.matcher.match_term(
            ConstraintTerm("style", ("minimalist",)), self.product
        )
        self.assertIs(black.state, MatchState.MATCH)
        self.assertIs(white.state, MatchState.MISMATCH)
        self.assertIs(unknown.state, MatchState.UNKNOWN)

    def test_unknown_price_is_preserved(self):
        product = normalize_product(raw_product(price=None))
        result = self.matcher.evaluate(
            state(hard=(ConstraintTerm("price_max", (120,)),)), product
        )
        self.assertIs(result.hard[0].state, MatchState.UNKNOWN)
        self.assertFalse(result.should_filter)

    def test_price_min_and_max(self):
        self.assertIs(self.matcher.match_term(
            ConstraintTerm("price_min", (80,)), self.product
        ).state, MatchState.MATCH)
        self.assertIs(self.matcher.match_term(
            ConstraintTerm("price_max", (80,)), self.product
        ).state, MatchState.MISMATCH)

    def test_multivalue_positive_uses_any_match(self):
        result = self.matcher.match_term(
            ConstraintTerm("color", ("white", "black")), self.product
        )
        self.assertIs(result.state, MatchState.MATCH)

    def test_excluded_any_hit_and_multicolor(self):
        product = normalize_product(raw_product(
            details={"Department": "Womens", "Color": "Black"},
            features=["Available in white", "Lightweight"],
        ))
        result = self.matcher.evaluate(
            state(excluded=(ConstraintTerm("color", ("white", "red")),)), product
        )
        # White is only weak feature evidence, so it cannot trigger hard filtering.
        self.assertFalse(result.should_filter)
        explicit = normalize_product(raw_product(details={"Color": "White / Black"}))
        # Explicit structured multi-colors are split through exact aliases.
        self.assertTrue(self.matcher.evaluate(
            state(excluded=(ConstraintTerm("color", ("white",)),)), explicit
        ).should_filter)

    def test_explicit_excluded_color_filters(self):
        product = normalize_product(raw_product(details={"Color": "White"}))
        result = self.matcher.evaluate(
            state(excluded=(ConstraintTerm("color", ("white",)),)), product
        )
        self.assertIs(result.excluded[0].state, MatchState.MISMATCH)
        self.assertTrue(result.should_filter)

    def test_material_composition_matches_each_component(self):
        for material in ("polyester", "spandex"):
            with self.subTest(material=material):
                result = self.matcher.match_term(
                    ConstraintTerm("material", (material,)), self.product
                )
                self.assertIs(result.state, MatchState.MATCH)

    def test_faux_leather_is_not_leather(self):
        product = normalize_product(raw_product(
            title="Faux leather bag", features=[], categories=["Bags"], details={}
        ))
        faux = self.matcher.match_term(ConstraintTerm("material", ("faux leather",)), product)
        leather = self.matcher.match_term(ConstraintTerm("material", ("leather",)), product)
        self.assertIs(faux.state, MatchState.MATCH)
        self.assertIs(leather.state, MatchState.UNKNOWN)
        self.assertFalse(self.matcher.evaluate(
            state(hard=(ConstraintTerm("material", ("leather",)),)), product
        ).should_filter)

    def test_weak_conflict_does_not_filter(self):
        weak = NormalizedProduct(
            parent_asin="W1", category_path=(), leaf_categories=(), audiences=(),
            materials=(), colors=(ExtractedValue("red", "title", 0.65),),
            brands=(), sizes=(), styles=(), price=None, features=(),
            average_rating=0.0, rating_number=0,
        )
        result = self.matcher.evaluate(
            state(hard=(ConstraintTerm("color", ("blue",)),)), weak
        )
        self.assertIs(result.hard[0].state, MatchState.UNKNOWN)
        self.assertFalse(result.should_filter)

    def test_soft_mismatch_never_filters(self):
        result = self.matcher.evaluate(
            state(soft=(ConstraintTerm("color", ("white",)),)), self.product
        )
        self.assertIs(result.soft[0].state, MatchState.MISMATCH)
        self.assertFalse(result.should_filter)

    def test_category_leaf_parent_and_cross_category(self):
        leaf = self.matcher.evaluate(state(category="running shoes"), self.product)
        parent = self.matcher.evaluate(state(category="women"), self.product)
        cross = self.matcher.evaluate(state(category="earrings"), self.product)
        self.assertIs(leaf.hard[0].state, MatchState.MATCH)
        self.assertIs(parent.hard[0].state, MatchState.MATCH)
        self.assertIs(cross.hard[0].state, MatchState.MISMATCH)
        self.assertTrue(cross.should_filter)

    def test_same_family_long_tail_category_is_unknown(self):
        product = normalize_product(raw_product(categories=[
            "Clothing, Shoes & Jewelry", "Women", "Clothing", "Tanks & Camisoles"
        ]))
        result = self.matcher.evaluate(state(category="t_shirts"), product)
        self.assertIs(result.hard[0].state, MatchState.UNKNOWN)
        self.assertFalse(result.should_filter)

    def test_feature_match_is_non_decisive_for_filtering(self):
        result = self.matcher.evaluate(
            state(hard=(ConstraintTerm("feature", ("light weight",)),)), self.product
        )
        self.assertIs(result.hard[0].state, MatchState.MATCH)
        self.assertFalse(result.should_filter)

    def test_threshold_is_configurable(self):
        matcher = ConstraintMatcher(0.96)
        result = matcher.evaluate(
            state(hard=(ConstraintTerm("color", ("white",)),)), self.product
        )
        self.assertFalse(result.should_filter)

        weak = NormalizedProduct(
            parent_asin="W1", category_path=(), leaf_categories=(), audiences=(),
            materials=(), colors=(ExtractedValue("red", "title", 0.65),),
            brands=(), sizes=(), styles=(), price=None, features=(),
            average_rating=0.0, rating_number=0,
        )
        permissive = ConstraintMatcher(0.60).evaluate(
            state(hard=(ConstraintTerm("color", ("blue",)),)), weak
        )
        self.assertIs(permissive.hard[0].state, MatchState.MISMATCH)
        self.assertTrue(permissive.should_filter)

    def test_price_requires_one_boundary(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.matcher.match_term(
                ConstraintTerm("price_max", (80, 120)), self.product
            )


if __name__ == "__main__":
    unittest.main()
