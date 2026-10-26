"""Маршруты FastAPI для синхронизации с Costco.com, категоризации и аналитики расходов."""
import asyncio
from datetime import date, timedelta
import json
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field

from app.categorizer.gemini_classifier import GeminiItemClassifier
from app.config import get_gemini_api_key, get_gemini_model, save_gemini_api_key
from app.database import Database
from app.scraper.costco_client import CostcoClient
from app.scraper.images import download_missing_images
from app.scraper.parser import parse_any_costco_payload
from app.scraper.safeway_client import SafewayClient, parse_safeway_payload

router = APIRouter(prefix="/api", tags=["costco"])

_db_instance: Database | None = None


def get_db() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance


class SyncRequest(BaseModel):
    start_date: str = Field(default_factory=lambda: (date.today() - timedelta(days=90)).isoformat())
    end_date: str = Field(default_factory=lambda: date.today().isoformat())
    timeout_seconds: int = Field(default=240, ge=30, le=600)


class UpdateItemCategoryRequest(BaseModel):
    category: str = Field(..., min_length=1)
    clean_name: str | None = None


class AddCategoryRequest(BaseModel):
    name: str = Field(..., min_length=1)


class SetGeminiKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=5)


@router.get("/config")
def get_app_config(db: Database = Depends(get_db)) -> dict[str, Any]:
    """Возвращает статус ключа Gemini API, модель и список всех доступных категорий."""
    key = get_gemini_api_key()
    has_key = bool(key and key != "your_gemini_api_key_here")
    pending = db.get_pending_items_for_gemini()
    return {
        "has_gemini_key": has_key,
        "gemini_model": get_gemini_model(),
        "categories": db.get_all_categories(),
        "pending_gemini_items_count": len(pending),
    }


