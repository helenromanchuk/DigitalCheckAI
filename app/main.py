"""DigitalCheckAI FastAPI application entry point."""
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.config import IMAGES_DIR, LOG_LEVEL

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)


def _configure_logging() -> None:
    """Sends app.* log records to the console at LOG_LEVEL; uvicorn only configures its own loggers."""
    app_logger = logging.getLogger("app")
    if app_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app_logger.addHandler(handler)
    level = logging.getLevelNamesMapping().get(LOG_LEVEL)
    app_logger.setLevel(level if level is not None else logging.INFO)
    if level is None:
        app_logger.warning("Неизвестный LOG_LEVEL=%r, используется INFO (DEBUG, INFO, WARNING, ERROR)", LOG_LEVEL)


_configure_logging()

app = FastAPI(
    title="DigitalCheckAI",
    description="Local analytics for Costco and Safeway purchases and receipts, with item decoding via the Gemini API",
    version="1.0.0",
)

app.include_router(api_router)


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """The page and scripts change with the code, so the browser must revalidate them on every load."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


@app.get("/")
def serve_dashboard() -> FileResponse:
    """Serves the dashboard's main page."""
    return FileResponse(str(STATIC_DIR / "index.html"))
