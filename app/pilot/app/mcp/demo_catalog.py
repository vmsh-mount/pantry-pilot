"""
Curated catalog for demo-mode Swiggy MCP (SWIGGY_MCP_MODE=demo).

Real Swiggy MCP has been returning 403 on get_orders/get_addresses since
2026-07-26 — root cause unconfirmed, but a scripted demo recording can't
depend on a live third-party API being up on the day of the shoot anyway.
This module is the frozen "catalog" the demo runs against: same wire shape
Swiggy actually returns (captured in tests/integration/conftest.py), so
DemoSwiggyMCPClient can feed it straight into the same _parse_product /
_parse_cart / _parse_go_to_item logic the real client uses — no parsing
code is duplicated or bypassed.

Deliberately spans staples, dairy, protein, produce, snacks, beverages,
personal care, cleaning, and bakery — enough category diversity for the
Pantry screen, and a deliberate mix of labelled packaged goods vs. unbranded
produce so nutrition resolution naturally produces a spread of confidence
tiers (Label/Verified vs. AI-estimated) rather than everything landing in
one bucket.
"""

import re

DEMO_ADDRESS = {
    "id":              "demo_addr_home_001",
    "addressLine":     "402, Willow Residency, 12th Main, Indiranagar, Bengaluru",
    "addressTag":      "Home",
    "addressCategory": "home",
    "phoneNumber":     None,
    "lat":             12.9716,
    "lng":             77.6412,
}

