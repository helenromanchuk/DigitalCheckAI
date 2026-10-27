"""FastAPI routes for Costco.com sync, categorization, and spending analytics."""
import asyncio
import json
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.categorizer.gemini_classifier import GeminiItemClassifier
from app.config import (
    RESERVED_CATEGORY_NAMES,
    get_gemini_api_key,
    get_gemini_model,
    merge_category_labels_en,
    save_gemini_api_key,
)
from app.database import Database
from app.errors import AppError, error_detail
from app.scraper.costco_client import CostcoClient
from app.scraper.images import download_missing_images
from app.scraper.parser import parse_any_costco_payload
from app.scraper.safeway_client import SafewayClient, parse_safeway_payload

router = APIRouter(prefix="/api", tags=["costco"])

_db_instance: Database | None = None


def _sync_error_detail(site: str, exc: BaseException) -> dict[str, Any]:
    """A sync failure with its cause; the UI shows both in its own language."""
    cause = error_detail(exc)
    return {
        "code": "sync_failed",
        "params": {"site": site},
        "cause": cause,
        "message": f"Sync with {site} failed: {cause['message']}",
    }


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
    name_en: str = ""


class SetGeminiKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=5)


@router.get("/config")
def get_app_config(db: Database = Depends(get_db)) -> dict[str, Any]:
    """Returns the Gemini API key status, the model, and all available categories."""
    key = get_gemini_api_key()
    has_key = bool(key and key != "your_gemini_api_key_here")
    pending = db.get_pending_items_for_gemini()
    return {
        "has_gemini_key": has_key,
        "gemini_model": get_gemini_model(),
        "categories": db.get_all_categories(),
        "category_labels_en": merge_category_labels_en(db.get_category_labels_en()),
        "pending_gemini_items_count": len(pending),
    }


@router.post("/config/gemini-key")
def update_gemini_key(
    payload: SetGeminiKeyRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Saves GEMINI_API_KEY to .env and classifies pending items."""
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
    """Adds a new user-defined category."""
    # Names and English labels must not repeat another category's: a custom "Other" would compete with
    # "Прочее" in the Gemini prompt, and English labels set on a default category would be ignored.
    # Re-entering an existing custom category with a new English name (a translation) is allowed
    name = payload.name.strip()
    labels_en = merge_category_labels_en(db.get_category_labels_en())
    taken = set(RESERVED_CATEGORY_NAMES)
    taken |= {c.casefold() for c in db.get_all_categories() if c != name}
    taken |= {en.casefold() for cat, en in labels_en.items() if cat != name}
    for value in (name, payload.name_en.strip()):
        if value.casefold() in taken:
            raise HTTPException(
                status_code=400,
                detail=AppError(
                    "category_reserved", f"'{value}' is already used by another category.", name=value
                ).to_detail(),
            )
    categories = db.add_custom_category(payload.name, payload.name_en)
    return {
        "categories": categories,
        "category_labels_en": merge_category_labels_en(db.get_category_labels_en()),
    }


@router.post("/sync")
async def sync_from_costco(
    payload: SyncRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """
    Opens a browser (Playwright), connects to costco.com, downloads all receipts
    (In-Warehouse, Gas Station, Online Orders) for the selected period, and sends
    only new Item #s to the Gemini API.
    """
    client = CostcoClient()
    try:
        parsed_receipts = await client.fetch_receipts(
            start_date=payload.start_date,
            end_date=payload.end_date,
            timeout_seconds=payload.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_sync_error_detail("Costco.com", exc)) from exc

    upsert_info = db.upsert_receipts(parsed_receipts)
    classifier = GeminiItemClassifier(db)
    # Run the synchronous Gemini call in a separate thread so it doesn't block the server's event loop
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
    Opens a browser (Playwright) on safeway.com, waits for the user to sign in, downloads
    Safeway in-store receipts for the selected period, and sends new items to the Gemini API.
    """
    client = SafewayClient()
    try:
        parsed_receipts = await client.fetch_receipts(
            start_date=payload.start_date,
            end_date=payload.end_date,
            timeout_seconds=payload.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_sync_error_detail("Safeway.com", exc)) from exc

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
    """Imports a JSON file with Costco or Safeway receipts (the format is detected automatically)."""
    try:
        raw_bytes = await file.read()
        data = json.loads(raw_bytes.decode("utf-8-sig"))
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=AppError("invalid_json", f"Invalid JSON file: {exc}", error=str(exc)).to_detail(),
        ) from exc

    try:
        parsed_receipts = parse_safeway_payload(data)
    except AppError as exc:
        raise HTTPException(status_code=400, detail=exc.to_detail()) from exc
    store = "safeway"
    if not parsed_receipts:
        parsed_receipts = parse_any_costco_payload(data)
        store = "costco"
    if not parsed_receipts:
        raise HTTPException(
            status_code=400,
            detail=AppError("no_receipts", "No Costco or Safeway receipts found in the file.").to_detail(),
        )

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
    """Sends every item that has no manual override and hasn't been classified by Gemini yet to the Gemini API."""
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
    """Returns spending statistics by category and month for the selected period."""
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
    """Returns an aggregated list of items for the period with their categories and totals."""
    items = db.get_items_summary(
        start_date=start_date,
        end_date=end_date,
        receipt_type=receipt_type,
        category=category,
        search=search,
        store=store,
    )
    return {"items": items, "categories": db.get_all_categories()}


# ":path" so that item numbers with "/" (Safeway numbers built from names, e.g. "MILK 1/2 GAL") still match
@router.patch("/items/{item_number:path}")
def update_item_category(
    item_number: str,
    payload: UpdateItemCategoryRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Lets the user manually change the category (and/or name) of the given Item #."""
    updated = db.update_item_category_manual(
        item_number=item_number,
        category=payload.category,
        clean_name=payload.clean_name,
    )
    if not updated:
        raise HTTPException(
            status_code=404,
            detail=AppError(
                "item_not_found", f"Item {item_number} not found in the catalog.", item=item_number
            ).to_detail(),
        )
    return {"status": "ok", "item": updated, "categories": db.get_all_categories()}


@router.get("/receipts")
def get_receipts(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    receipt_type: str | None = Query(default="all"),
    store: str | None = Query(default="all"),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Returns receipts and online orders for the selected period with their line items."""
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
    """Searches item purchases across all stores and all time."""
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
    """Compares Costco memberships (Gold Star vs Executive 2% Reward) for a year or period."""
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
