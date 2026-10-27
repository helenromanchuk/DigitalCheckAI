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
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
IMAGES_DIR = DATA_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

FUEL_CATEGORY = "Авто и заправка (Gas Station)"

DEFAULT_CATEGORY_LABELS_EN: dict[str, str] = {
    "Мясо, птица и морепродукты": "Meat, poultry & seafood",
    "Овощи, фрукты и ягоды": "Fruits, vegetables & berries",
    "Молочные продукты, сыр и яйца": "Dairy, cheese & eggs",
    "Выпечка, хлеб и готовая еда (Deli)": "Bakery, bread & prepared food (Deli)",
    "Бакалея, масла, специи и консервы": "Pantry, oils, spices & canned goods",
    "Заморозка и полуфабрикаты": "Frozen & ready meals",
    "Снеки, сладости, орехи и напитки": "Snacks, sweets, nuts & drinks",
    "Бытовая химия, бумага и уборка": "Household, paper & cleaning",
    "Здоровье, аптека, витамины и гигиена": "Health, pharmacy, vitamins & personal care",
    "Товары для дома, кухни и сада": "Home, kitchen & garden",
    "Одежда и обувь": "Clothing & shoes",
    "Электроника и техника": "Electronics & appliances",
    FUEL_CATEGORY: "Auto & fuel (Gas Station)",
    "Алкоголь": "Alcohol",
    "Прочее": "Other",
}

DEFAULT_CATEGORIES: list[str] = list(DEFAULT_CATEGORY_LABELS_EN)


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
