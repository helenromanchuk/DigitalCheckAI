"""Decodes Costco Item #s and categorizes purchases with the Gemini API, caching results in SQLite."""
import json
import logging
import re
from typing import Any
from pydantic import BaseModel, Field

from app.config import DEFAULT_CATEGORIES, FUEL_CATEGORY, get_gemini_api_key, get_gemini_model
from app.database import Database

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT_MS = 120_000


class CategorizedItemSchema(BaseModel):
    item_number: str = Field(
        description="Exact item_number from the input list without modifications (keep prefixes like 'SWY-' and leading zeros)"
    )
    clean_name: str = Field(
        description=(
            "Full decoded product name in English with the consumer package size/weight/count in parentheses at the end: "
            "use 'lb' or 'oz' for solid weight (multi-packs as 'N x M oz' or 'N x M lb'), and 'fl oz', 'ml', 'L', or 'gal' "
            "for liquids (e.g. 'Kirkland Signature Organic Extra Virgin Olive Oil (2 L)', "
            "'Pistachio Chocolate Bar (6 x 2.6 oz)', 'Caliwater Cactus Water Variety Pack (6 x 16.9 fl oz)')"
        )
    )
    category: str = Field(
        description="Exact category string selected verbatim from the provided Allowed categories list"
    )


class CategorizedBatchSchema(BaseModel):
    items: list[CategorizedItemSchema]


SYSTEM_INSTRUCTION = (
    "You are an expert on US retail products, item numbers, and receipt descriptions from Costco "
    "(in-warehouse receipts and online orders) and Safeway (item numbers prefixed with 'SWY-').\n"
    "Your task is to decode abbreviated receipt descriptions (raw_description) into clean, accurate "
    "English product names (clean_name) and classify each item strictly using the provided list of allowed categories."
)


