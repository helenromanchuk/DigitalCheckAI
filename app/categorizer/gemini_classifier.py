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
        description="Точный артикул товара из входного списка без изменений (включая префиксы вроде 'SWY-' и ведущие нули)"
    )
    clean_name: str = Field(
        description=(
            "Полное расшифрованное название товара на английском языке с размером/весом/количеством потребительской "
            "упаковки в круглых скобках в конце: для твёрдых товаров в 'lb' или 'oz' (мультипаки как 'N x M oz' / 'N x M lb'), "
            "для жидкостей обязательно в 'fl oz', 'ml', 'L' или 'gal' (например, "
            "'Kirkland Signature Organic Extra Virgin Olive Oil (2 L)', 'Pistachio Chocolate Bar (6 x 2.6 oz)', "
            "'Caliwater Cactus Water Variety Pack (6 x 16.9 fl oz)')"
        )
    )
    category: str = Field(description="Точное название категории строго из предоставленного списка")


class CategorizedBatchSchema(BaseModel):
    items: list[CategorizedItemSchema]


SYSTEM_INSTRUCTION = (
    "Ты — эксперт по товарам, артикулам и кассовым чекам американских сетей Costco (склад, онлайн-заказы) "
    "и Safeway (артикулы с префиксом 'SWY-').\n"
    "Твоя задача — расшифровывать сокращённые чековые описания (raw_description) в чистые, точные названия товаров "
    "на английском языке (clean_name) и классифицировать их строго по заданному списку категорий."
)


