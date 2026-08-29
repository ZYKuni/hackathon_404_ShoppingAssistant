# Catalog Business Rules v0.1

This document translates shopping language into catalog evidence rules for the
planned `Catalog Normalizer`. It is deliberately separate from the current
Agent so that the business assumptions can be reviewed before they affect
ranking metrics.

## Objective

Normalize both sides of retrieval into the same attributes:

```text
user_message -> category / audience / budget / color / material / brand / size / style / use_case / feature
catalog item  -> category / audience / price  / color / material / brand / size / style / use_case / feature
```

Unknown evidence must stay unknown. Missing catalog text does not mean that a
product fails a preference.

## Evidence priority

| Attribute | Strongest evidence | Secondary evidence | Weak fallback |
|---|---|---|---|
| Category | leaf and full `categories` path | explicit product noun in `title` | `features`, `description` |
| Audience | `details.Department` or `details.Suggested Users` | audience node in `categories` | `title`, `features` |
| Price | numeric `price` | parseable textual price | unknown |
| Color | `details.Color` | explicit color in `title` | `features`, `description` |
| Material | `details.Material` | `features` | `title`, `description` |
| Brand | `details.Brand` / `details.Brand Name` | `details.Manufacturer` | `store`, then `title` |
| Size | `details.Size` plus category context | explicit size in `title` | `features`, `description` |
| Style | structured style / fit details | `title`, `features` | `description` |
| Use case | explicit category or sport details | `title`, `features` | `description` |
| Feature | structured special-feature details | `features` | `title`, `description` |

When sources disagree, keep the stronger source and log the conflict for audit.
`store` is brand-like evidence, not guaranteed ground truth.

## Hard constraints and soft preferences

| Attribute | Default interpretation | Upgrade / downgrade rule |
|---|---|---|
| Category | Hard | Ask for clarification if no useful category is known |
| Audience | Hard when explicitly stated | Unknown product audience remains UNKNOWN, not FAIL |
| Price | Hard when explicitly bounded | Missing or non-numeric product price remains UNKNOWN |
| Size | Hard when explicitly stated | Interpret only inside product-category context |
| Color | Soft | `must`, `only`, `required` upgrades to hard |
| Material | Soft | `must`, `only`, `required` upgrades to hard |
| Brand | Soft | `only` or explicit brand loyalty upgrades to hard |
| Style | Soft | Explicit rejection becomes an exclusion |
| Use case | Soft | Can drive category expansion during browsing |
| Feature | Soft | `must`, `only`, `required` upgrades to hard |

Language such as `prefer`, `ideally`, `would be nice`, and `could work` should
remain soft. Language such as `not`, `anything but`, and `no` creates an
exclusion, not a positive preference.

## Tri-state filtering

Every hard condition should return one of three states:

- **PASS**: catalog evidence confirms the condition.
- **FAIL**: reliable catalog evidence contradicts the condition.
- **UNKNOWN**: the field is missing, ambiguous, or only weakly inferred.

Suggested retrieval behavior:

1. Rank PASS products first.
2. Keep UNKNOWN products as lower-confidence candidates when PASS recall is insufficient.
3. Remove FAIL products.
4. Never convert UNKNOWN to FAIL merely because a field is absent.

This is especially important for budget because most catalog prices are missing.

## Attribute-specific caveats

### Category

- Use the entire category path, not only the leaf string.
- Preserve raw category text for debugging.
- Map aliases such as `road running` and `running shoe` to one canonical value.
- Flag suspicious leaf labels during manual audit instead of silently rewriting them.

### Price

- Accept numeric `int` / `float` values directly.
- Text such as `from 12.99` may provide a lower-bound hint but is not an exact price.
- Placeholder symbols and missing values remain unknown.
- Do not apply a strict budget FAIL unless a trustworthy comparable price exists.

### Brand

- Prefer explicit brand fields over manufacturer, and manufacturer over `store`.
- Keep genuine brand names as open vocabulary after basic normalization.
- Values such as `Generic` should not become strong personalization evidence.

### Size

- Shoe, apparel, jewelry, and accessory sizes are different namespaces.
- Preserve width terms such as `wide` separately when possible.
- Do not compare raw size strings across unrelated categories.

### Color and material

- Support multiple acceptable values and explicit exclusions.
- Keep `faux_leather` distinct from genuine `leather`.
- Normalize spelling variants such as `grey -> gray` without deleting the raw evidence.

### Use case and feature

- The same phrase may be a use case or a feature depending on context.
- `winter` is normally a use case; `insulated` and `warm` are normally features.
- Use-case evidence should help retrieval expansion, while feature evidence should help reranking.

## Manual audit protocol

`docs/catalog_manual_audit_sample.csv` contains a deterministic 50-product
sample balanced across common leaf categories. Two business reviewers should:

1. Fill the `review_*` columns independently for the first 10 overlapping products.
2. Resolve disagreements and update this rule document or the shared lexicon.
3. Divide the remaining products between reviewers.
4. Record missing data, conflicting evidence, suspicious categories, and ambiguous sizes.
5. Give the finalized sheet to the Catalog Normalizer implementer as an acceptance set.

The audit sheet is not training data and must not be used to hand-tune individual
public targets.

## Acceptance criteria for Catalog Normalizer

- Never changes `parent_asin` or the frozen catalog.
- Imports shared aliases from `starter/attribute_lexicons.py`.
- Preserves raw evidence and records source/confidence for normalized values.
- Handles missing values without exceptions.
- Uses category-aware size interpretation.
- Produces PASS / FAIL / UNKNOWN for hard constraints.
- Passes the reviewed manual sample before integration into `agent.py`.