def _build_classification_prompt(
    items_payload: list[dict[str, Any]],
    categories: list[str],
) -> str:
    """Builds the structured prompt for decoding and categorizing a batch of Costco/Safeway receipt items."""
    return (
        "For every item in the input JSON list, decode `clean_name` and assign `category`.\n\n"
        "### 1. Rules for Decoding `clean_name` (in English)\n"
        "- **Brand & retail abbreviations**:\n"
        "  - Costco: `KS` = Kirkland Signature, `ORG`/`ORGNC`/`OG` = Organic, `EVOO` = Extra Virgin Olive Oil, "
        "`CHKN`/`CKN`/`CHX` = Chicken, `BNLS/SL` or `BS` = Boneless Skinless, `BRST` = Breast, `GRND`/`GB` = Ground Beef, "
        "`SSG` = Sausage, `VTY` = Variety Pack, `PK` = Pack, `CT` = ct, `#` = lb, `Z` = oz (or fl oz for liquids).\n"
        "  - Safeway (`item_number` starts with `SWY-`): `O ORGNC` = O Organics, `SIGNATURE SELECT` = Signature SELECT, "
        "`OPEN NATURE` = Open Nature, `LUCERNE` = Lucerne. If `raw_description` has the placeholder form "
        "`Item name not found (<DEPT>)`, produce a clear generic department name (e.g., `Bakery Department Baked Goods`).\n"
        "- **Strip Costco warehouse & pallet logistics codes**:\n"
        "  - Completely ignore pallet, case, shelf-life, and promo codes from the second receipt description line: "
        "`T6H6`, `10T4H`, `C12T6H4` (Tie/High/Case), `P=504`, `P120`, `P360/P300`, `FP576`, `HP288`, `LYR192` (pallet count), "
        "`SL28`, `SL 18 DAYS` (shelf life), `DOM600`, `COO:CN`, `RW` (random weight), `FY25`/`FY26`, `MVM`/`MVMP800`, `CSPC#`, `PLU`.\n"
        "- **Distinguish Master Case counts from consumer packages**:\n"
        "  - Patterns like `12/750ML` (wine), `16/2LB` (cheese), `12/46 OZ` (meatballs), or `8/50 OZ` (sauerkraut) at a normal "
        "retail `sample_unit_price` mean 1 consumer unit (`(750 ml)`, `(2 lb)`, `(46 oz)`, `(50 oz)`), where the leading number "
        "is the wholesale master case count. Never include the wholesale case count in `clean_name`.\n"
        "  - When a master case wraps a consumer multi-pack (e.g., `18/2-16OZ` or `12-2/24 OZ`), include only the consumer "
        "multi-pack: `(2 x 16 oz)`, `(2 x 24 oz)`.\n"
        "- **Standardized weight, volume, and count format (in parentheses at the end of `clean_name`)**:\n"
        "  - Solid items sold by weight: use singular `lb` or `oz` only (e.g., `(2 lb)`, `(12 oz)`). "
        "When both metric and US units appear (`908 G / 2 LB`), keep only the US unit `(2 lb)`.\n"
        "  - Solid multi-packs with per-unit weight: always format with `x` as `(N x M oz)` or `(N x M lb)` "
        "(e.g., `6/2.6 OZ` -> `(6 x 2.6 oz)`, `4-8OZ` -> `(4 x 8 oz)`, `20CT/1.13Z` -> `(20 x 1.13 oz)`). "
        "Do not use comma formatting such as `(20 ct, 1.13 oz)`.\n"
        "  - **Liquids (beverages, water, juice, kombucha, milk, oils, sauces, liquid laundry detergent, shampoo, body wash, lotion, mouthwash)**: "
        "always write volume in **`fl oz`**, **`ml`**, **`L`**, or **`gal`** (e.g., `6/16.9 OZ` drink -> `(6 x 16.9 fl oz)`, "
        "`2/40.5 OZ` juice -> `(2 x 40.5 fl oz)`, `194 OZ` liquid detergent -> `(194 fl oz)`), even if the receipt abbreviates it as `OZ` or `Z` "
        "(downstream unit-price calculation uses `oz`/`lb` strictly for solid weight and ignores `fl oz`).\n"
        "  - Count-only items without weight: `(N ct)` or `(N-Pack)` (e.g., `(72 ct)`, `(3-Pack)`).\n"
        "- **No speculative slash-guessing (`/`)**:\n"
        "  - Never output slash-separated guesses (e.g., do NOT write `Brand A / Brand B`, `Men's / Women's`, or `Chocolate / Cheese`). "
        "Include a specific brand or flavor only when it is unambiguously supported by `raw_description` and `item_number`; "
        "otherwise write a clean, factual product name.\n\n"
        "### 2. Rules for Selecting `category`\n"
        "Select exactly one category string **verbatim** from the `Allowed categories` JSON list below (do not translate or alter the category string), following these boundaries:\n"
        "- **Meat, poultry & seafood**: fresh/chilled raw meat, poultry, fish, seafood, bacon, sausages & links "
        "(Aidells, Kiolbassa, linguica, franks), salami, prosciutto, charcuterie, and packaged sliced deli meats (sliced turkey, ham, roast beef).\n"
        "- **Bakery, bread & prepared food (Deli)**: bread, buns, baguettes, croissants, tortillas, naan, pita, cakes, pastries, "
        "crepes/pancakes, and fresh prepared Costco Service Deli / ready-to-eat chilled meals (rotisserie chicken, sushi, poke, "
        "refrigerated ravioli/pasta, prepared entrées in sauce).\n"
        "- **Frozen & ready meals**: frozen foods — dumplings/potstickers/mandu, frozen burgers/patties, frozen meatballs, "
        "frozen snacks/appetizers (flautas, spanakopita, takoyaki, pizza), frozen breaded or portioned fish/seafood "
        "(frozen Mahi-Mahi, frozen seafood mix, tortilla-crusted tilapia), frozen vegetables/berries/fruit, ice cream.\n"
        "- **Fruits, vegetables & berries**: fresh produce, fruits, berries, vegetables, fresh herbs, salad mixes & kits, kimchi, living potted herbs.\n"
        "- **Dairy, cheese & eggs**: milk, cream, butter, sour cream, yogurt, cultured/probiotic dairy drinks (Cool Plus, kefir), all cheeses, eggs.\n"
        "- **Pantry, oils, spices & canned goods**: olive & cooking oils, vinegar, grains, rice, oats/granola/cereal, flour, sugar, "
        "yeast, dry pasta & instant noodle bowls, sauces, mayonnaise, marinades, spices/seasonings/rubs, honey, jams/fruit spreads, olives, sauerkraut, canned goods.\n"
        "- **Snacks, sweets, nuts & drinks**: chocolate, candy, cookies, crackers, chips, nuts, trail mix, dried & freeze-dried fruit, "
        "protein/granola bars, non-alcoholic beverages (water, juice, soda, kombucha, energy drinks), and coffee (whole bean, ground, K-Cup pods) & tea.\n"
        "- **Household, paper & cleaning**: laundry detergent, dish soap/pods, surface/oven/shoe cleaners, bath tissue/toilet paper, "
        "paper towels, facial tissues, trash bags, disposable food storage bags (Ziploc), foil, parchment.\n"
        "- **Health, pharmacy, vitamins & personal care**: vitamins, supplements, OTC medications, toothpaste, mouthwash, dental floss, "
        "razors/cartridges, deodorant, feminine care (pads, liners), shampoo, conditioner, body wash, skincare/creams/lotions, toner pads, sunscreen, cosmetics, massagers.\n"
        "- **Home, kitchen & garden**: cookware, reusable food storage containers & lunch boxes (Pyrex, Bentgo), cabinet/cosmetic organizers, "
        "storage drawers, hangers, home textiles, gift wrap & ribbon, garden planters & decorative floral arrangements, folding carts/hand trucks, multi-tools & hardware.\n"
        "- **Electronics & appliances**: electronics, headphones, appliances, portable fans, chargers, cables, and batteries (Duracell AA/AAA, rechargeable).\n"
        "- **Clothing & shoes**: men's, women's, and kids' apparel, underwear, socks, swimwear, shoes, gloves, hats/beanies, sunglasses/eyewear, bags & backpacks.\n"
        "- **Alcohol**: wine, prosecco, champagne, beer, spirits, sangria.\n"
        "- **Auto & fuel (Gas Station)**: gasoline, diesel, motor oil, automotive supplies.\n"
        "- **Other**: bottle deposits (`CA REDEMP VAL` / CRV), liquor liter taxes (`LIQUOR LITER TAX`), standalone store coupons/adjustments (`CPN`, `/123456`), "
        "toys & building sets (`LEGO`, dolls), gift cards, and items that do not fit any other category.\n\n"
        "### 3. Output Completeness Requirements\n"
        "- Return an entry for **every** item in the input list in the exact same order.\n"
        "- The `item_number` field in the output must match the input `item_number` character-for-character.\n\n"
        "Allowed categories:\n"
        f"{json.dumps(categories, ensure_ascii=False, indent=2)}\n\n"
        "Items to decode and categorize:\n"
        f"{json.dumps(items_payload, ensure_ascii=False, indent=2)}"
    )


