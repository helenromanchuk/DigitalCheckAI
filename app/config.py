"""DigitalCheckAI configuration."""
import os
import re
import sys
from pathlib import Path
from threading import RLock

from dotenv import dotenv_values, unset_key
from keyring.backend import KeyringBackend

from app.errors import AppError

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
# Do not copy legacy plaintext credentials into the process environment.
for _name, _value in dotenv_values(ENV_PATH).items():
    if _name != "GEMINI_API_KEY" and _value is not None:
        os.environ.setdefault(_name, _value)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("COSTCO_DB_PATH", str(DATA_DIR / "costco.db")))
# Level for app.* loggers; DEBUG also logs every Gemini prompt and response
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
IMAGES_DIR = DATA_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

FUEL_CATEGORY = "Auto & fuel"
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
        "Fruits & vegetables",
        "Овощи и фрукты",
        "fresh produce, fruits, berries, vegetables, fresh herbs, salad mixes & kits, kimchi, living potted herbs.",
    ),
    (
        "Dairy & eggs",
        "Молочные продукты и яйца",
        "milk, cream, butter, sour cream, yogurt, cultured/probiotic dairy drinks (Cool Plus, kefir), "
        "all cheeses, eggs. Dairy drinks stay here; plant-based milk belongs to Beverages, coffee & tea.",
    ),
    (
        "Bread & bakery",
        "Хлеб и выпечка",
        "bread, buns, baguettes, croissants, tortillas, naan, pita, cakes, fresh pastries, crepes/pancakes. "
        "Packaged cookies and crackers belong to Snacks & sweets; frozen baked goods belong to Frozen foods.",
    ),
    (
        "Prepared meals & deli",
        "Готовая еда",
        "fresh/chilled prepared meals, rotisserie chicken, sushi, poke, refrigerated ravioli/pasta, "
        "prepared entrées in sauce. Sliced deli meats belong to Meat, poultry & seafood; "
        "salad kits belong to Fruits & vegetables; frozen meals belong to Frozen foods.",
    ),
    (
        "Frozen foods",
        "Замороженные продукты",
        "all frozen food, including raw meat/fish, vegetables, berries, pizza, dumplings/potstickers/mandu, "
        "burgers/patties, meatballs, appetizers, frozen bakery, ice cream. Frozen takes priority over food type. "
        "Freeze-dried shelf-stable fruit belongs to Snacks & sweets.",
    ),
    (
        "Pantry & cooking ingredients",
        "Бакалея и продукты для готовки",
        "olive & cooking oils, vinegar, grains, rice, oats/granola/cereal, flour, sugar, yeast, dry pasta & "
        "instant noodle bowls, sauces, mayonnaise, marinades, spices/seasonings/rubs, honey, jams/fruit spreads, "
        "olives, sauerkraut, canned goods. Coffee and tea belong to Beverages, coffee & tea.",
    ),
    (
        "Snacks & sweets",
        "Снеки и сладости",
        "chocolate, candy, cookies, crackers, chips, nuts, trail mix, dried & freeze-dried fruit, "
        "protein/granola bars. Drinks belong to Beverages, coffee & tea.",
    ),
    (
        "Beverages, coffee & tea",
        "Напитки, кофе и чай",
        "non-alcoholic drinks: water, juice, soda, kombucha, energy drinks, plant-based milk, "
        "coffee (whole bean, ground, K-Cup pods), tea. Dairy drinks stay in Dairy & eggs; "
        "alcoholic drinks belong to Alcohol.",
    ),
    (
        ALCOHOL_CATEGORY,
        "Алкоголь",
        "wine, prosecco, champagne, beer, spirits, sangria. Takes priority over non-alcoholic beverages.",
    ),
    (
        "Household supplies & cleaning",
        "Бытовые расходники и уборка",
        "laundry detergent, dish soap/pods, surface/oven/shoe cleaners, bath tissue/toilet paper, paper towels, "
        "facial tissues, trash bags, disposable food storage bags (Ziploc), foil, parchment. "
        "Reusable household goods belong to Home, kitchen & garden.",
    ),
    (
        "Health & personal care",
        "Здоровье и личная гигиена",
        "vitamins, supplements, OTC medications, toothpaste, mouthwash, dental floss, razors/cartridges, deodorant, "
        "feminine care (pads, liners), shampoo, conditioner, body wash, skincare/creams/lotions, toner pads, "
        "sunscreen, cosmetics, massagers.",
    ),
    (
        "Home, kitchen & garden",
        "Дом, кухня и сад",
        "cookware, reusable food storage containers & lunch boxes (Pyrex, Bentgo), cabinet/cosmetic organizers, "
        "storage drawers, hangers, furniture, home textiles, gift wrap & ribbon, garden plants/planters & "
        "decorative floral arrangements, folding carts/hand trucks, multi-tools & hardware. "
        "Kitchen appliances belong to Electronics & appliances; disposable supplies go to Household supplies & cleaning.",
    ),
    (
        "Clothing, shoes & accessories",
        "Одежда, обувь и аксессуары",
        "men's, women's, and kids' apparel, underwear, socks, swimwear, shoes, gloves, hats/beanies, "
        "sunglasses/eyewear, bags & backpacks.",
    ),
    (
        "Electronics & appliances",
        "Электроника и бытовая техника",
        "electronics, computers, headphones, appliances (including blenders and coffee makers), portable fans, "
        "chargers, cables, and batteries "
        "(Duracell AA/AAA, rechargeable).",
    ),
    (
        FUEL_CATEGORY,
        "Авто и топливо",
        "gasoline, diesel, tires, motor oil, automotive supplies.",
    ),
    (
        "Toys & games",
        "Игрушки и игры",
        "LEGO, dolls, toys, puzzles, board games. Baby supplies and pet items use a matching custom category "
        "when present, otherwise Other.",
    ),
    (
        OTHER_CATEGORY,
        "Прочее",
        "unmatched deposits/tax lines, standalone store coupons/adjustments (`CPN`, `/123456`), gift cards, "
        "and items that do not fit any allowed category. Attached discounts and bottle deposits follow their "
        "owner item's category; never classify them separately.",
    ),
]

