"""Integration tests for the FastAPI stats endpoints, period filtering, and category changes."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes import get_db
from app.database import Database
from app.main import app


def test_api_import_stats_and_category_override(tmp_path: Path, monkeypatch):
    test_db = Database(tmp_path / "api_test.db")
    app.dependency_overrides[get_db] = lambda: test_db
    monkeypatch.setattr("app.categorizer.gemini_classifier.get_gemini_api_key", lambda: "")

    client = TestClient(app)

    sample_payload = [
        {
            "transactionBarcode": "2401084700101",
            "transactionDate": "2026-10-17",
            "receiptType": "In-Warehouse",
            "warehouseName": "SAN JOSE #148",
            "subTotal": 24.99,
            "taxes": 0.0,
            "total": 24.99,
            "itemArray": [
                {"itemNumber": "678910", "itemDescription01": "KS ORG EVOO", "unit": 1, "amount": 24.99},
            ],
        },
        {
            "transactionBarcode": "2401084700105",
            "transactionDate": "2026-08-08",
            "receiptType": "In-Warehouse",
            "warehouseName": "SAN JOSE #148",
            "subTotal": 24.99,
            "taxes": 0.0,
            "total": 24.99,
            "itemArray": [
                {"itemNumber": "678910", "itemDescription01": "KS ORG EVOO", "unit": 1, "amount": 24.99},
            ],
        },
        {
            "transactionBarcode": "2401084700102",
            "transactionDate": "2026-10-04",
            "receiptType": "Fuel",
            "documentType": "FuelReceipt",
            "warehouseName": "SAN JOSE GAS #148",
            "total": 52.40,
            "itemArray": [
                {"itemNumber": "800877", "itemDescription01": "UNLEADED GASOLINE 87", "unit": 12.2, "amount": 52.40},
            ],
        },
    ]

    # 1. Import receipts via /api/import
    res_import = client.post(
        "/api/import",
        files={"file": ("receipts.json", json.dumps(sample_payload).encode("utf-8"), "application/json")},
    )
    assert res_import.status_code == 200
    import_data = res_import.json()
    assert import_data["fetched_receipts"] == 3

    # 2. Request stats for all purchase types
    res_stats = client.get("/api/stats?receipt_type=all")
    assert res_stats.status_code == 200
    stats = res_stats.json()
    assert stats["summary"]["receipt_count"] == 3
    assert stats["summary"]["total_spent"] > 0

    # 3. Filter to fuel only (gas)
    res_gas = client.get("/api/stats?receipt_type=gas")
    assert res_gas.status_code == 200
    gas_stats = res_gas.json()
    assert gas_stats["summary"]["receipt_count"] == 1
    assert gas_stats["by_category"][0]["category"] == "Auto & fuel (Gas Station)"

    # 4. Change the category of item #678910 via PATCH /api/items/678910
    res_patch = client.patch(
        "/api/items/678910",
        json={"category": "Gifts & cooking", "clean_name": "KS Olive Oil 2L"},
    )
    assert res_patch.status_code == 200
    patched = res_patch.json()["item"]
    assert patched["category"] == "Gifts & cooking"
    assert patched["is_manual_override"] == 1

    # The new category appears in stats with the total of 2 purchases (24.99 * 2 = 49.98)
    res_stats_after = client.get("/api/stats?receipt_type=all")
    cats_after = {c["category"]: c["net_spent"] for c in res_stats_after.json()["by_category"]}
    assert cats_after.get("Gifts & cooking") == 49.98

    app.dependency_overrides.clear()


def test_membership_card_profitability_analytics(tmp_path: Path, monkeypatch):
    test_db = Database(tmp_path / "membership_test.db")
    app.dependency_overrides[get_db] = lambda: test_db
    monkeypatch.setattr("app.categorizer.gemini_classifier.get_gemini_api_key", lambda: "")

    client = TestClient(app)

    # 1. Import a warehouse receipt ($1000 net + $50 alcohol + $80 tax) and a gas receipt ($200 fuel)
    payload_low = [
        {
            "transactionBarcode": "W-100",
            "transactionDate": "2026-05-10",
            "receiptType": "In-Warehouse",
            "warehouseName": "SAN JOSE #148",
            "subTotal": 1050.00,
            "taxes": 80.00,
            "total": 1130.00,
            "itemArray": [
                {"itemNumber": "104422", "itemDescription01": "USDA CHOICE RIBEYE", "unit": 1, "amount": 1000.00},
                {"itemNumber": "901122", "itemDescription01": "KIRKLAND PROSECCO", "unit": 1, "amount": 50.00},
            ],
        },
        {
            "transactionBarcode": "G-100",
            "transactionDate": "2026-05-12",
            "receiptType": "Fuel",
            "documentType": "FuelReceipt",
            "warehouseName": "SAN JOSE GAS #148",
            "total": 200.00,
            "itemArray": [
                {"itemNumber": "800877", "itemDescription01": "UNLEADED GASOLINE 87", "unit": 45.0, "amount": 200.00},
            ],
        },
    ]

    client.post(
        "/api/import",
        files={"file": ("low.json", json.dumps(payload_low).encode("utf-8"), "application/json")},
    )

    # Check the analytics with alcohol excluded (exclude_alcohol=true by default):
    # eligible_spend = 1000.00 (fuel $200, tax $80 and alcohol $50 are excluded)
    res_mb_1 = client.get("/api/membership-analytics?year=2026&exclude_alcohol=true")
    assert res_mb_1.status_code == 200
    mb1 = res_mb_1.json()
    assert mb1["spend_breakdown"]["total_spent_with_tax"] == 1330.00
    assert mb1["spend_breakdown"]["excluded_gas_spent"] == 200.00
    assert mb1["spend_breakdown"]["excluded_tax"] == 80.00
    assert mb1["spend_breakdown"]["excluded_alcohol_spent"] == 50.00
    assert mb1["spend_breakdown"]["eligible_spend"] == 1000.00
    assert mb1["comparison"]["estimated_reward"] == 20.00  # 2% of $1000
    assert mb1["comparison"]["recommended_card"] == "gold_star"
    assert mb1["comparison"]["spend_needed_for_breakeven"] == 2250.00  # $3250 - $1000

    # 2. Add a $3,000 online order -> the base becomes $4,000 (> the $3,250 break-even point)
    payload_high = [
        {
            "orderNumber": "555000111",
            "orderPlacedDate": "2026-08-20T10:00:00",
            "merchandiseTotal": 3000.00,
            "orderTax": 240.00,
            "orderTotal": 3240.00,
            "orderLineItems": [
                {
                    "itemNumber": "1799882",
                    "itemDescription": "MACBOOK PRO",
                    "quantity": 1,
                    "unitPrice": 3000.00,
                    "merchandiseTotal": 3000.00,
                    "discountAmount": 0.0,
                }
            ],
        }
    ]
    client.post(
        "/api/import",
        files={"file": ("high.json", json.dumps(payload_high).encode("utf-8"), "application/json")},
    )

    res_mb_2 = client.get("/api/membership-analytics?year=2026&exclude_alcohol=true")
    assert res_mb_2.status_code == 200
    mb2 = res_mb_2.json()
    assert mb2["spend_breakdown"]["eligible_spend"] == 4000.00
    assert mb2["comparison"]["estimated_reward"] == 80.00  # 2% of $4000 = $80
    assert mb2["comparison"]["recommended_card"] == "executive"
    assert mb2["comparison"]["exec_advantage_vs_gold"] == 15.00  # $80 reward - $65 upgrade fee = +$15 net benefit
    assert mb2["comparison"]["spend_needed_for_breakeven"] == 0.0

    app.dependency_overrides.clear()