# Local fallback dictionary used until GEMINI_API_KEY is set
FALLBACK_RULES: list[tuple[list[str], str, dict[str, str]]] = [
    (
        ["UNLEADED", "GASOLINE", "FUEL", "DIESEL"],
        FUEL_CATEGORY,
        {"UNLEADED GASOLINE 87": "Costco Regular Unleaded 87 Gasoline"},
    ),
    (
        ["RIBEYE", "SALMON", "BEEF", "PORK", "CHICKEN", "SHRIMP", "STEAK", "LAMB", "TURKEY", "BACON"],
        "Мясо, птица и морепродукты",
        {
            "USDA CHOICE RIBEYE": "USDA Choice Ribeye Steaks",
            "WILD ALASKAN SALMON": "Wild Alaskan Salmon Fillet",
        },
    ),
    (
        ["ROTISSERIE", "CROISSANT", "BAGEL", "MUFFIN", "BREAD", "DELI", "PIZZA", "CAKE"],
        "Выпечка, хлеб и готовая еда (Deli)",
        {
            "ROTISSERIE CHKN": "Kirkland Signature Rotisserie Chicken",
            "CROISSANTS 12CT": "Costco Bakery Croissants (12 ct)",
        },
    ),
    (
        ["STRAWBERR", "AVOCADO", "BANANA", "APPLE", "SPINACH", "SALAD", "TOMATO", "BLUEBERR", "GRAPE", "PRODUCE"],
        "Овощи, фрукты и ягоды",
        {
            "ORG STRAWBERRIES": "Organic Strawberries",
            "ORG AVOCADOS 6CT": "Organic Avocados (6 ct)",
        },
    ),
    (
        ["EGGS", "BUTTER", "MILK", "CHEESE", "YOGURT", "CREAM"],
        "Молочные продукты, сыр и яйца",
        {
            "KS ORG EGGS 24CT": "Kirkland Signature Organic Eggs (24 ct)",
            "KERRYGOLD BUTTER": "Kerrygold Pure Irish Butter",
        },
    ),
    (
        ["EVOO", "OLIVE OIL", "RICE", "PASTA", "COFFEE", "SAUCE", "HONEY", "SPICE", "FLOUR"],
        "Бакалея, масла, специи и консервы",
        {
            "KS ORG EVOO 2L BOTTLE": "Kirkland Signature Organic Extra Virgin Olive Oil (2 L)",
            "KS ORG EVOO": "Kirkland Signature Organic Extra Virgin Olive Oil",
        },
    ),
    (
        ["NUTS", "CHOC", "CHIPS", "COOKIE", "SNACK", "WATER", "SODA", "JUICE"],
        "Снеки, сладости, орехи и напитки",
        {
            "KS MIXED NUTS 40OZ": "Kirkland Signature Mixed Nuts (40 oz)",
        },
    ),
    (
        ["CHARMIN", "BATH TISSUE", "TIDE", "BOUNTY", "CASCADE", "DETERGENT", "TRASH BAG", "PAPER TOWEL"],
        "Бытовая химия, бумага и уборка",
        {
            "CHARMIN ULTRA SOFT": "Charmin Ultra Soft Toilet Paper",
            "KS BATH TISSUE": "Kirkland Signature Bath Tissue",
            "TIDE PODS 152CT": "Tide PODS Laundry Detergent (152 ct)",
        },
    ),
    (
        ["COQ10", "VITAMIN", "ADVIL", "TYLENOL", "SHAMPOO", "TOOTHPASTE", "LOTION", "ALLERGY", "SUNSCREEN"],
        "Здоровье, аптека, витамины и гигиена",
        {
            "COQ10 300MG 100CT": "Kirkland Signature CoQ10 300 mg (100 softgels)",
        },
    ),
    (
        ["PROSECCO", "WINE", "VODKA", "BEER", "WHISKEY", "TEQUILA", "CHAMPAGNE"],
        "Алкоголь",
        {
            "KIRKLAND PROSECCO": "Kirkland Signature Asolo Prosecco Superiore DOCG",
        },
    ),
    (
        ["AIRPODS", "IPAD", "MACBOOK", "TV", "SAMSUNG", "SONY", "USB", "BATTERY", "CHARGER"],
        "Электроника и техника",
        {
            "APPLE AIRPODS PRO 2ND GEN USB-C": "Apple AirPods Pro 2 (USB-C)",
        },
    ),
    (
        ["JEANS", "LEVIS", "SHIRT", "JACKET", "SOCKS", "SHOES", "PANTS", "HOODIE"],
        "Одежда и обувь",
        {
            "LEVIS MENS JEANS": "Levi's Men's Jeans",
        },
    ),
    (
        ["PYREX", "STORAGE", "PAN", "TOWEL", "PILLOW", "BLANKET", "BLENDER"],
        "Товары для дома, кухни и сада",
        {
            "PYREX GLASS STORAGE": "Pyrex Glass Food Storage Set",
        },
    ),
]


