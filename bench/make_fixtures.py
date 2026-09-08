#!/usr/bin/env python
"""Generate the bulky fixtures the long_context suite needs.

A FoodPlanner user with a year of history has a large item book and a large
recipe collection, and get_prices / search_recipes hand those to the model in
one tool result. The point of these files is to find where a 64k window starts
dropping detail, so each one buries a needle whose answer cannot be guessed.

    python make_fixtures.py            # writes fixtures/itembook-{8k,16k,32k}.json
"""
from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).parent / "fixtures"

CATEGORIES = ["frukt & grönt", "bröd", "mejeri", "kött & fisk", "fryst", "skafferi",
              "dryck", "övrigt"]

BASE = [
    ("vetemjöl", "skafferi"), ("strösocker", "skafferi"), ("havregryn", "skafferi"),
    ("pasta", "skafferi"), ("ris", "skafferi"), ("linser", "skafferi"),
    ("kokosmjölk", "skafferi"), ("krossade tomater", "skafferi"), ("olivolja", "skafferi"),
    ("rapsolja", "skafferi"), ("salt", "skafferi"), ("svartpeppar", "skafferi"),
    ("kanel", "skafferi"), ("paprikapulver", "skafferi"), ("bakpulver", "skafferi"),
    ("mjölk", "mejeri"), ("grädde", "mejeri"), ("crème fraiche", "mejeri"),
    ("smör", "mejeri"), ("yoghurt", "mejeri"), ("riven ost", "mejeri"),
    ("halloumi", "mejeri"), ("ägg", "mejeri"), ("kvarg", "mejeri"),
    ("kycklingfilé", "kött & fisk"), ("nötfärs", "kött & fisk"), ("falukorv", "kött & fisk"),
    ("lax", "kött & fisk"), ("torskrygg", "kött & fisk"), ("bacon", "kött & fisk"),
    ("gul lök", "frukt & grönt"), ("vitlök", "frukt & grönt"), ("morötter", "frukt & grönt"),
    ("potatis", "frukt & grönt"), ("paprika", "frukt & grönt"), ("spenat", "frukt & grönt"),
    ("broccoli", "frukt & grönt"), ("citron", "frukt & grönt"), ("bananer", "frukt & grönt"),
    ("äpplen", "frukt & grönt"), ("tomater", "frukt & grönt"), ("purjolök", "frukt & grönt"),
    ("knäckebröd", "bröd"), ("tortillabröd", "bröd"), ("frallor", "bröd"),
    ("frysta räkor", "fryst"), ("fiskpinnar", "fryst"), ("ärtor", "fryst"),
    ("kaffe", "skafferi"), ("te", "skafferi"), ("apelsinjuice", "dryck"),
    ("havredryck", "dryck"), ("mineralvatten", "dryck"),
    ("diskmedel", "övrigt"), ("toapapper", "övrigt"), ("hushållspapper", "övrigt"),
]

MODIFIERS = ["", "ekologisk ", "svensk ", "färsk ", "riven ", "hackad ", "grovmalen ",
             "finmalen ", "kravmärkt ", "laktosfri ", "osaltad ", "kylskåpskall "]

# The needle. Nothing about it is guessable from world knowledge: a made-up item
# with an arbitrary price and a category that is deliberately not the obvious one.
NEEDLE = {
    "name": "surdegsknäcke från Vretstorp",
    "price_per_100": 42.5,
    "unit": "g",
    "source": "user",
    "category": "bröd",
    "livsmedel": {"livsmedel_id": 90210, "food_name": "Knäckebröd, rågsikt", "source": "user"},
    "updated_at": "2026-08-14T09:12:00Z",
}


def make_items(n: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    items, used = [], set()
    while len(items) < n:
        base, cat = rng.choice(BASE)
        name = (rng.choice(MODIFIERS) + base).strip()
        if name in used:
            continue
        used.add(name)
        items.append({
            "name": name,
            "price_per_100": round(rng.uniform(3, 180), 2),
            "unit": rng.choice(["g", "ml", "piece"]),
            "source": rng.choice(["estimated", "estimated", "estimated", "user"]),
            "category": cat,
            "livsmedel": {"livsmedel_id": rng.randint(100, 9999),
                          "food_name": base.capitalize(),
                          "source": "claude"},
            "updated_at": f"2026-0{rng.randint(1,9)}-{rng.randint(10,28)}T"
                          f"{rng.randint(10,23)}:{rng.randint(10,59)}:00Z",
        })
    return items


def write(target_tokens: int) -> Path:
    # ~4 chars per token; grow until the serialized file is close to target.
    n = 40
    while True:
        items = make_items(n)
        items.insert(len(items) // 2, dict(NEEDLE))     # bury it in the middle
        doc = {"items": items, "count": len(items), "currency": "SEK",
               "max_age_days": 120}
        blob = json.dumps(doc, ensure_ascii=False, indent=1)
        if len(blob) // 4 >= target_tokens or n > 4000:
            break
        n = int(n * 1.6) + 10
    path = OUT / f"itembook-{target_tokens // 1000}k.json"
    path.write_text(blob, encoding="utf-8")
    print(f"{path.name}: {len(doc['items'])} items, {len(blob)} chars, ~{len(blob)//4} tokens")
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for t in (8000, 16000, 32000):
        write(t)
    print(f"\nNeedle buried in every file: {NEEDLE['name']!r} "
          f"-> {NEEDLE['price_per_100']} SEK/100{NEEDLE['unit']}, category {NEEDLE['category']!r}")
