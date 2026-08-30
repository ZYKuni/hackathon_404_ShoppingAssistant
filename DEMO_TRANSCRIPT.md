# Demonstrated multi-turn session

Captured from `python demo_session.py --catalog data/catalog.jsonl` on
2026-08-31. Recommendations below are abbreviated to the first three of ten;
the script prints the complete machine-readable response for every turn.

## Turn 1 — broad Browsing request

**Customer:** I'm looking for women's running shoes, but I'm still exploring.

**Agent:** I found a broad set of possible matches. Do you have a preferred material?

- `ask_attribute`: `material`
- first recommendations: `B08D6GYP6Y`, `B005BQXZV4`, `B002LVV4WE`
- fallback events: none

## Turn 2 — accumulated preferences

**Customer:** Lightweight and breathable matter most, preferably cotton.

**Agent:** I found a broad set of possible matches. Which product feature matters most to you?

- `ask_attribute`: `feature`
- first recommendations: `B09JSGXP1N`, `B07J6R5K8C`, `B07T4PB5GZ`
- fallback events: none

## Turn 3 — explicit category and intent override

**Customer:** Actually, ignore my earlier preference. What I need is a black waterproof hiking boot.

**Agent:** I found a broad set of possible matches. Do you have a preferred material?

- `ask_attribute`: `material`
- first recommendations: `B01N638H08`, `B006UN6MJO`, `B07YR7GCSG`
- fallback events: none

The shoe evidence from turns 1–2 is discarded when the explicit boot category
arrives. The customer-facing message also exposes candidate overload and asks a
new unresolved field. Every response reported token usage `0/0`.
