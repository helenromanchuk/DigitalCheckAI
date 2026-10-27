"""DigitalCheckAI configuration."""
import os
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

FUEL_CATEGORY = "Авто и заправка (Gas Station)"

# Single source of truth for default categories: (canonical DB name, English UI/prompt label, classifier scope)
_DEFAULT_CATEGORIES_SPEC: list[tuple[str, str, str]] = [
    (
        "Мясо, птица и морепродукты",
        "Meat, poultry & seafood",
        "fresh/chilled raw meat, poultry, fish, seafood, bacon, sausages & links "
        "(Aidells, Kiolbassa, linguica, franks), salami, prosciutto, charcuterie, and packaged sliced deli meats "
        "(sliced turkey, ham, roast beef).",
    ),
    (
        "Овощи, фрукты и ягоды",
        "Fruits, vegetables & berries",
        "fresh produce, fruits, berries, vegetables, fresh herbs, salad mixes & kits, kimchi, living potted herbs.",
    ),
    (
        "Молочные продукты, сыр и яйца",
        "Dairy, cheese & eggs",
        "milk, cream, butter, sour cream, yogurt, cultured/probiotic dairy drinks (Cool Plus, kefir), "
        "all cheeses, eggs.",
    ),
    (
        "Выпечка, хлеб и готовая еда (Deli)",
        "Bakery, bread & prepared food (Deli)",
        "bread, buns, baguettes, croissants, tortillas, naan, pita, cakes, pastries, crepes/pancakes, "
        "and fresh prepared Costco Service Deli / ready-to-eat chilled meals (rotisserie chicken, sushi, poke, "
        "refrigerated ravioli/pasta, prepared entrées in sauce).",
    ),
    (
        "Бакалея, масла, специи и консервы",
        "Pantry, oils, spices & canned goods",
        "olive & cooking oils, vinegar, grains, rice, oats/granola/cereal, flour, sugar, yeast, dry pasta & "
        "instant noodle bowls, sauces, mayonnaise, marinades, spices/seasonings/rubs, honey, jams/fruit spreads, "
        "olives, sauerkraut, canned goods.",
    ),
    (
        "Заморозка и полуфабрикаты",
        "Frozen & ready meals",
        "frozen foods — dumplings/potstickers/mandu, frozen burgers/patties, frozen meatballs, frozen snacks/"
        "appetizers (flautas, spanakopita, takoyaki, pizza), frozen breaded or portioned fish/seafood "
        "(frozen Mahi-Mahi, frozen seafood mix, tortilla-crusted tilapia), frozen vegetables/berries/fruit, ice cream.",
    ),
    (
        "Снеки, сладости, орехи и напитки",
        "Snacks, sweets, nuts & drinks",
        "chocolate, candy, cookies, crackers, chips, nuts, trail mix, dried & freeze-dried fruit, "
        "protein/granola bars, non-alcoholic beverages (water, juice, soda, kombucha, energy drinks), "
        "and coffee (whole bean, ground, K-Cup pods) & tea.",
    ),
    (
        "Бытовая химия, бумага и уборка",
        "Household, paper & cleaning",
        "laundry detergent, dish soap/pods, surface/oven/shoe cleaners, bath tissue/toilet paper, paper towels, "
        "facial tissues, trash bags, disposable food storage bags (Ziploc), foil, parchment.",
    ),
    (
        "Здоровье, аптека, витамины и гигиена",
        "Health, pharmacy, vitamins & personal care",
        "vitamins, supplements, OTC medications, toothpaste, mouthwash, dental floss, razors/cartridges, deodorant, "
        "feminine care (pads, liners), shampoo, conditioner, body wash, skincare/creams/lotions, toner pads, "
        "sunscreen, cosmetics, massagers.",
    ),
    (
        "Товары для дома, кухни и сада",
        "Home, kitchen & garden",
        "cookware, reusable food storage containers & lunch boxes (Pyrex, Bentgo), cabinet/cosmetic organizers, "
        "storage drawers, hangers, home textiles, gift wrap & ribbon, garden planters & decorative floral "
        "arrangements, folding carts/hand trucks, multi-tools & hardware.",
    ),
    (
        "Одежда и обувь",
        "Clothing & shoes",
        "men's, women's, and kids' apparel, underwear, socks, swimwear, shoes, gloves, hats/beanies, "
        "sunglasses/eyewear, bags & backpacks.",
    ),
    (
        "Электроника и техника",
        "Electronics & appliances",
        "electronics, headphones, appliances, portable fans, chargers, cables, and batteries "
        "(Duracell AA/AAA, rechargeable).",
    ),
    (
        FUEL_CATEGORY,
        "Auto & fuel (Gas Station)",
        "gasoline, diesel, motor oil, automotive supplies.",
    ),
    (
        "Алкоголь",
        "Alcohol",
        "wine, prosecco, champagne, beer, spirits, sangria.",
    ),
    (
        "Прочее",
        "Other",
        "bottle deposits (`CA REDEMP VAL` / CRV), liquor liter taxes (`LIQUOR LITER TAX`), standalone store coupons/"
        "adjustments (`CPN`, `/123456`), gift cards, and items (such as toys/`LEGO` when no kids/toys category is "
        "in `Allowed categories`) that do not fit any other allowed category.",
    ),
]

DEFAULT_CATEGORIES: list[str] = [name for name, _, _ in _DEFAULT_CATEGORIES_SPEC]
DEFAULT_CATEGORY_LABELS_EN: dict[str, str] = {name: en for name, en, _ in _DEFAULT_CATEGORIES_SPEC}
DEFAULT_CATEGORY_SCOPES: dict[str, str] = {name: scope for name, _, scope in _DEFAULT_CATEGORIES_SPEC}


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
