"""Главный модуль FastAPI приложения DigitalCheckAI."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import IMAGES_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="DigitalCheckAI",
    description="Локальная аналитика покупок и чеков Costco и Safeway с расшифровкой товаров через Gemini API",
    version="1.0.0",
)

app.include_router(api_router)


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """Страница и скрипты меняются вместе с кодом: браузер должен перепроверять их при каждой загрузке."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


@app.get("/")
def serve_dashboard() -> FileResponse:
    """Отдаёт главную страницу веб-дашборда."""
    return FileResponse(str(STATIC_DIR / "index.html"))
