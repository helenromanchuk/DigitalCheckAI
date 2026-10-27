"""Tests for importing Costco and Safeway JSON files (the format produced by the export scripts in the help)."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes import get_db
from app.database import Database
from app.main import app
from tests.test_safeway import SAMPLE_SAFEWAY_RECEIPT


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Database]:
    test_db = Database(tmp_path / "import.db")
    app.dependency_overrides[get_db] = lambda: test_db
    monkeypatch.setattr("app.categorizer.gemini_classifier.get_gemini_api_key", lambda: "")
    monkeypatch.setattr("app.api.routes.download_missing_images", lambda db: 0)
    return TestClient(app), test_db


def _upload(client: TestClient, payload, name: str = "receipts.json"):
    return client.post("/api/import", files={"file": (name, json.dumps(payload).encode("utf-8"), "application/json")})


def test_import_safeway_export_file(tmp_path: Path, monkeypatch):
    client, test_db = _client(tmp_path, monkeypatch)

    res = _upload(client, {"source": "safeway", "receipts": [SAMPLE_SAFEWAY_RECEIPT]})
    assert res.status_code == 200
    body = res.json()
    assert body["store"] == "safeway"
    assert body["inserted_receipts"] == 1

    receipts = client.get("/api/receipts?store=safeway").json()["receipts"]
    assert [r["transaction_barcode"] for r in receipts] == ["SWY-12345678901234567890123"]
    # The image URL is saved; the file is downloaded during import
    assert [m["item_number"] for m in test_db.get_items_missing_images()] == ["SWY-6927607035"]

    # Importing again doesn't create duplicates
    again = _upload(client, {"source": "safeway", "receipts": [SAMPLE_SAFEWAY_RECEIPT]}).json()
    assert (again["inserted_receipts"], again["updated_receipts"]) == (0, 1)

    app.dependency_overrides.clear()


def test_import_safeway_list_without_items_is_rejected(tmp_path: Path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    list_only = {"receipts": [{"_id": "abc", "posDateTime": "2026-10-20T16:02:40.00", "finalTotal": "94.69", "itemCount": 25}]}

    res = _upload(client, list_only)
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "safeway_list_only"

    app.dependency_overrides.clear()


def test_import_costco_export_file_with_order_details(tmp_path: Path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    payload = {
        "source": "costco",
        "receipts": [
            {
                "transactionBarcode": "21000000000002610161201",
                "transactionDateTime": "2026-10-16T12:01:00",
                "receiptType": "In-Warehouse",
                "documentType": "WarehouseReceiptDetail",
                "warehouseName": "GREAT OAKS",
                "subTotal": 24.78,
                "taxes": 0,
                "total": 24.78,
                "itemArray": [
                    {"itemNumber": "1268246", "itemDescription01": "ORG WHL MILK", "unit": 1, "amount": 12.79},
                    {"itemNumber": "2534", "itemDescription01": "CHERRIES", "unit": 1, "amount": 11.99},
                ],
            }
        ],
        "bcOrders": [
            {
                "orderNumber": "1318889887",
                "orderPlacedDate": "2026-10-02T12:15:01.937",
                "merchandiseTotal": 49.99,
                "orderTax": 4.98,
                "orderTotal": 54.97,
                "orderShipTos": [
                    {"orderLineItems": [
                        {"itemNumber": "2072081", "itemDescription": "Leatherman Sidekick", "unitPrice": 49.99,
                         "quantity": 1, "merchandiseTotal": 49.99},
                    ]}
                ],
            }
        ],
    }

    res = _upload(client, payload)
    assert res.status_code == 200
    assert res.json()["store"] == "costco"
    assert res.json()["fetched_receipts"] == 2

    receipts = {r["receipt_type"]: r for r in client.get("/api/receipts?store=costco").json()["receipts"]}
    assert len(receipts["warehouse"]["items"]) == 2
    online = receipts["online"]
    assert online["total"] == 54.97
    assert [(i["item_number"], i["net_amount"]) for i in online["items"]] == [("2072081", 49.99)]

    app.dependency_overrides.clear()