# A keyword at the end of a compound word ("PINEAPPLE", "CHEESECAKE") needs at least this many letters
# before it, so "RICE" doesn't match inside "PRICE"
MIN_COMPOUND_HEAD = 3


def _keyword_match_rank(keyword: str, words: list[str], text: str) -> int:
    """
    How well a keyword matches: 0 = no match, 1 = word prefix, 2 = whole word, phrase or end of a compound.
    Multi-word keywords match as a phrase; short ones (up to 3 letters, e.g. "TV", "PAN") only as a whole
    word or its plural ("PANS"); longer ones as a word prefix ("STRAWBERR" -> "STRAWBERRIES") or as the end
    of a compound word. In English compounds the last part names the thing ("CHEESECAKE" is a cake),
    so an ending match outranks a prefix match.
    """
    if " " in keyword:
        return 2 if re.search(rf"\b{re.escape(keyword)}", text) else 0
    if len(keyword) <= 3:
        return 2 if keyword in words or f"{keyword}S" in words else 0
    rank = 0
    for word in words:
        if word == keyword or (word.endswith(keyword) and len(word) - len(keyword) >= MIN_COMPOUND_HEAD):
            return 2
        if word.startswith(keyword):
            rank = 1
    return rank


def fallback_categorize_item(item_number: str, raw_description: str) -> dict[str, str]:
    """Local dictionary-based decoding and categorization (used only when GEMINI_API_KEY is missing)."""
    upper = raw_description.upper().strip()
    result = {
        "item_number": item_number,
        "raw_description": raw_description,
        "clean_name": raw_description.title(),
        "category": "Прочее",
    }

    # Exact names win over keywords from any rule (otherwise "APPLE AIRPODS..." ends up in produce)
    for _, category, exact_map in FALLBACK_RULES:
        if upper in exact_map:
            return {**result, "clean_name": exact_map[upper], "category": category}

    # Among keyword hits the best match wins (see _keyword_match_rank), then the longest keyword:
    # "AIRPODS" is more specific than "APPLE"
    words = re.findall(r"[A-Z0-9]+", upper)
    best: tuple[int, int] = (0, 0)
    for keywords, category, _ in FALLBACK_RULES:
        for kw in keywords:
            rank = _keyword_match_rank(kw, words, upper)
            if rank and (rank, len(kw)) > best:
                best = (rank, len(kw))
                result["category"] = category
    return result


