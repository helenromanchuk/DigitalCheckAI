"""Тесты парсинга чеков Safeway и разделения статистики по магазинам."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes import get_db
from app.database import Database
from app.main import app
from app.scraper.safeway_client import parse_safeway_receipt

# Сокращённая структура ответа /order-account/api/instore (детали чека).
# Сумма позиций (10.48) на 0.50 больше итога: скидка на весь чек, не привязанная к позициям.
SAMPLE_SAFEWAY_RECEIPT = {
    "posDateTime": "2026-10-20T16:02:40",
    "barCode": "12345678901234567890123",
    "storeName": "Safeway",
    "address": {"city": "SAN JOSE"},
    "finalTotal": "9.98",
    "taxAmount": 0,
    "discountTotal": "3.51",
    "items": [
        {
            "lineItemType": "Sale",
            "id": "6927607035",
            "bpn": "960211847",
            "name": "PATAKS MANGO CHICKEN SAUCE",
            "department": "GROCERY",
            "quantity": 1,
            "weightItem": False,
            "regularPrice": "5.99",
            "regularPriceTotal": "5.99",
            "discountTotal": "1.66",
            "reducedPriceTotal": "4.33",
            "isFuel": False,
        },
        {
            "lineItemType": "Sale",
            "id": "7016295873",
            "name": "Item name not found",
            "department": "BAKED GOODS",
            "quantity": 1.5,
            "weightItem": True,
            "regularPrice": "5.00",
            "regularPriceTotal": "7.50",
            "discountTotal": "1.35",
            "reducedPriceTotal": "6.15",
            "isFuel": False,
        },
    ],
}


def test_parse_safeway_receipt_allocates_basket_discount():
    r = parse_safeway_receipt(SAMPLE_SAFEWAY_RECEIPT)

    assert r["transaction_barcode"] == "SWY-12345678901234567890123"
    assert r["store"] == "safeway"
    assert r["receipt_type"] == "safeway"
    assert r["warehouse_name"] == "Safeway San Jose"
    assert r["total"] == 9.98
    assert r["subtotal"] == 9.98

    items = {i["item_number"]: i for i in r["items"]}
    assert set(items) == {"SWY-6927607035", "SWY-7016295873"}
    assert items["SWY-7016295873"]["raw_description"] == "Item name not found (BAKED GOODS)"
    assert items["SWY-7016295873"]["quantity"] == 1.5

    # Картинка есть только у товара с номером bpn
    assert items["SWY-6927607035"]["image_url"].startswith("https://images.albertsons-media.com/is/image/ABS/960211847")
    assert "image_url" not in items["SWY-7016295873"]

    # Нераспределённые 0.50 разнесены по позициям, сумма сходится с итогом чека
    assert round(sum(i["net_amount"] for i in r["items"]), 2) == 9.98
    assert round(sum(i["discount_amount"] for i in r["items"]), 2) == 3.51


def test_store_filter_separates_costco_and_safeway(tmp_path: Path, monkeypatch):
    test_db = Database(tmp_path / "stores.db")
    app.dependency_overrides[get_db] = lambda: test_db
    monkeypatch.setattr("app.categorizer.gemini_classifier.get_gemini_api_key", lambda: "")
    client = TestClient(app)

    costco_receipt = {
        "transaction_barcode": "C-1",
        "transaction_date": "2026-10-17",
        "receipt_type": "warehouse",
        "warehouse_name": "GREAT OAKS",
        "subtotal": 20.0,
        "tax": 0.0,
        "total": 20.0,
        "instant_savings": 0.0,
        "items": [
            {"item_number": "678910", "raw_description": "KS ORG EVOO", "quantity": 1, "unit_price": 20.0,
             "gross_amount": 20.0, "discount_amount": 0.0, "net_amount": 20.0},
        ],
    }
    test_db.upsert_receipts([costco_receipt, parse_safeway_receipt(SAMPLE_SAFEWAY_RECEIPT)])

    def receipt_count(store: str) -> int:
        res = client.get(f"/api/stats?store={store}")
        assert res.status_code == 200
        return res.json()["summary"]["receipt_count"]

    assert receipt_count("costco") == 1
    assert receipt_count("safeway") == 1
    assert receipt_count("all") == 2

    receipts = client.get("/api/receipts?store=safeway").json()["receipts"]
    assert [r["store"] for r in receipts] == ["safeway"]

    # URL картинки попал в справочник и ждёт скачивания
    missing = test_db.get_items_missing_images()
    assert [m["item_number"] for m in missing] == ["SWY-6927607035"]
    test_db.set_item_image_file("SWY-6927607035", "SWY-6927607035.jpg")
    assert test_db.get_items_missing_images() == []
    items = client.get("/api/items?store=safeway").json()["items"]
    assert {i["item_number"]: i["image_file"] for i in items}["SWY-6927607035"] == "SWY-6927607035.jpg"

    # Аналитика карт Costco не учитывает покупки в Safeway
    mb = client.get("/api/membership-analytics?year=2026&exclude_alcohol=false").json()
    assert mb["spend_breakdown"]["receipt_count"] == 1
    assert mb["spend_breakdown"]["eligible_spend"] == 20.0

    app.dependency_overrides.clear()


def test_search_purchases_across_stores(tmp_path: Path, monkeypatch):
    test_db = Database(tmp_path / "search.db")
    app.dependency_overrides[get_db] = lambda: test_db
    monkeypatch.setattr("app.categorizer.gemini_classifier.get_gemini_api_key", lambda: "")
    client = TestClient(app)

    costco_receipt = {
        "transaction_barcode": "C-2",
        "transaction_date": "2026-09-01T10:00:00",
        "receipt_type": "warehouse",
        "warehouse_name": "GREAT OAKS",
        "subtotal": 9.0,
        "tax": 0.0,
        "total": 9.0,
        "items": [
            {"item_number": "555", "raw_description": "KS MANGO CHUTNEY", "quantity": 2, "unit_price": 5.0,
             "gross_amount": 10.0, "discount_amount": 1.0, "net_amount": 9.0},
        ],
    }
    test_db.upsert_receipts([costco_receipt, parse_safeway_receipt(SAMPLE_SAFEWAY_RECEIPT)])
    test_db.update_item_category_manual("555", "Бакалея, масла, специи и консервы", "Чатни из манго")

    res = client.get("/api/search?q=mango")
    assert res.status_code == 200
    rows = res.json()["results"]
    # Новые покупки сверху, оба магазина
    assert [(r["store"], r["purchase_date"]) for r in rows] == [("safeway", "2026-10-20"), ("costco", "2026-09-01")]
    costco_row = rows[1]
    assert (costco_row["quantity"], costco_row["gross_amount"], costco_row["net_amount"]) == (2.0, 10.0, 9.0)

    # Поиск по русскому названию без учёта регистра
    rows_ru = client.get("/api/search?q=ЧАТНИ").json()["results"]
    assert [r["item_number"] for r in rows_ru] == ["555"]

    assert client.get("/api/search?q=m").status_code == 422

    app.dependency_overrides.clear()


def test_store_column_migration_for_existing_db(tmp_path: Path):
    import sqlite3

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE receipts (
            transaction_barcode TEXT PRIMARY KEY,
            transaction_date TEXT NOT NULL,
            receipt_type TEXT NOT NULL DEFAULT 'warehouse',
            warehouse_name TEXT NOT NULL DEFAULT '',
            subtotal REAL NOT NULL DEFAULT 0.0,
            tax REAL NOT NULL DEFAULT 0.0,
            total REAL NOT NULL DEFAULT 0.0,
            instant_savings REAL NOT NULL DEFAULT 0.0,
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("INSERT INTO receipts (transaction_barcode, transaction_date, created_at) VALUES ('X', '2025-01-01', 'now')")
    conn.commit()
    conn.close()

    Database(db_path)

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT store FROM receipts WHERE transaction_barcode = 'X'").fetchone()[0] == "costco"
    conn.close()
