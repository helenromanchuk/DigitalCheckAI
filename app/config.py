"""DigitalCheckAI configuration."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("COSTCO_DB_PATH", str(DATA_DIR / "costco.db")))
# Level for app.* loggers; DEBUG also logs every Gemini prompt and response
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
IMAGES_DIR = DATA_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

FUEL_CATEGORY = "Auto & fuel (Gas Station)"
ALCOHOL_CATEGORY = "Alcohol"
OTHER_CATEGORY = "Other"

# Single source of truth for default categories: (canonical name stored in the DB and sent to Gemini,
# Russian UI label, classifier scope). Gemini only ever sees the English names
_DEFAULT_CATEGORIES_SPEC: list[tuple[str, str, str]] = [
    (
        "Meat, poultry & seafood",
        "Мясо, птица и морепродукты",
        "fresh/chilled raw meat, poultry, fish, seafood, bacon, sausages & links "
        "(Aidells, Kiolbassa, linguica, franks), salami, prosciutto, charcuterie, and packaged sliced deli meats "
        "(sliced turkey, ham, roast beef).",
    ),
    (
        "Fruits, vegetables & berries",
        "Овощи, фрукты и ягоды",
        "fresh produce, fruits, berries, vegetables, fresh herbs, salad mixes & kits, kimchi, living potted herbs.",
    ),
    (
        "Dairy, cheese & eggs",
        "Молочные продукты, сыр и яйца",
        "milk, cream, butter, sour cream, yogurt, cultured/probiotic dairy drinks (Cool Plus, kefir), "
        "all cheeses, eggs.",
    ),
    (
        "Bakery, bread & prepared food (Deli)",
        "Выпечка, хлеб и готовая еда (Deli)",
        "bread, buns, baguettes, croissants, tortillas, naan, pita, cakes, pastries, crepes/pancakes, "
        "and fresh prepared Costco Service Deli / ready-to-eat chilled meals (rotisserie chicken, sushi, poke, "
        "refrigerated ravioli/pasta, prepared entrées in sauce).",
    ),
    (
        "Pantry, oils, spices & canned goods",
        "Бакалея, масла, специи и консервы",
        "olive & cooking oils, vinegar, grains, rice, oats/granola/cereal, flour, sugar, yeast, dry pasta & "
        "instant noodle bowls, sauces, mayonnaise, marinades, spices/seasonings/rubs, honey, jams/fruit spreads, "
        "olives, sauerkraut, canned goods.",
    ),
    (
        "Frozen & ready meals",
        "Заморозка и полуфабрикаты",
        "frozen foods — dumplings/potstickers/mandu, frozen burgers/patties, frozen meatballs, frozen snacks/"
        "appetizers (flautas, spanakopita, takoyaki, pizza), frozen breaded or portioned fish/seafood "
        "(frozen Mahi-Mahi, frozen seafood mix, tortilla-crusted tilapia), frozen vegetables/berries/fruit, ice cream.",
    ),
    (
        "Snacks, sweets, nuts & drinks",
        "Снеки, сладости, орехи и напитки",
        "chocolate, candy, cookies, crackers, chips, nuts, trail mix, dried & freeze-dried fruit, "
        "protein/granola bars, non-alcoholic beverages (water, juice, soda, kombucha, energy drinks), "
        "and coffee (whole bean, ground, K-Cup pods) & tea.",
    ),
    (
        "Household, paper & cleaning",
        "Бытовая химия, бумага и уборка",
        "laundry detergent, dish soap/pods, surface/oven/shoe cleaners, bath tissue/toilet paper, paper towels, "
        "facial tissues, trash bags, disposable food storage bags (Ziploc), foil, parchment.",
    ),
    (
        "Health, pharmacy, vitamins & personal care",
        "Здоровье, аптека, витамины и гигиена",
        "vitamins, supplements, OTC medications, toothpaste, mouthwash, dental floss, razors/cartridges, deodorant, "
        "feminine care (pads, liners), shampoo, conditioner, body wash, skincare/creams/lotions, toner pads, "
        "sunscreen, cosmetics, massagers.",
    ),
    (
        "Home, kitchen & garden",
        "Товары для дома, кухни и сада",
        "cookware, reusable food storage containers & lunch boxes (Pyrex, Bentgo), cabinet/cosmetic organizers, "
        "storage drawers, hangers, home textiles, gift wrap & ribbon, garden planters & decorative floral "
        "arrangements, folding carts/hand trucks, multi-tools & hardware.",
    ),
    (
        "Clothing & shoes",
        "Одежда и обувь",
        "men's, women's, and kids' apparel, underwear, socks, swimwear, shoes, gloves, hats/beanies, "
        "sunglasses/eyewear, bags & backpacks.",
    ),
    (
        "Electronics & appliances",
        "Электроника и техника",
        "electronics, headphones, appliances, portable fans, chargers, cables, and batteries "
        "(Duracell AA/AAA, rechargeable).",
    ),
    (
        FUEL_CATEGORY,
        "Авто и заправка (Gas Station)",
        "gasoline, diesel, motor oil, automotive supplies.",
    ),
    (
        ALCOHOL_CATEGORY,
        "Алкоголь",
        "wine, prosecco, champagne, beer, spirits, sangria.",
    ),
    (
        OTHER_CATEGORY,
        "Прочее",
        "bottle deposits (`CA REDEMP VAL` / CRV), liquor liter taxes (`LIQUOR LITER TAX`), standalone store coupons/"
        "adjustments (`CPN`, `/123456`), gift cards, and items (such as toys/`LEGO` when no kids/toys category is "
        "in `Allowed categories`) that do not fit any other allowed category.",
    ),
]

DEFAULT_CATEGORIES: list[str] = [name for name, _, _ in _DEFAULT_CATEGORIES_SPEC]
DEFAULT_CATEGORY_LABELS_RU: dict[str, str] = {name: ru for name, ru, _ in _DEFAULT_CATEGORIES_SPEC}
DEFAULT_CATEGORY_SCOPES: dict[str, str] = {name: scope for name, _, scope in _DEFAULT_CATEGORIES_SPEC}
def merge_category_labels_ru(custom_labels_ru: dict[str, str] | None = None) -> dict[str, str]:
    """Combines custom and default Russian category labels, with defaults taking precedence."""
    return {**(custom_labels_ru or {}), **DEFAULT_CATEGORY_LABELS_RU}


def default_category_lookup() -> dict[str, str]:
    """Maps each default category's casefolded English name and Russian label to its English name."""
    lookup: dict[str, str] = {}
    for name, name_ru in DEFAULT_CATEGORY_LABELS_RU.items():
        lookup[name.casefold()] = name
        lookup[name_ru.casefold()] = name
    return lookup


_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def is_english_category_name(name: str) -> bool:
    """Category names are stored and sent to Gemini in English; Russian belongs in the UI label."""
    return bool(name.strip()) and not _CYRILLIC_RE.search(name)


def get_gemini_api_key() -> str:
    """Returns the current GEMINI_API_KEY from the environment or the .env file."""
    load_dotenv(ENV_PATH, override=True)
    return os.getenv("GEMINI_API_KEY", "").strip()


def save_gemini_api_key(api_key: str) -> None:
    """Saves GEMINI_API_KEY to the local .env file and updates the environment."""
    clean_key = api_key.strip()
    os.environ["GEMINI_API_KEY"] = clean_key

    lines: list[str] = []
    found = False
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                lines.append(f"GEMINI_API_KEY={clean_key}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"GEMINI_API_KEY={clean_key}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_gemini_model() -> str:
    """Returns the Gemini model name used for categorization."""
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