def _build_classification_prompt(
    items_payload: list[dict[str, Any]],
    categories: list[str],
) -> str:
    """Builds the structured prompt for decoding and categorizing a batch of Costco/Safeway receipt items."""
    return (
        "Для каждого товара из входного JSON-списка выполни расшифровку `clean_name` и назначь `category`.\n\n"
        "### 1. Правила расшифровки `clean_name` (на английском языке)\n"
        "- **Аббревиатуры брендов и терминов**:\n"
        "  - Costco: `KS` = Kirkland Signature, `ORG`/`ORGNC`/`OG` = Organic, `EVOO` = Extra Virgin Olive Oil, "
        "`CHKN`/`CKN`/`CHX` = Chicken, `BNLS/SL` или `BS` = Boneless Skinless, `BRST` = Breast, `GRND`/`GB` = Ground Beef, "
        "`SSG` = Sausage, `VTY` = Variety Pack, `PK` = Pack, `CT` = ct, `#` = lb, `Z` = oz (или fl oz для жидкостей).\n"
        "  - Safeway (`item_number` начинается с `SWY-`): `O ORGNC` = O Organics, `SIGNATURE SELECT` = Signature SELECT, "
        "`OPEN NATURE` = Open Nature, `LUCERNE` = Lucerne. Если `raw_description` имеет вид `Item name not found (<DEPT>)`, "
        "сформируй понятное обобщённое название по отделу (например, `Bakery Department Baked Goods`).\n"
        "- **Удаляй складские и логистические коды Costco**:\n"
        "  - Полностью игнорируй паллетные, коробочные и складские метки из второй строки чека: "
        "`T6H6`, `10T4H`, `C12T6H4` (Tie/High/Case), `P=504`, `P120`, `P360/P300`, `FP576`, `HP288`, `LYR192` (количество на паллете), "
        "`SL28`, `SL 18 DAYS` (срок годности), `DOM600`, `COO:CN`, `RW` (random weight), `FY25`/`FY26`, `MVM`/`MVMP800`, `CSPC#`, `PLU`.\n"
        "- **Отличай оптовую коробку (Master Case) от потребительской упаковки**:\n"
        "  - Записи вида `12/750ML` (вино), `16/2LB` (сыр), `12/46 OZ` (фрикадельки), `8/50 OZ` (капуста) при обычной "
        "розничной цене `sample_unit_price` означают 1 потребительскую единицу (`(750 ml)`, `(2 lb)`, `(46 oz)`, `(50 oz)`), "
        "а первое число — размер транспортного короба для склада. Не включай размер оптового короба в `clean_name`.\n"
        "  - Если внутри оптового короба указан потребительский мультипак (например, `18/2-16OZ` или `12-2/24 OZ`), "
        "указывай только потребительский мультипак: `(2 x 16 oz)`, `(2 x 24 oz)`.\n"
        "- **Единый формат веса, объёма и количества (в круглых скобках в конце `clean_name`)**:\n"
        "  - Твёрдые товары по весу: используй только `lb` или `oz` в единственном числе (например, `(2 lb)`, `(12 oz)`). "
        "Если в чеке дублируются метрические и американские единицы (`908 G / 2 LB`), оставляй только американские `(2 lb)`.\n"
        "  - Мультипаки твёрдых товаров с известным весом единицы: записывай СТРОГО через `x` — `(N x M oz)` или `(N x M lb)` "
        "(например, `6/2.6 OZ` -> `(6 x 2.6 oz)`, `4-8OZ` -> `(4 x 8 oz)`, `20CT/1.13Z` -> `(20 x 1.13 oz)`), "
        "не используй запятую вроде `(20 ct, 1.13 oz)`.\n"
        "  - **Жидкости (напитки, вода, сок, комбуча, молоко, масла, соусы, жидкие средства для стирки, шампуни, гели, лосьоны, ополаскиватели)**: "
        "всегда пиши объём в **`fl oz`**, **`ml`**, **`L`** или **`gal`** (например, `6/16.9 OZ` для напитка -> `(6 x 16.9 fl oz)`, "
        "`2/40.5 OZ` для сока -> `(2 x 40.5 fl oz)`, `194 OZ` для геля для стирки -> `(194 fl oz)`), даже если в чеке написано `OZ` или `Z` "
        "(приложение считает цену за фунт/унцию веса только по `oz`/`lb` и игнорирует `fl oz`).\n"
        "  - Штучные товары без веса: `(N ct)` или `(N-Pack)` (например, `(72 ct)`, `(3-Pack)`).\n"
        "- **Не гадай через слеш (`/`)**:\n"
        "  - Не пиши двойные предположения через `/` (например, НЕ пиши `Brand A / Brand B`, `Men's / Women's`, `Chocolate / Cheese`). "
        "Указывай конкретный бренд или вкус только тогда, когда он однозначно следует из `raw_description` и `item_number`; "
        "иначе дай чистое точное название по факту описания.\n\n"
        "### 2. Правила выбора категории `category`\n"
        "Выбери ровно одну категорию СТРОГО из списка разрешённых категорий ниже, руководствуясь разграничениями:\n"
        "- `Мясо, птица и морепродукты`: свежее/охлаждённое мясо, птица, рыба, морепродукты, бекон, сосиски и колбаски "
        "(Aidells, Kiolbassa, linguica, franks), салями, прошутто, мясные деликатесные нарезки (sliced turkey/ham/roast beef).\n"
        "- `Выпечка, хлеб и готовая еда (Deli)`: хлеб, булочки, багеты, круассаны, тортильи, наан, пита, торты, пирожные, "
        "блинчики/крепы, а также охлаждённая готовая кулинария и отдел Costco Service Deli (курица-гриль rotisserie, суши, поке, "
        "охлаждённые равиоли/паста, готовые блюда в соусе).\n"
        "- `Заморозка и полуфабрикаты`: замороженные продукты — пельмени/дамплинги/манду/потстикеры, замороженные котлеты и бургеры, "
        "замороженные фрикадельки (meatballs), замороженные закуски (flautas, spanakopita, takoyaki, пицца), замороженная рыба и "
        "морепродукты в панировке или порциях (frozen Mahi-Mahi, frozen seafood mix, tortilla-crusted tilapia), замороженные овощи/ягоды/фрукты, мороженое.\n"
        "- `Овощи, фрукты и ягоды`: свежие овощи, фрукты, ягоды, свежая зелень, салатные миксы и наборы (salad kits), кимчи, живая зелень в горшочках (living basil/herbs).\n"
        "- `Молочные продукты, сыр и яйца`: молоко, сливки, сливочное масло, сметана, йогурты, кисломолочные/пробиотические напитки (Cool Plus, кефир), любые сыры, яйца.\n"
        "- `Бакалея, масла, специи и консервы`: оливковое и растительные масла, уксус, крупы, рис, овсянка/гранола/хлопья, мука, сахар, "
        "дрожжи, сухая паста и лапша (в т.ч. noodle bowls быстрого приготовления), соусы, майонез, маринады, специи и приправы, мёд, джемы, оливки, квашеная капуста, консервы.\n"
        "- `Снеки, сладости, орехи и напитки`: шоколад, конфеты, печенье, крекеры, чипсы, орехи, сухофрукты и сублимированные ягоды, "
        "протеиновые и злаковые батончики, безалкогольные напитки (вода, соки, газировка, комбуча, энергетики), а также кофе (в зёрнах, молотый, капсулы K-Cup) и чай.\n"
        "- `Бытовая химия, бумага и уборка`: средства для стирки, посуды и уборки, чистящие губки, туалетная бумага, бумажные полотенца, "
        "салфетки, мешки для мусора, одноразовые пакеты для хранения/заморозки (Ziploc), фольга и пергамент.\n"
        "- `Здоровье, аптека, витамины и гигиена`: витамины, БАДы, лекарства, зубная паста, ополаскиватели, зубные нити, бритвы и кассеты, "
        "дезодоранты, женская гигиена (pads, liners), шампуни, кондиционеры, гели для душа, кремы, лосьоны, патчи, солнцезащитные средства, косметика, массажёры.\n"
        "- `Товары для дома, кухни и сада`: посуда, контейнеры и ланч-боксы (Pyrex, Bentgo), органайзеры, ящики для хранения, вешалки, "
        "текстиль для дома, подарочная бумага и ленты, садовые кашпо и декоративные растения/цветы, хозяйственные тележки, мультитулы и инструменты.\n"
        "- `Электроника и техника`: электроника, наушники, техника, портативные вентиляторы, зарядные устройства, кабели, а также батарейки и аккумуляторы (Duracell AA/AAA).\n"
        "- `Одежда и обувь`: мужская, женская и детская одежда, бельё, носки, купальники, обувь, перчатки, шапки, солнцезащитные очки, сумки и рюкзаки.\n"
        "- `Алкоголь`: вино, просекко, шампанское, пиво, крепкий алкоголь, сангрия.\n"
        "- `Авто и заправка (Gas Station)`: автомобильное топливо, моторные масла, автотовары.\n"
        "- `Прочее`: залоговая стоимость тары (`CA REDEMP VAL` / CRV), алкогольные налоги (`LIQUOR LITER TAX`), отдельные строки скидок/купонов (`CPN`, `/123456`), "
        "детские игрушки и конструкторы (`LEGO`, куклы), подарочные карты и товары, не подходящие ни под одну другую категорию.\n\n"
        "### 3. Требования к полноте ответа\n"
        "- Верни результат для **каждого** товара из входного списка в том же порядке.\n"
        "- Поле `item_number` в ответе должно **посимвольно совпадать** с входным `item_number`.\n\n"
        "Разрешённые категории:\n"
        f"{json.dumps(categories, ensure_ascii=False, indent=2)}\n\n"
        "Список товаров для расшифровки и категоризации:\n"
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