@router.post("/config/gemini-key")
def update_gemini_key(
    payload: SetGeminiKeyRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Сохраняет GEMINI_API_KEY в .env и автоматически классифицирует ожидающие товары."""
    save_gemini_api_key(payload.api_key)
    pending = db.get_pending_items_for_gemini()
    classifier = GeminiItemClassifier(db)
    cls_result = classifier.classify_new_items(pending) if pending else {"classified_count": 0, "provider": "gemini"}
    return {
        "status": "ok",
        "has_gemini_key": True,
        "reclassified": cls_result,
    }


@router.post("/categories")
def create_custom_category(
    payload: AddCategoryRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Добавляет новую пользовательскую категорию в список."""
    categories = db.add_custom_category(payload.name)
    return {"categories": categories}


@router.post("/sync")
async def sync_from_costco(
    payload: SyncRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """
    Открывает браузер (Playwright), подключается к costco.com, скачивает все чеки
    (In-Warehouse, Gas Station, Online Orders) за выбранный период и отправляет
    только новые Item # в Gemini API.
    """
    client = CostcoClient()
    try:
        parsed_receipts = await client.fetch_receipts(
            start_date=payload.start_date,
            end_date=payload.end_date,
            timeout_seconds=payload.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка синхронизации с Costco.com: {exc}") from exc

    upsert_info = db.upsert_receipts(parsed_receipts)
    classifier = GeminiItemClassifier(db)
    # Синхронный вызов Gemini в отдельном потоке, чтобы не блокировать event loop сервера
    cls_info = await asyncio.to_thread(classifier.classify_new_items, upsert_info["new_items"])

    return {
        "status": "ok",
        "fetched_receipts": len(parsed_receipts),
        "inserted_receipts": upsert_info["inserted_receipts"],
        "updated_receipts": upsert_info["updated_receipts"],
        "new_items_count": len(upsert_info["new_items"]),
        "classification": cls_info,
    }


@router.post("/sync/safeway")
async def sync_from_safeway(
    payload: SyncRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """
    Открывает браузер (Playwright) на safeway.com, ждёт входа пользователя, скачивает чеки
    из магазинов Safeway за выбранный период и отправляет новые товары в Gemini API.
    """
    client = SafewayClient()
    try:
        parsed_receipts = await client.fetch_receipts(
            start_date=payload.start_date,
            end_date=payload.end_date,
            timeout_seconds=payload.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка синхронизации с Safeway.com: {exc}") from exc

    upsert_info = db.upsert_receipts(parsed_receipts)
    classifier = GeminiItemClassifier(db)
    cls_info = await asyncio.to_thread(classifier.classify_new_items, upsert_info["new_items"])
    images_downloaded = await asyncio.to_thread(download_missing_images, db)

    return {
        "status": "ok",
        "fetched_receipts": len(parsed_receipts),
        "inserted_receipts": upsert_info["inserted_receipts"],
        "updated_receipts": upsert_info["updated_receipts"],
        "new_items_count": len(upsert_info["new_items"]),
        "images_downloaded": images_downloaded,
        "classification": cls_info,
    }


@router.post("/import")
async def import_receipts_json(
    file: UploadFile = File(...),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Импортирует JSON-файл с чеками Costco или Safeway (формат определяется автоматически)."""
    try:
        raw_bytes = await file.read()
        data = json.loads(raw_bytes.decode("utf-8-sig"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Некорректный JSON-файл: {exc}") from exc

    try:
        parsed_receipts = parse_safeway_payload(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store = "safeway"
    if not parsed_receipts:
        parsed_receipts = parse_any_costco_payload(data)
        store = "costco"
    if not parsed_receipts:
        raise HTTPException(status_code=400, detail="В файле не найдено чеков Costco или Safeway.")

    upsert_info = db.upsert_receipts(parsed_receipts)
    classifier = GeminiItemClassifier(db)
    cls_info = await asyncio.to_thread(classifier.classify_new_items, upsert_info["new_items"])
    images_downloaded = await asyncio.to_thread(download_missing_images, db)

    return {
        "status": "ok",
        "store": store,
        "images_downloaded": images_downloaded,
        "fetched_receipts": len(parsed_receipts),
        "inserted_receipts": upsert_info["inserted_receipts"],
        "updated_receipts": upsert_info["updated_receipts"],
        "new_items_count": len(upsert_info["new_items"]),
        "classification": cls_info,
    }


@router.post("/reclassify-pending")
def reclassify_pending_items(db: Database = Depends(get_db)) -> dict[str, Any]:
    """Отправляет все не переопределённые вручную товары (где ещё не работал Gemini) в Gemini API."""
    pending = db.get_pending_items_for_gemini()
    classifier = GeminiItemClassifier(db)
    cls_info = classifier.classify_new_items(pending)
    return {
        "status": "ok",
        "pending_count": len(pending),
        "classification": cls_info,
    }


@router.get("/stats")
def get_statistics(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    receipt_type: str | None = Query(default="all"),
    store: str | None = Query(default="all"),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Возвращает статистику расходов по категориям и месяцам за выбранный период."""
    return db.get_statistics(start_date=start_date, end_date=end_date, receipt_type=receipt_type, store=store)


@router.get("/items")
def get_items(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    receipt_type: str | None = Query(default="all"),
    category: str | None = Query(default="all"),
    search: str | None = Query(default=None),
    store: str | None = Query(default="all"),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Возвращает агрегированный список товаров за период с их категориями и суммами."""
    items = db.get_items_summary(
        start_date=start_date,
        end_date=end_date,
        receipt_type=receipt_type,
        category=category,
        search=search,
        store=store,
    )
    return {"items": items, "categories": db.get_all_categories()}


@router.patch("/items/{item_number}")
def update_item_category(
    item_number: str,
    payload: UpdateItemCategoryRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Позволяет пользователю вручную изменить категорию (и/или название) для заданного Item #."""
    updated = db.update_item_category_manual(
        item_number=item_number,
        category=payload.category,
        clean_name=payload.clean_name,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Артикул {item_number} не найден в справочнике.")
    return {"status": "ok", "item": updated, "categories": db.get_all_categories()}


@router.get("/receipts")
def get_receipts(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    receipt_type: str | None = Query(default="all"),
    store: str | None = Query(default="all"),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Возвращает список чеков и онлайн-заказов за выбранный период с детализацией позиций."""
    receipts = db.get_receipts_list(
        start_date=start_date,
        end_date=end_date,
        receipt_type=receipt_type,
        store=store,
    )
    return {"receipts": receipts}


@router.get("/search")
def search_purchases(
    q: str = Query(..., min_length=2),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Ищет покупки товара по всем магазинам и за всё время."""
    results = db.search_purchases(q)
    return {"query": q, "results": results}


@router.get("/membership-analytics")
def get_membership_analytics(
    year: str | None = Query(default="auto"),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    gold_fee: float = Query(default=65.0, ge=0.0, le=1000.0),
    exec_fee: float = Query(default=130.0, ge=0.0, le=1000.0),
    reward_rate: float = Query(default=2.0, ge=0.1, le=20.0),
    reward_cap: float = Query(default=1250.0, ge=0.0, le=10000.0),
    exclude_alcohol: bool = Query(default=True),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Рассчитывает аналитику выгодности карт Costco (Gold Star vs Executive 2% Reward) за год или период."""
    return db.get_membership_analytics(
        year=year,
        start_date=start_date,
        end_date=end_date,
        gold_fee=gold_fee,
        exec_fee=exec_fee,
        reward_rate=reward_rate,
        reward_cap=reward_cap,
        exclude_alcohol=exclude_alcohol,
    )
