"""DigitalCheckAI FastAPI application entry point."""
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
