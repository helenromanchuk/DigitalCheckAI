"""Тесты кэширования Item# в БД, первичной классификации через Gemini API и ручного изменения категории."""
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

    # 1. Первый раз загружаем чек с Item #678910 -> он должен попасть в new_items и отправиться в Gemini
    upsert_1 = db.upsert_receipts(receipt_1)
    assert len(upsert_1["new_items"]) == 1
    assert upsert_1["new_items"][0]["item_number"] == "678910"

    classifier = GeminiItemClassifier(db)
    cls_res_1 = classifier.classify_new_items(upsert_1["new_items"])
    assert cls_res_1["provider"] == "gemini"
    assert cls_res_1["classified_count"] == 1
    assert len(gemini_calls) == 1

    # Проверяем, что расшифровка и категория сохранились в локальной БД
    catalog_map = db.get_existing_catalog_map(["678910"])
    assert catalog_map["678910"]["clean_name"] == "Gemini Decoded: KS ORG EVOO"
    assert catalog_map["678910"]["category"] == "Бакалея, масла, специи и консервы"
    assert catalog_map["678910"]["classified_by"] == "gemini"

    # 2. Загружаем второй чек с ТЕМ ЖЕ Item #678910 -> повторного вызова Gemini быть НЕ должно!
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
    assert len(gemini_calls) == 1  # Всё ещё ровно 1 вызов!

    # 3. Пользователь вручную меняет категорию для Item #678910
    updated_item = db.update_item_category_manual(
        item_number="678910",
        category="Здоровье, аптека, витамины и гигиена",
        clean_name="Оливковое масло Kirkland (Моё название)",
    )
    assert updated_item is not None
    assert updated_item["is_manual_override"] == 1
    assert updated_item["category"] == "Здоровье, аптека, витамины и гигиена"

    # Проверяем, что статистика по обоим чекам сразу пересчиталась на новую категорию
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

    # Повторная синхронизация: детали не загрузились, в чеке из списка нет позиций
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
