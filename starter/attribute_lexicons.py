"""Shared canonical values for dialogue parsing and catalog normalization.

Both sides of the system must import this module instead of maintaining separate
spellings.  The dictionaries intentionally cover only high-confidence MVP terms;
unknown values are normalized but preserved rather than silently discarded.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Mapping


SCHEMA_VERSION = "0.1.0"

ALLOWED_ASK_ATTRIBUTES = {
    "category", "material", "color", "size", "style", "brand",
    "budget", "feature", "use_case", "other",
}

STATE_FIELDS = {
    "category", "audience", "price_min", "price_max", "color", "material",
    "brand", "size", "style", "use_case", "feature",
}
SINGLE_VALUE_FIELDS = {"category", "price_min", "price_max"}
MULTI_VALUE_FIELDS = STATE_FIELDS - SINGLE_VALUE_FIELDS

# A category change invalidates product-scoped constraints unless the new turn
# explicitly restates them.  User-profile data lives outside this state and is not
# cleared here.
PRODUCT_SCOPED_FIELDS = set(STATE_FIELDS)

DEFAULT_STRENGTH = {
    "category": "hard",
    "audience": "hard",
    "price_min": "hard",
    "price_max": "hard",
    "color": "soft",
    "material": "soft",
    "brand": "soft",
    "size": "hard",
    "style": "soft",
    "use_case": "soft",
    "feature": "soft",
}

FIELD_TO_ASK_ATTRIBUTE = {
    "category": "category",
    "audience": "category",
    "price_min": "budget",
    "price_max": "budget",
    "color": "color",
    "material": "material",
    "brand": "brand",
    "size": "size",
    "style": "style",
    "use_case": "use_case",
    "feature": "feature",
}

AUDIENCE_ALIASES = {
    "woman": "women",
    "women": "women",
    "womens": "women",
    "women's": "women",
    "female": "women",
    "ladies": "women",
    "man": "men",
    "men": "men",
    "mens": "men",
    "men's": "men",
    "male": "men",
    "girl": "girls",
    "girls": "girls",
    "boy": "boys",
    "boys": "boys",
    "baby girl": "baby_girls",
    "baby girls": "baby_girls",
    "baby boy": "baby_boys",
    "baby boys": "baby_boys",
    "unisex": "unisex_adult",
    "unisex adult": "unisex_adult",
    "unisex child": "unisex_child",
    "unisex kids": "unisex_child",
}

COLOR_ALIASES = {
    "black": "black",
    "white": "white",
    "blue": "blue",
    "navy": "navy",
    "navy blue": "navy",
    "red": "red",
    "pink": "pink",
    "green": "green",
    "brown": "brown",
    "gray": "gray",
    "grey": "gray",
    "purple": "purple",
    "yellow": "yellow",
    "orange": "orange",
    "beige": "beige",
    "gold": "gold",
    "silver": "silver",
    "multi": "multicolor",
    "multicolour": "multicolor",
    "multicolor": "multicolor",
    "multicolored": "multicolor",
    "multicoloured": "multicolor",
}

MATERIAL_ALIASES = {
    "cotton": "cotton",
    "100% cotton": "cotton",
    "poly": "polyester",
    "polyester": "polyester",
    "nylon": "nylon",
    "leather": "leather",
    "genuine leather": "leather",
    "faux leather": "faux_leather",
    "synthetic leather": "faux_leather",
    "wool": "wool",
    "spandex": "spandex",
    "silk": "silk",
    "rayon": "rayon",
    "linen": "linen",
    "denim": "denim",
    "acrylic": "acrylic",
    "rubber": "rubber",
    "mesh": "mesh",
    "fleece": "fleece",
    "suede": "suede",
    "stainless steel": "stainless_steel",
    "sterling silver": "sterling_silver",
}

CATEGORY_ALIASES = {
    "running shoe": "running_shoes",
    "running shoes": "running_shoes",
    "road running": "running_shoes",
    "trainer": "sneakers",
    "trainers": "sneakers",
    "sneaker": "sneakers",
    "sneakers": "sneakers",
    "fashion sneakers": "sneakers",
    "boot": "boots",
    "boots": "boots",
    "winter boot": "winter_boots",
    "winter boots": "winter_boots",
    "sandal": "sandals",
    "sandals": "sandals",
    "slipper": "slippers",
    "slippers": "slippers",
    "t shirt": "t_shirts",
    "t shirts": "t_shirts",
    "tee": "t_shirts",
    "tees": "t_shirts",
    "dress": "dresses",
    "dresses": "dresses",
    "jacket": "jackets",
    "jackets": "jackets",
    "winter jacket": "winter_jackets",
    "winter jackets": "winter_jackets",
    "jean": "jeans",
    "jeans": "jeans",
    "legging": "leggings",
    "leggings": "leggings",
    "hoodie": "hoodies",
    "hoodies": "hoodies",
    "watch": "watches",
    "watches": "watches",
    "wrist watches": "watches",
    "earring": "earrings",
    "earrings": "earrings",
    "necklace": "necklaces",
    "necklaces": "necklaces",
}

USE_CASE_ALIASES = {
    "run": "running",
    "running": "running",
    "walk": "walking",
    "walking": "walking",
    "hike": "hiking",
    "hiking": "hiking",
    "gym": "gym",
    "workout": "gym",
    "work": "work",
    "office": "work",
    "travel": "travel",
    "travelling": "travel",
    "traveling": "travel",
    "wedding": "wedding",
    "outdoor": "outdoor",
    "outdoors": "outdoor",
    "winter": "winter",
    "summer": "summer",
}

FEATURE_ALIASES = {
    "water proof": "waterproof",
    "waterproof": "waterproof",
    "water resistant": "water_resistant",
    "light weight": "lightweight",
    "lightweight": "lightweight",
    "breathable": "breathable",
    "warm": "warm",
    "insulated": "insulated",
    "durable": "durable",
    "comfortable": "comfortable",
    "comfort": "comfortable",
    "hypoallergenic": "hypoallergenic",
}


def normalize_phrase(value: object) -> str:
    """Normalize a phrase without inventing a value or dropping unknown text."""
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9&+' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _canonical_from(mapping: Mapping[str, str], value: object) -> str:
    normalized = normalize_phrase(value)
    return mapping.get(normalized, normalized.replace(" ", "_"))


def canonicalize(field: str, value: object) -> object:
    """Return the shared canonical representation for a state field."""
    if field not in STATE_FIELDS:
        raise ValueError(f"Unsupported state field: {field}")
    if field in {"price_min", "price_max"}:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be numeric, not bool")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field} must be numeric: {value!r}") from error
        if number < 0:
            raise ValueError(f"{field} cannot be negative")
        return int(number) if number.is_integer() else number
    if field == "audience":
        return _canonical_from(AUDIENCE_ALIASES, value)
    if field == "color":
        return _canonical_from(COLOR_ALIASES, value)
    if field == "material":
        return _canonical_from(MATERIAL_ALIASES, value)
    if field == "category":
        return _canonical_from(CATEGORY_ALIASES, value)
    if field == "use_case":
        return _canonical_from(USE_CASE_ALIASES, value)
    if field == "feature":
        return _canonical_from(FEATURE_ALIASES, value)
    if field == "size":
        return normalize_phrase(value).upper()
    # Brand and style remain open vocabularies in v0.1.
    return normalize_phrase(value)


def ask_attribute_for(field: str) -> str:
    try:
        return FIELD_TO_ASK_ATTRIBUTE[field]
    except KeyError as error:
        raise ValueError(f"Unsupported state field: {field}") from error
