"""Decodes Costco Item #s and categorizes purchases with the Gemini API, caching results in SQLite."""
import json
import logging
import re
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from app.config import (
    DEFAULT_CATEGORY_LABELS_EN,
    DEFAULT_CATEGORY_SCOPES,
    FUEL_CATEGORY,
    get_gemini_api_key,
    get_gemini_model,
    merge_category_labels_en,
)
from app.database import Database

logger = logging.getLogger(__name__)

GEMINI_TIMEOUT_MS = 120_000


class CategorizedItemSchema(BaseModel):
    id: int = Field(
        description="Exact integer id of the item from the input list"
    )
    item_number: str = Field(
        description=(
            "Exact item_number of this item from the input list without modifications "
            "(keep prefixes like 'SWY-' and leading zeros)"
        )
    )
    clean_name: str = Field(
        description=(
            "Full decoded product name in English with the consumer package size/weight/count in parentheses "
            "at the end: use 'lb' or 'oz' for solid weight (multi-packs as 'N x M oz' or 'N x M lb'), and "
            "'fl oz', 'ml', 'L', or 'gal' for liquids (e.g. 'Kirkland Signature Organic Extra Virgin Olive Oil (2 L)', "
            "'Pistachio Chocolate Bar (6 x 2.6 oz)', 'Caliwater Cactus Water Variety Pack (6 x 16.9 fl oz)')"
        )
    )
    category: str = Field(
        description="Exact category value selected verbatim from the provided Allowed categories list"
    )


class CategorizedBatchSchema(BaseModel):
    items: list[CategorizedItemSchema]


_CATEGORY_RULES_TEXT = "\n".join(
    f"- **{en_label}**: {DEFAULT_CATEGORY_SCOPES.get(cat_key, '')}"
    for cat_key, en_label in DEFAULT_CATEGORY_LABELS_EN.items()
)

SYSTEM_INSTRUCTION = (
    "You are an expert on US retail products, item numbers, and receipt descriptions from Costco "
    "(in-warehouse receipts and online orders) and Safeway (item numbers prefixed with 'SWY-').\n"
    "Your task is to decode abbreviated receipt descriptions (`raw_description`) into clean, accurate "
    "English product names (`clean_name`) and classify each item strictly using the provided `Allowed categories`.\n\n"
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
    "`T6H6`, `10T4H`, `C12T6H4` (Tie/High/Case), `P=504`, `P120`, `P360/P300`, `FP576`, `HP288`, `LYR192` "
    "(pallet count), `SL28`, `SL 18 DAYS` (shelf life), `DOM600`, `COO:CN`, `RW` (random weight), "
    "`FY25`/`FY26`, `MVM`/`MVMP800`, `CSPC#`, `PLU`.\n"
    "- **Distinguish Master Case counts from consumer packages**:\n"
    "  - Patterns like `12/750ML` (wine), `16/2LB` (cheese), `12/46 OZ` (meatballs), or `8/50 OZ` (sauerkraut) at a "
    "normal retail `sample_unit_price` mean 1 consumer unit (`(750 ml)`, `(2 lb)`, `(46 oz)`, `(50 oz)`), where the "
    "leading number is the wholesale master case count. Never include the wholesale case count in `clean_name`.\n"
    "  - When a master case wraps a consumer multi-pack (e.g., `18/2-16OZ` or `12-2/24 OZ`), include only the "
    "consumer multi-pack: `(2 x 16 oz)`, `(2 x 24 oz)`.\n"
    "- **Standardized weight, volume, and count format (in parentheses at the end of `clean_name`)**:\n"
    "  - Solid items sold by weight: use singular `lb` or `oz` only (e.g., `(2 lb)`, `(12 oz)`). "
    "When both metric and US units appear (`908 G / 2 LB`), keep only the US unit `(2 lb)`.\n"
    "  - Solid multi-packs with per-unit weight: always format with `x` as `(N x M oz)` or `(N x M lb)` "
    "(e.g., `6/2.6 OZ` -> `(6 x 2.6 oz)`, `4-8OZ` -> `(4 x 8 oz)`, `20CT/1.13Z` -> `(20 x 1.13 oz)`). "
    "Do not use comma formatting such as `(20 ct, 1.13 oz)`.\n"
    "  - **Liquids (beverages, water, juice, kombucha, milk, oils, sauces, liquid laundry detergent, shampoo, "
    "body wash, lotion, mouthwash)**: always write volume in **`fl oz`**, **`ml`**, **`L`**, or **`gal`** "
    "(e.g., `6/16.9 OZ` drink -> `(6 x 16.9 fl oz)`, `2/40.5 OZ` juice -> `(2 x 40.5 fl oz)`, "
    "`194 OZ` liquid detergent -> `(194 fl oz)`), even if the receipt abbreviates it as `OZ` or `Z` "
    "(downstream unit-price calculation uses `oz`/`lb` strictly for solid weight and ignores `fl oz`).\n"
    "  - Count-only items without weight: `(N ct)` or `(N-Pack)` (e.g., `(72 ct)`, `(3-Pack)`).\n"
    "- **No speculative slash-guessing (`/`)**:\n"
    "  - Never output slash-separated guesses (e.g., do NOT write `Brand A / Brand B`, `Men's / Women's`, or "
    "`Chocolate / Cheese`). Include a specific brand or flavor only when it is unambiguously supported by "
    "`raw_description` and `item_number`; otherwise write a clean, factual product name.\n\n"
    "### 2. Rules for Selecting `category`\n"
    "- Return the exact `category` value from `Allowed categories` in the user prompt.\n"
    "- **Custom user-defined categories take priority**: If `Allowed categories` includes custom categories beyond "
    f"the {len(DEFAULT_CATEGORY_LABELS_EN)} defaults (e.g., `Kids`, `Toys`, `Pets`, `Baby`), always prefer a "
    "matching custom category over broad defaults or `Other` (for example, `LEGO` or dolls go to a kids/toys "
    "category if present, and pet items go to a pets category if present).\n"
    "- Default category boundaries (shown by their `english_label`):\n"
    f"{_CATEGORY_RULES_TEXT}\n\n"
    "### 3. Output Completeness Requirements\n"
    "- Return an entry for **every** item in the input list in the exact same order.\n"
    "- Copy each item's integer `id` and `item_number` from the input list without modification."
)