# ── Catalog ─────────────────────────────────────────────────────────────────
# (product_id, name, brand, category, quantity, mrp, offer_price, keywords)
#
# `category` here is deliberately one of pantry_service.py's real 5 buckets
# (staples | fresh_produce | dairy | packaged | grocery — see
# _normalise_category / _REORDER_THRESHOLDS) rather than a finer-grained
# label. Search keywords still carry the specific concept (e.g. "protein",
# "iron") independent of this bucket — this field only drives Pantry-page
# grouping and reorder thresholds, and using an ad-hoc taxonomy here just
# meant everything fell through to the "grocery" catch-all on the actual
# Pantry screen instead of landing in a real bucket.
_CATALOG_RAW = [
    # staples
    ("dp_rice_001",    "India Gate Basmati Rice",   "India Gate",  "staples", "5 kg",   520, 480, ["rice", "basmati"]),
    ("dp_atta_002",    "Aashirvaad Whole Wheat Atta", "Aashirvaad", "staples", "5 kg",   290, 270, ["atta", "wheat flour", "flour"]),
    ("dp_toordal_003", "Tata Sampann Toor Dal",     "Tata Sampann", "staples", "1 kg",   185, 172, ["toor dal", "dal", "lentils", "protein"]),
    ("dp_moongdal_004","Tata Sampann Moong Dal",    "Tata Sampann", "staples", "1 kg",   165, 155, ["moong dal", "dal", "lentils", "protein"]),
    ("dp_oil_005",     "Fortune Sunflower Oil",     "Fortune",     "staples", "1 L",    165, 152, ["oil", "cooking oil", "sunflower oil"]),
    ("dp_salt_006",    "Tata Salt",                 "Tata",        "staples", "1 kg",    28,  28, ["salt"]),
    ("dp_sugar_007",   "Madhur Sugar",              "Madhur",      "staples", "1 kg",    52,  48, ["sugar"]),
    ("dp_besan_008",   "Rajdhani Besan",            "Rajdhani",    "staples", "1 kg",   110, 102, ["besan", "gram flour", "protein"]),

    # dairy (incl. eggs — no separate protein bucket exists in pantry_service)
    ("dp_milk_010",    "Amul Toned Milk",           "Amul",        "dairy", "1 L",       68,  64, ["milk", "dairy"]),
    ("dp_curd_011",    "Mother Dairy Curd",         "Mother Dairy","dairy", "400 g",     45,  42, ["curd", "yogurt", "dahi"]),
    ("dp_paneer_012",  "Amul Malai Paneer",         "Amul",        "dairy", "200 g",     95,  90, ["paneer", "protein", "cottage cheese"]),
    ("dp_butter_013",  "Amul Butter",               "Amul",        "dairy", "500 g",    260, 245, ["butter"]),
    ("dp_cheese_014",  "Amul Cheese Slices",        "Amul",        "dairy", "200 g",    130, 120, ["cheese"]),
    ("dp_eggs_015",    "Farm Fresh Eggs",           None,          "dairy", "6 pcs",    54,  48, ["eggs", "egg", "protein"]),

    # non-dairy protein — no matching bucket, falls to grocery (matches app design)
    ("dp_soya_016",    "Nutrela Soya Chunks",       "Nutrela",     "grocery", "200 g",    45,  40, ["soya chunks", "soya", "protein", "meal maker"]),
    ("dp_chicken_017", "Licious Chicken Breast",    "Licious",     "grocery", "500 g",   240, 219, ["chicken", "chicken breast", "protein", "meat"]),
    ("dp_tofu_018",    "Epigamia Tofu",             "Epigamia",    "grocery", "200 g",   110, 99,  ["tofu", "protein"]),

    # fruits & vegetables (mostly unbranded — drives AI-estimated confidence tier)
    ("dp_tomato_020",  "Tomato",                    None, "fresh_produce", "1 kg",    45,  38, ["tomato", "tomatoes"]),
    ("dp_onion_021",   "Onion",                     None, "fresh_produce", "1 kg",    42,  36, ["onion", "onions"]),
    ("dp_potato_022",  "Potato",                    None, "fresh_produce", "1 kg",    38,  32, ["potato", "potatoes"]),
    ("dp_spinach_023", "Spinach (Palak)",           None, "fresh_produce", "250 g",   28,  24, ["spinach", "palak", "leafy greens", "iron"]),
    ("dp_carrot_024",  "Carrot",                    None, "fresh_produce", "500 g",   32,  28, ["carrot", "carrots"]),
    ("dp_banana_025",  "Banana",                    None, "fresh_produce", "1 dozen",     60,  54, ["banana", "bananas", "fruit"]),
    ("dp_apple_026",   "Apple (Shimla)",             None, "fresh_produce", "1 kg",       180, 165, ["apple", "apples", "fruit"]),
    ("dp_orange_027",  "Orange",                    None, "fresh_produce", "1 kg",        90,  82, ["orange", "oranges", "fruit", "vitamin c"]),
    ("dp_pomegranate_028", "Pomegranate",           None, "fresh_produce", "500 g",       85,  78, ["pomegranate", "fruit", "iron"]),

    # snacks / bakery / beverages — all map to "packaged"
    ("dp_biscuits_030","Britannia Marie Gold",      "Britannia",   "packaged", "250 g",     35,  32, ["biscuits", "marie", "snack"]),
    ("dp_namkeen_031", "Haldiram's Aloo Bhujia",     "Haldiram's",  "packaged", "200 g",     55,  50, ["namkeen", "bhujia", "snack"]),
    ("dp_chips_032",   "Lay's Classic Salted",      "Lay's",       "packaged", "52 g",      20,  20, ["chips", "lays", "snack"]),
    ("dp_bread_033",   "Britannia Brown Bread",     "Britannia",   "packaged", "400 g",     55,  50, ["bread", "brown bread"]),
    ("dp_muesli_034",  "Kellogg's Muesli",          "Kellogg's",   "packaged", "500 g",    345, 320, ["muesli", "cereal", "breakfast", "fiber"]),
    ("dp_oats_035",    "Saffola Oats",              "Saffola",     "packaged", "1 kg",     165, 150, ["oats", "fiber", "breakfast"]),
    ("dp_dates_036",   "Medjoul Dates",             None, "packaged", "250 g",       210, 195, ["dates", "iron", "snack"]),
    ("dp_almonds_037", "Whole Almonds",             None, "packaged", "250 g",       280, 260, ["almonds", "nuts", "protein"]),
    ("dp_tea_040",     "Tata Tea Premium",          "Tata",        "packaged", "500 g",  240, 225, ["tea"]),
    ("dp_coffee_041",  "Nescafe Classic",           "Nescafe",     "packaged", "100 g",  340, 315, ["coffee"]),
    ("dp_juice_042",   "Real Mixed Fruit Juice",    "Real",        "packaged", "1 L",    120, 108, ["juice"]),

    # personal care / cleaning — no matching bucket, falls to grocery
    ("dp_soap_050",    "Dove Soap",                 "Dove",        "grocery", "125 g", 65, 58, ["soap", "bathing soap"]),
    ("dp_shampoo_051", "Dove Shampoo",               "Dove",        "grocery", "340 ml", 280, 255, ["shampoo"]),
    ("dp_toothpaste_052", "Colgate Strong Teeth",   "Colgate",     "grocery", "200 g", 105, 95, ["toothpaste"]),
    ("dp_detergent_053", "Surf Excel Detergent",    "Surf Excel",  "grocery", "1 kg",   210, 195, ["detergent", "washing powder"]),
    ("dp_dishwash_054", "Vim Dishwash Bar",         "Vim",         "grocery", "200 g",   20,  18, ["dishwash", "vim", "dish soap"]),
    ("dp_handwash_055", "Dettol Handwash",          "Dettol",      "grocery", "250 ml", 99, 89, ["handwash", "hand sanitizer", "dettol"]),
]

