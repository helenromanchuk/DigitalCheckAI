"""Tests for Item # caching in the database, first-time Gemini classification, and manual category changes."""
from pathlib import Path
from app.categorizer.gemini_classifier import GeminiItemClassifier
from app.database import Database
from app.scraper.parser import parse_any_costco_payload


def test_first_time_gemini_classification_and_manual_override(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "test_costco.db")
    gemini_calls: list[list[dict]] = []

    def fake_gemini_batch(self, batch, categories, api_key):
        gemini_calls.append(batch)
        return [
            {
                "item_number": item["item_number"],
                "raw_description": item["raw_description"],
                "clean_name": f"Gemini Decoded: {item['raw_description']}",
                "category": "Бакалея, масла, специи и консервы",
            }
            for item in batch
        ]

    monkeypatch.setattr("app.categorizer.gemini_classifier.get_gemini_api_key", lambda: "test-gemini-key-123")
    monkeypatch.setattr(GeminiItemClassifier, "_call_gemini_batch", fake_gemini_batch)

    receipt_1 = parse_any_costco_payload(
        {
            "transactionBarcode": "R-001",
            "transactionDate": "2026-10-01",
            "total": 24.99,
            "itemArray": [
                {"itemNumber": "678910", "itemDescription01": "KS ORG EVOO", "unit": 1, "amount": 24.99}
            ],
        }
    )

    # 1. First load of a receipt with Item #678910 -> it must land in new_items and be sent to Gemini
    upsert_1 = db.upsert_receipts(receipt_1)
    assert len(upsert_1["new_items"]) == 1
    assert upsert_1["new_items"][0]["item_number"] == "678910"

    classifier = GeminiItemClassifier(db)
    cls_res_1 = classifier.classify_new_items(upsert_1["new_items"])
    assert cls_res_1["provider"] == "gemini"
    assert cls_res_1["classified_count"] == 1
    assert len(gemini_calls) == 1

    # The decoded name and category are saved in the local database
    catalog_map = db.get_existing_catalog_map(["678910"])
    assert catalog_map["678910"]["clean_name"] == "Gemini Decoded: KS ORG EVOO"
    assert catalog_map["678910"]["category"] == "Бакалея, масла, специи и консервы"
    assert catalog_map["678910"]["classified_by"] == "gemini"

    # 2. Load a second receipt with the SAME Item #678910 -> Gemini must NOT be called again
    receipt_2 = parse_any_costco_payload(
        {
            "transactionBarcode": "R-002",
            "transactionDate": "2026-10-10",
            "total": 24.99,
            "itemArray": [
                {"itemNumber": "678910", "itemDescription01": "KS ORG EVOO", "unit": 1, "amount": 24.99}
            ],
        }
    )
    upsert_2 = db.upsert_receipts(receipt_2)
    assert len(upsert_2["new_items"]) == 0
    classifier.classify_new_items(upsert_2["new_items"])
    assert len(gemini_calls) == 1  # Still exactly 1 call

    # 3. The user manually changes the category of Item #678910
    updated_item = db.update_item_category_manual(
        item_number="678910",
        category="Здоровье, аптека, витамины и гигиена",
        clean_name="Оливковое масло Kirkland (Моё название)",
    )
    assert updated_item is not None
    assert updated_item["is_manual_override"] == 1
    assert updated_item["category"] == "Здоровье, аптека, витамины и гигиена"

    # Stats for both receipts immediately move to the new category
    stats = db.get_statistics(start_date="2026-10-01", end_date="2026-10-31")
    assert len(stats["by_category"]) == 1
    assert stats["by_category"][0]["category"] == "Здоровье, аптека, витамины и гигиена"
    assert stats["by_category"][0]["net_spent"] == 49.98


def test_partial_receipt_does_not_wipe_existing_items(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    full = parse_any_costco_payload(
        {
            "transactionBarcode": "R-PART",
            "transactionDate": "2026-10-01",
            "total": 5.0,
            "itemArray": [{"itemNumber": "42", "itemDescription01": "MILK", "unit": 1, "amount": 5.0}],
        }
    )
    db.upsert_receipts(full)

    # Re-sync: details failed to load, and the receipt from the list has no items
    partial = parse_any_costco_payload({"transactionBarcode": "R-PART", "transactionDate": "2026-10-01", "total": 5.0})
    partial[0]["is_partial"] = True
    result = db.upsert_receipts(partial)

    assert result["skipped_receipts"] == 1
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM receipt_items WHERE transaction_barcode = 'R-PART'").fetchone()[0]
    assert count == 1


def test_gemini_unknown_item_numbers_are_ignored(tmp_path: Path, monkeypatch):
    import json
    import types as pytypes
    from google import genai

    db = Database(tmp_path / "test_costco.db")
    db.upsert_receipts(
        parse_any_costco_payload(
            {
                "transactionBarcode": "R-G",
                "transactionDate": "2026-10-01",
                "itemArray": [{"itemNumber": "123456", "itemDescription01": "KS EVOO", "unit": 1, "amount": 9.0}],
            }
        )
    )

    answer = {
        "items": [
            {"item_number": "0123456 ", "clean_name": "Olive Oil", "category": "Бакалея, масла, специи и консервы"},
            {"item_number": "999999", "clean_name": "Invented", "category": "Прочее"},
        ]
    }

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = pytypes.SimpleNamespace(
                generate_content=lambda **kw: pytypes.SimpleNamespace(text=json.dumps(answer, ensure_ascii=False))
            )

    monkeypatch.setattr(genai, "Client", FakeClient)
    results = GeminiItemClassifier(db)._call_gemini_batch(
        [{"item_number": "123456", "raw_description": "KS EVOO"}], db.get_all_categories(), "key"
    )

    assert [r["item_number"] for r in results] == ["123456"]
    assert results[0]["clean_name"] == "Olive Oil"