@lru_cache(maxsize=16)
def _build_batch_schema_cached(categories: tuple[str, ...]) -> type[BaseModel]:
    category_desc = CategorizedItemSchema.model_fields["category"].description
    item_model = create_model(
        "DynamicCategorizedItemSchema",
        __base__=CategorizedItemSchema,
        category=(Literal[*categories], Field(description=category_desc)),
    )
    return create_model(
        "DynamicCategorizedBatchSchema",
        __base__=CategorizedBatchSchema,
        items=(list[item_model], ...),
    )


def _build_batch_schema(categories: list[str] | tuple[str, ...]) -> type[BaseModel]:
    """Builds (or returns cached) a batch response schema whose category field is restricted to allowed categories."""
    return _build_batch_schema_cached(tuple(categories))


def _build_classification_prompt(
    batch: list[dict[str, Any]],
    categories: list[str],
    category_labels_en: dict[str, str] | None = None,
) -> str:
    """Builds the per-batch user prompt with the allowed categories and items (with integer ids) to classify."""
    labels_en = merge_category_labels_en(category_labels_en)
    allowed_entries: list[str | dict[str, str]] = [
        {"category": c, "english_label": labels_en[c]} if c in labels_en and labels_en[c] != c else c
        for c in categories
    ]
    items_payload = [
        {
            "id": idx,
            "item_number": str(i["item_number"]),
            "raw_description": str(i.get("raw_description", "")),
            "sample_unit_price": i.get("sample_unit_price", 0.0),
        }
        for idx, i in enumerate(batch)
    ]
    return (
        "Allowed categories (set `category` to the exact `category` value):\n"
        f"{json.dumps(allowed_entries, ensure_ascii=False, indent=2)}\n\n"
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


_SWY_PREFIX_RE = re.compile(r"^SWY(?=[-\s_\d])[-\s_]*", re.IGNORECASE)


def _same_item_number(returned: str, expected: str, batch_inums: set[str]) -> bool:
    """Checks that the item_number returned for a batch id refers to that same item."""
    ret = returned.strip()
    exp = expected.strip()
    if ret == exp:
        return True
    if not ret or not exp or ret in batch_inums:
        return False
    exp_swy = bool(_SWY_PREFIX_RE.match(exp))
    ret_swy = bool(_SWY_PREFIX_RE.match(ret))
    if ret_swy and not exp_swy:
        return False
    exp_core = _SWY_PREFIX_RE.sub("", exp).strip().lstrip("0") or "0"
    ret_core = _SWY_PREFIX_RE.sub("", ret).strip().lstrip("0") or "0"
    return ret_core == exp_core


def _make_gemini_client(api_key: str) -> Any:
    """Creates a Gemini API client with a finite HTTP timeout."""
    from google import genai
    from google.genai import types

    # Without a timeout a hung Gemini request blocks sync forever; on error the batch goes to the fallback
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )


class GeminiItemClassifier:
    """
    Costco and Safeway item classifier.
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

        custom_labels_en: dict[str, str] | None = None
        client: Any = None
        total_saved = 0
        for start in range(0, len(items), self.batch_size):
            batch = items[start : start + self.batch_size]
            try:
                if custom_labels_en is None:
                    custom_labels_en = self.db.get_category_labels_en()
                if client is None:
                    client = _make_gemini_client(api_key)
                batch_results = self._call_gemini_batch(batch, categories, client, custom_labels_en)
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
        client: Any,
        custom_labels_en: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """Calls the Google GenAI SDK with a strict JSON schema for a batch of items."""
        from google.genai import types

        model_name = get_gemini_model()
        prompt = _build_classification_prompt(batch, categories, custom_labels_en)
        batch_schema = _build_batch_schema(categories)

        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=batch_schema,
                temperature=0.1,
            ),
        )

        parsed_data = json.loads(response.text)
        batch_inums = {str(i["item_number"]).strip() for i in batch}
        results_by_id: dict[int, dict[str, str]] = {}

        for entry in parsed_data.get("items", []):
            item_id = entry.get("id")
            if type(item_id) is not int or not (0 <= item_id < len(batch)):
                logger.warning("Gemini вернул id товара не из пакета: %r", item_id)
                continue
            src = batch[item_id]
            inum = str(src["item_number"])
            returned_inum = str(entry.get("item_number", ""))
            if not _same_item_number(returned_inum, inum, batch_inums):
                logger.warning(
                    "Gemini вернул несовпадающий артикул %r для id=%d (ожидался %r)",
                    returned_inum,
                    item_id,
                    inum,
                )
                continue
            raw_desc = str(src.get("raw_description", ""))
            raw_cat = str(entry.get("category", ""))
            if raw_cat in categories:
                cat = raw_cat
            elif raw_cat.strip() in categories:
                cat = raw_cat.strip()
            else:
                logger.warning("Gemini вернул неизвестную категорию %r для артикула %s", raw_cat, inum)
                cat = "Прочее"
            results_by_id[item_id] = {
                "item_number": inum,
                "raw_description": raw_desc or inum,
                "clean_name": str(entry.get("clean_name") or raw_desc or inum).strip(),
                "category": cat,
            }
        return list(results_by_id.values())