CATALOG = [
    {
        "productId":   pid,
        "displayName": name,
        "brand":       brand,
        "category":    category,
        "inStock":     True,
        "keywords":    keywords,
        "variations": [{
            "spinId":                pid,
            "skuId":                 pid,
            "displayName":           f"{name} {qty}",
            "brandName":             brand,
            "quantityDescription":   qty,
            "isInStockAndAvailable": True,
            "price":                 {"mrp": mrp, "offerPrice": offer},
            "imageUrl":              None,
        }],
    }
    for pid, name, brand, category, qty, mrp, offer, keywords in _CATALOG_RAW
]

# Generic descriptors an LLM might suggest that don't literally appear in any
# item's name — mapped to a concrete search term so planning doesn't come up
# empty on a reasonable-sounding but non-literal query.
_SYNONYMS = {
    "protein source": "paneer", "protein-rich": "paneer", "lean protein": "chicken",
    "leafy greens": "spinach", "iron rich": "spinach", "iron-rich": "spinach",
    "fresh fruit": "banana", "fruit": "banana", "seasonal fruit": "apple",
    "cooking oil": "oil", "dairy": "milk", "whole grain": "oats",
    "healthy snack": "almonds", "breakfast cereal": "muesli",
    "vegetable": "tomato", "greens": "spinach",
}

_DEFAULT_FALLBACK = ["dp_rice_001", "dp_milk_010", "dp_tomato_020", "dp_banana_025"]


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def search_catalog(query: str, limit: int = 10) -> list[dict]:
    """Keyword search over CATALOG. Never returns empty for a non-empty query —
    falls back to a small set of popular staples, mirroring how real Swiggy
    fuzzy search rarely comes back truly empty."""
    q = query.strip().lower()
    if not q:
        return []

    q = _SYNONYMS.get(q, q)
    q_tokens = set(_tokens(q))

    scored = []
    for item in CATALOG:
        haystack = " ".join(filter(None, [
            item["displayName"], item["brand"] or "", item["category"] or "",
            " ".join(item["keywords"]),
        ])).lower()
        score = 0
        if q in haystack:
            score += 10
        item_tokens = set(_tokens(haystack))
        score += 3 * len(q_tokens & item_tokens)
        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda x: -x[0])
    results = [item for _, item in scored[:limit]]

    if not results:
        results = [i for i in CATALOG if i["productId"] in _DEFAULT_FALLBACK][:limit]

    return results


def go_to_items(n: int = 8) -> list[dict]:
    """Bootstrap list for onboarding / empty-pantry Flow first-run — the demo-
    mode equivalent of real Swiggy's your_go_to_items for a brand-new user."""
    ids = ["dp_rice_001", "dp_atta_002", "dp_toordal_003", "dp_milk_010",
           "dp_curd_011", "dp_tomato_020", "dp_onion_021", "dp_oil_005"]
    by_id = {i["productId"]: i for i in CATALOG}
    return [by_id[i] for i in ids[:n] if i in by_id]