DEFAULT_CATEGORIES: list[str] = [name for name, _, _ in _DEFAULT_CATEGORIES_SPEC]
DEFAULT_CATEGORY_LABELS_RU: dict[str, str] = {name: ru for name, ru, _ in _DEFAULT_CATEGORIES_SPEC}
DEFAULT_CATEGORY_SCOPES: dict[str, str] = {name: scope for name, _, scope in _DEFAULT_CATEGORIES_SPEC}

# Split categories use a conservative default; the migration separates meals and drinks by description.
LEGACY_CATEGORY_ALIASES: dict[str, str] = {
    "Fruits, vegetables & berries": "Fruits & vegetables",
    "Овощи, фрукты и ягоды": "Fruits & vegetables",
    "Dairy, cheese & eggs": "Dairy & eggs",
    "Молочные продукты, сыр и яйца": "Dairy & eggs",
    "Bakery, bread & prepared food (Deli)": "Bread & bakery",
    "Выпечка, хлеб и готовая еда (Deli)": "Bread & bakery",
    "Pantry, oils, spices & canned goods": "Pantry & cooking ingredients",
    "Бакалея, масла, специи и консервы": "Pantry & cooking ingredients",
    "Frozen & ready meals": "Frozen foods",
    "Заморозка и полуфабрикаты": "Frozen foods",
    "Snacks, sweets, nuts & drinks": "Snacks & sweets",
    "Снеки, сладости, орехи и напитки": "Snacks & sweets",
    "Household, paper & cleaning": "Household supplies & cleaning",
    "Бытовая химия, бумага и уборка": "Household supplies & cleaning",
    "Health, pharmacy, vitamins & personal care": "Health & personal care",
    "Здоровье, аптека, витамины и гигиена": "Health & personal care",
    "Товары для дома, кухни и сада": "Home, kitchen & garden",
    "Clothing & shoes": "Clothing, shoes & accessories",
    "Одежда и обувь": "Clothing, shoes & accessories",
    "Электроника и техника": "Electronics & appliances",
    "Auto & fuel (Gas Station)": FUEL_CATEGORY,
    "Авто и заправка (Gas Station)": FUEL_CATEGORY,
}


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


_CREDENTIAL_SERVICE = "DigitalCheckAI"
_CREDENTIAL_ACCOUNT = "GEMINI_API_KEY"
_CREDENTIAL_LOCK = RLock()


class CredentialStoreError(AppError):
    """A credential failure with no secret or backend exception in its message."""

    def __init__(self) -> None:
        super().__init__(
            "credential_store_unavailable",
            "Could not access secure credential storage or remove the legacy .env key. "
            "Unlock your operating system credential store and check .env permissions, then retry. "
            "An injected GEMINI_API_KEY environment variable can be used without saving it.",
        )


def _credential_store() -> KeyringBackend:
    """Select a native secure backend directly, bypassing configurable plaintext plugins."""
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring()
    if sys.platform == "darwin":
        from keyring.backends.macOS import Keyring

        return Keyring()
    if sys.platform.startswith("linux"):
        from keyring.backends.SecretService import Keyring

        return Keyring()
    raise CredentialStoreError()


def _remove_legacy_gemini_key() -> None:
    """Remove every legacy assignment, including quoted, multiline and exported values."""
    if "GEMINI_API_KEY" in dotenv_values(ENV_PATH):
        removed, _ = unset_key(ENV_PATH, "GEMINI_API_KEY")
        if not removed:
            raise CredentialStoreError()


def get_gemini_api_key() -> str:
    """Read the native credential store, migrating old .env keys; allow injected environment keys."""
    with _CREDENTIAL_LOCK:
        legacy = (dotenv_values(ENV_PATH).get("GEMINI_API_KEY") or "").strip()
        try:
            store = _credential_store()
            saved = store.get_password(_CREDENTIAL_SERVICE, _CREDENTIAL_ACCOUNT)
        except Exception:
            # Headless systems may inject a key instead of using a desktop keychain.
            if legacy and legacy != "your_gemini_api_key_here":
                raise CredentialStoreError() from None
            return os.getenv("GEMINI_API_KEY", "").strip()
        if legacy:
            if not saved and legacy != "your_gemini_api_key_here":
                save_gemini_api_key(legacy)
                return legacy
            try:
                _remove_legacy_gemini_key()
            except Exception:
                raise CredentialStoreError() from None
        return (saved or os.getenv("GEMINI_API_KEY", "")).strip()


def save_gemini_api_key(api_key: str) -> None:
    """Persist only in native secure storage; remove legacy plaintext after a successful write."""
    clean_key = api_key.strip()
    if not clean_key:
        raise ValueError("The Gemini API key must not be empty.")
    with _CREDENTIAL_LOCK:
        try:
            _credential_store().set_password(_CREDENTIAL_SERVICE, _CREDENTIAL_ACCOUNT, clean_key)
            _remove_legacy_gemini_key()
        except Exception:
            raise CredentialStoreError() from None


def get_gemini_model() -> str:
    """Returns the Gemini model name used for categorization."""
    return os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