def _loose_item_key(item_number: str) -> str:
    """Item number without spaces and leading zeros; "000" stays "0", an empty one stays empty."""
    trimmed = item_number.strip()
    return trimmed.lstrip("0") or ("0" if trimmed else "")


class GeminiItemClassifier:
    """
    Costco item classifier.
    The first time an Item # appears, sends it to the Gemini API for decoding and categorization
    and stores the result in the local SQLite database.
    """

    def __init__(self, db: Database, batch_size: int = 40) -> None:
        self.db = db
        self.batch_size = batch_size

    def classify_new_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Takes a list of new items (ones not yet in item_catalog, or still pending
        classification), decodes them with the Gemini API, and saves them to the database.
        """
        if not items:
            return {"classified_count": 0, "provider": "none"}

        api_key = get_gemini_api_key()
        categories = self.db.get_all_categories()

        if not api_key or api_key == "your_gemini_api_key_here":
            logger.info("GEMINI_API_KEY не задан — используется локальный резервный классификатор.")
            fallback_results = [
                fallback_categorize_item(str(i["item_number"]), str(i.get("raw_description", "")))
                for i in items
            ]
            saved = self.db.save_categorized_items(fallback_results, classified_by="fallback")
            return {"classified_count": saved, "provider": "fallback"}

        total_saved = 0
        for start in range(0, len(items), self.batch_size):
            batch = items[start : start + self.batch_size]
            try:
                batch_results = self._call_gemini_batch(batch, categories, api_key)
                saved = self.db.save_categorized_items(batch_results, classified_by="gemini")
                total_saved += saved
            except Exception as exc:
                logger.warning("Ошибка вызова Gemini API (%s), применяем fallback для пакета.", exc)
                fallback_results = [
                    fallback_categorize_item(str(i["item_number"]), str(i.get("raw_description", "")))
                    for i in batch
                ]
                saved = self.db.save_categorized_items(fallback_results, classified_by="fallback")
                total_saved += saved

        return {"classified_count": total_saved, "provider": "gemini"}

    def _call_gemini_batch(
        self,
        batch: list[dict[str, Any]],
        categories: list[str],
        api_key: str,
    ) -> list[dict[str, str]]:
        """Calls the Google GenAI SDK with a strict JSON schema for a batch of Costco item numbers."""
        from google import genai
        from google.genai import types

        # Without a timeout a hung Gemini request blocks sync forever; on error the batch goes to the fallback
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
        )
        model_name = get_gemini_model()

        items_payload = [
            {
                "item_number": str(i["item_number"]),
                "raw_description": str(i.get("raw_description", "")),
                "sample_unit_price": i.get("sample_unit_price", 0.0),
            }
            for i in batch
        ]

        prompt = _build_classification_prompt(items_payload, categories)

        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=CategorizedBatchSchema,
                temperature=0.1,
            ),
        )

        parsed_data = json.loads(response.text)
        raw_map = {str(i["item_number"]): str(i.get("raw_description", "")) for i in batch}
        # Gemini may return an item number with leading zeros or spaces; map it back to the original.
        # Only unambiguous keys: if "0123" and "123" are both in the batch, neither is matched loosely
        loose_candidates: dict[str, list[str]] = {}
        for k in raw_map:
            loose_candidates.setdefault(_loose_item_key(k), []).append(k)
        loose_map = {key: keys[0] for key, keys in loose_candidates.items() if key and len(keys) == 1}
        results: list[dict[str, str]] = []

        for entry in parsed_data.get("items", []):
            returned = str(entry.get("item_number", "")).strip()
            inum = returned if returned in raw_map else loose_map.get(_loose_item_key(returned))
            if not inum:
                # An unknown or made-up item number must not create or overwrite catalog rows;
                # the batch item stays pending and goes into the next classification run
                logger.warning("Gemini вернул артикул не из пакета: %r", returned)
                continue
            cat = str(entry.get("category", "Прочее")).strip()
            if cat not in categories:
                cat = "Прочее"
            results.append(
                {
                    "item_number": inum,
                    "raw_description": raw_map.get(inum, inum),
                    "clean_name": str(entry.get("clean_name") or raw_map.get(inum, inum)).strip(),
                    "category": cat,
                }
            )
        return results
