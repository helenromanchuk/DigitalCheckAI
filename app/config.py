"""Конфигурация приложения DigitalCheckAI."""
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

DEFAULT_CATEGORIES: list[str] = [
    "Мясо, птица и морепродукты",
    "Овощи, фрукты и ягоды",
    "Молочные продукты, сыр и яйца",
    "Выпечка, хлеб и готовая еда (Deli)",
    "Бакалея, масла, специи и консервы",
    "Заморозка и полуфабрикаты",
    "Снеки, сладости, орехи и напитки",
    "Бытовая химия, бумага и уборка",
    "Здоровье, аптека, витамины и гигиена",
    "Товары для дома, кухни и сада",
    "Одежда и обувь",
    "Электроника и техника",
    "Авто и заправка (Gas Station)",
    "Алкоголь",
    "Прочее",
]


def get_gemini_api_key() -> str:
    """Возвращает актуальный GEMINI_API_KEY из окружения или .env файла."""
    load_dotenv(ENV_PATH, override=True)
    return os.getenv("GEMINI_API_KEY", "").strip()


def save_gemini_api_key(api_key: str) -> None:
    """Сохраняет GEMINI_API_KEY в локальный файл .env и обновляет окружение."""
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
    """Возвращает имя модели Gemini для категоризации."""
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
