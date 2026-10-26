"""Сервис расшифровки Item# и категоризации покупок Costco с помощью Gemini API и кэшированием в SQLite."""
import json
import logging
from typing import Any
from pydantic import BaseModel, Field

from app.config import DEFAULT_CATEGORIES, get_gemini_api_key, get_gemini_model
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


# Локальный резервный словарь для работы до ввода GEMINI_API_KEY
FALLBACK_RULES: list[tuple[list[str], str, dict[str, str]]] = [
    (
        ["UNLEADED", "GASOLINE", "FUEL", "DIESEL"],
        "Авто и заправка (Gas Station)",
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
        ["COQ10", "VITAMIN", "ADVIL", "TYLENOL", "SHAMPOO", "TOOTHPASTE", "LOTION", "ALLERGY"],
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


def fallback_categorize_item(item_number: str, raw_description: str) -> dict[str, str]:
    """Локальная расшифровка и категоризация по словарю (используется только если нет GEMINI_API_KEY)."""
    upper = raw_description.upper().strip()
    for keywords, category, exact_map in FALLBACK_RULES:
        if upper in exact_map:
            return {
                "item_number": item_number,
                "raw_description": raw_description,
                "clean_name": exact_map[upper],
                "category": category,
            }
        for kw in keywords:
            if kw in upper:
                clean = exact_map.get(upper, raw_description.title())
                return {
                    "item_number": item_number,
                    "raw_description": raw_description,
                    "clean_name": clean,
                    "category": category,
                }
    return {
        "item_number": item_number,
        "raw_description": raw_description,
        "clean_name": raw_description.title(),
        "category": "Прочее",
    }


class GeminiItemClassifier:
    """
    Классификатор товаров Costco.
    При первом появлении Item# отправляет его в Gemini API для расшифровки и категоризации,
    сохраняя результат в локальную БД SQLite.
    """

    def __init__(self, db: Database, batch_size: int = 40) -> None:
        self.db = db
        self.batch_size = batch_size

    def classify_new_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Принимает список новых товаров (только тех, которых ещё не было в item_catalog,
        или ожидающих классификации), расшифровывает через Gemini API и сохраняет в БД.
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
        """Вызывает Google GenAI SDK со строгой JSON-схемой для пакета артикулов Costco."""
        from google import genai
        from google.genai import types

        # Без таймаута зависший запрос к Gemini блокирует синхронизацию бесконечно; ошибка уйдёт в fallback
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
        # Gemini может вернуть артикул с ведущими нулями или пробелами — сводим к исходному
        loose_map = {k.strip().lstrip("0"): k for k in raw_map}
        results: list[dict[str, str]] = []

        for entry in parsed_data.get("items", []):
            returned = str(entry.get("item_number", "")).strip()
            inum = returned if returned in raw_map else loose_map.get(returned.lstrip("0"))
            if not inum:
                # Чужой или выдуманный артикул не должен создавать/перезаписывать записи каталога;
                # товар из пакета останется pending и уйдёт в следующую классификацию
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
