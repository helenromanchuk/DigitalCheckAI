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
    item_number: str = Field(description="Артикул товара Costco (Item #)")
    clean_name: str = Field(
        description="Полное расшифрованное и понятное название товара (например, 'Kirkland Signature Organic Extra Virgin Olive Oil (2L)')"
    )
    category: str = Field(description="Точное название категории из предоставленного списка")


class CategorizedBatchSchema(BaseModel):
    items: list[CategorizedItemSchema]


# Local fallback dictionary used until GEMINI_API_KEY is set
FALLBACK_RULES: list[tuple[list[str], str, dict[str, str]]] = [
    (
        ["UNLEADED", "GASOLINE", "FUEL", "DIESEL"],
        FUEL_CATEGORY,
        {"UNLEADED GASOLINE 87": "Бензин Regular Unleaded 87 (Costco Gas)"},
    ),
    (
        ["RIBEYE", "SALMON", "BEEF", "PORK", "CHICKEN", "SHRIMP", "STEAK", "LAMB", "TURKEY", "BACON"],
        "Мясо, птица и морепродукты",
        {
            "USDA CHOICE RIBEYE": "Стейки говяжьи USDA Choice Ribeye",
            "WILD ALASKAN SALMON": "Дикий аляскинский лосось (филе)",
        },
    ),
    (
        ["ROTISSERIE", "CROISSANT", "BAGEL", "MUFFIN", "BREAD", "DELI", "PIZZA", "CAKE"],
        "Выпечка, хлеб и готовая еда (Deli)",
        {
            "ROTISSERIE CHKN": "Курица-гриль Kirkland Rotisserie Chicken",
            "CROISSANTS 12CT": "Круассаны свежей выпечки Costco (12 шт.)",
        },
    ),
    (
        ["STRAWBERR", "AVOCADO", "BANANA", "APPLE", "SPINACH", "SALAD", "TOMATO", "BLUEBERR", "GRAPE", "PRODUCE"],
        "Овощи, фрукты и ягоды",
        {
            "ORG STRAWBERRIES": "Органическая клубника",
            "ORG AVOCADOS 6CT": "Органические авокадо (6 шт.)",
        },
    ),
    (
        ["EGGS", "BUTTER", "MILK", "CHEESE", "YOGURT", "CREAM"],
        "Молочные продукты, сыр и яйца",
        {
            "KS ORG EGGS 24CT": "Органические яйца Kirkland Signature (24 шт.)",
            "KERRYGOLD BUTTER": "Сливочное масло Kerrygold Pure Irish Butter",
        },
    ),
    (
        ["EVOO", "OLIVE OIL", "RICE", "PASTA", "COFFEE", "SAUCE", "HONEY", "SPICE", "FLOUR"],
        "Бакалея, масла, специи и консервы",
        {
            "KS ORG EVOO 2L BOTTLE": "Оливковое масло первого отжима Kirkland Organic EVOO (2 л)",
            "KS ORG EVOO": "Оливковое масло первого отжима Kirkland Organic EVOO",
        },
    ),
    (
        ["NUTS", "CHOC", "CHIPS", "COOKIE", "SNACK", "WATER", "SODA", "JUICE"],
        "Снеки, сладости, орехи и напитки",
        {
            "KS MIXED NUTS 40OZ": "Смесь орехов Kirkland Signature Mixed Nuts (1.13 кг)",
        },
    ),
    (
        ["CHARMIN", "BATH TISSUE", "TIDE", "BOUNTY", "CASCADE", "DETERGENT", "TRASH BAG", "PAPER TOWEL"],
        "Бытовая химия, бумага и уборка",
        {
            "CHARMIN ULTRA SOFT": "Туалетная бумага Charmin Ultra Soft",
            "KS BATH TISSUE": "Туалетная бумага Kirkland Signature Bath Tissue",
            "TIDE PODS 152CT": "Капсулы для стирки Tide PODS (152 шт.)",
        },
    ),
    (
        ["COQ10", "VITAMIN", "ADVIL", "TYLENOL", "SHAMPOO", "TOOTHPASTE", "LOTION", "ALLERGY", "SUNSCREEN"],
        "Здоровье, аптека, витамины и гигиена",
        {
            "COQ10 300MG 100CT": "Коэнзим Q10 Kirkland Signature 300 мг (100 капсул)",
        },
    ),
    (
        ["PROSECCO", "WINE", "VODKA", "BEER", "WHISKEY", "TEQUILA", "CHAMPAGNE"],
        "Алкоголь",
        {
            "KIRKLAND PROSECCO": "Игристое вино Kirkland Signature Asolo Prosecco Superiore DOCG",
        },
    ),
    (
        ["AIRPODS", "IPAD", "MACBOOK", "TV", "SAMSUNG", "SONY", "USB", "BATTERY", "CHARGER"],
        "Электроника и техника",
        {
            "APPLE AIRPODS PRO 2ND GEN USB-C": "Наушники Apple AirPods Pro 2 (USB-C)",
        },
    ),
    (
        ["JEANS", "LEVIS", "SHIRT", "JACKET", "SOCKS", "SHOES", "PANTS", "HOODIE"],
        "Одежда и обувь",
        {
            "LEVIS MENS JEANS": "Мужские джинсы Levi's",
        },
    ),
    (
        ["PYREX", "STORAGE", "PAN", "TOWEL", "PILLOW", "BLANKET", "BLENDER"],
        "Товары для дома, кухни и сада",
        {
            "PYREX GLASS STORAGE": "Набор стеклянных контейнеров для хранения Pyrex",
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

        prompt = (
            "Ты — эксперт по товарам и чекам сети магазинов Costco (США).\n"
            "В чеках Costco названия товаров сильно сокращены (например, 'KS' = 'Kirkland Signature', "
            "'ORG' = 'Organic', 'EVOO' = 'Extra Virgin Olive Oil', 'CHKN' = 'Chicken').\n\n"
            "Для каждого товара из списка:\n"
            "1. Расшифруй сокращённое чековое описание `raw_description` (с учётом артикула `item_number` и цены) "
            "в понятное полное название товара `clean_name`.\n"
            "2. Назначь наиболее подходящую категорию `category` СТРОГО из следующего списка разрешённых категорий:\n"
            f"{json.dumps(categories, ensure_ascii=False, indent=2)}\n\n"
            "Список товаров для расшифровки и категоризации:\n"
            f"{json.dumps(items_payload, ensure_ascii=False, indent=2)}"
        )

        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
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
