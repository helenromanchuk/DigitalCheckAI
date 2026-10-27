"""Tests for Item # caching in the database, first-time Gemini classification, and manual category changes."""
from pathlib import Path

from app.categorizer.gemini_classifier import GeminiItemClassifier
from app.database import ENGLISH_NAMES_SCHEMA_VERSION, Database
from app.scraper.parser import parse_any_costco_payload


def test_first_time_gemini_classification_and_manual_override(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "test_costco.db")
    gemini_calls: list[list[dict]] = []

    def fake_gemini_batch(self, batch, categories, api_key, **_kwargs):
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
    monkeypatch.setattr("app.categorizer.gemini_classifier._make_gemini_client", lambda api_key: object())
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
            {"id": 0, "clean_name": "Olive Oil", "category": "Бакалея, масла, специи и консервы"},
            {"id": 999, "clean_name": "Invented", "category": "Прочее"},
            {"id": -1, "clean_name": "Negative", "category": "Прочее"},
            {"id": True, "clean_name": "BoolNotInt", "category": "Прочее"},
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


def test_fallback_prefers_exact_names_and_specific_keywords():
    from app.categorizer.gemini_classifier import fallback_categorize_item

    def category(desc):
        return fallback_categorize_item("1", desc)["category"]

    # The exact name from the electronics rule beats "APPLE" from produce
    assert category("APPLE AIRPODS PRO 2ND GEN USB-C") == "Электроника и техника"
    # A longer keyword wins: "AIRPODS" over "APPLE"
    assert category("APPLE AIRPODS MAX") == "Электроника и техника"
    # No substring hits inside other words
    assert category("PRICE MATCH ADJ") == "Прочее"
    # A keyword at the end of a compound names the thing; plurals of short keywords match
    assert category("PINEAPPLE CHUNKS") == "Овощи, фрукты и ягоды"
    assert category("CHEESECAKE") == "Выпечка, хлеб и готовая еда (Deli)"
    assert category("FLATBREAD") == "Выпечка, хлеб и готовая еда (Deli)"
    assert category("FRY PANS 2PK") == "Товары для дома, кухни и сада"
    assert category("TVORT CHEESE") == "Молочные продукты, сыр и яйца"
    # Prefix keywords still work
    assert category("ORG STRAWBERRIES 2LB") == "Овощи, фрукты и ягоды"
    assert category("SUNSCREEN CREAM SPF50") == "Здоровье, аптека, витамины и гигиена"
    assert category("BOUNTY PAPER TOWEL 12CT") == "Бытовая химия, бумага и уборка"


def test_fuel_keeps_parser_category_and_skips_classifier(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    result = db.upsert_receipts(
        parse_any_costco_payload(
            {
                "transactionBarcode": "GAS-1",
                "transactionDate": "2026-10-01",
                "documentType": "FuelReceipt",
                "itemArray": [{"itemNumber": "800877", "itemDescription01": "REGULAR", "unit": 10, "amount": 45.0}],
            }
        )
    )

    assert result["new_items"] == []
    assert db.get_pending_items_for_gemini() == []
    with db.connect() as conn:
        row = conn.execute("SELECT category, classified_by FROM item_catalog WHERE item_number = '800877'").fetchone()
    assert (row["category"], row["classified_by"]) == ("Авто и заправка (Gas Station)", "parser")


def test_existing_pending_fuel_and_orphan_rows_are_not_sent_to_classifier(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    db.upsert_receipts(
        parse_any_costco_payload(
            {
                "transactionBarcode": "GAS-OLD",
                "transactionDate": "2026-10-01",
                "documentType": "FuelReceipt",
                "itemArray": [{"itemNumber": "800877", "itemDescription01": "REGULAR", "unit": 10, "amount": 45.0}],
            }
        )
    )
    with db.connect() as conn:
        # State left by older syncs: fuel saved as pending, and a fee row with no receipt line
        conn.execute("UPDATE item_catalog SET category = 'Прочее', classified_by = 'pending' WHERE item_number = '800877'")
        conn.execute(
            """
            INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
            VALUES ('FEE1', 'DELIVERY FEE', 'DELIVERY FEE', 'Прочее', 0, 'pending', '2026-10-01')
            """
        )
        conn.execute("PRAGMA user_version = 0")

    db.init_db()

    assert db.get_pending_items_for_gemini() == []
    with db.connect() as conn:
        row = conn.execute("SELECT category, classified_by FROM item_catalog WHERE item_number = '800877'").fetchone()
    assert (row["category"], row["classified_by"]) == ("Авто и заправка (Gas Station)", "parser")


def test_resync_moves_pending_row_to_parser_category(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    gas = {
        "transactionBarcode": "GAS-2",
        "transactionDate": "2026-10-02",
        "documentType": "FuelReceipt",
        "itemArray": [{"itemNumber": "800877", "itemDescription01": "REGULAR", "unit": 10, "amount": 45.0}],
    }
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
            VALUES ('800877', 'REGULAR', 'REGULAR', 'Прочее', 0, 'fallback', '2026-10-01')
            """
        )

    db.upsert_receipts(parse_any_costco_payload(gas))

    with db.connect() as conn:
        row = conn.execute("SELECT category, classified_by FROM item_catalog WHERE item_number = '800877'").fetchone()
    assert (row["category"], row["classified_by"]) == ("Авто и заправка (Gas Station)", "parser")


def test_gemini_item_numbers_differing_by_leading_zeros_and_duplicate_ids(tmp_path: Path, monkeypatch):
    import json
    import types as pytypes

    from google import genai

    db = Database(tmp_path / "test_costco.db")
    answer = {
        "items": [
            {"id": 1, "clean_name": "First Draft", "category": "Прочее"},
            {"id": "", "clean_name": "Empty", "category": "Прочее"},
            {"id": 1, "clean_name": "Exact", "category": "Прочее"},
        ]
    }

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = pytypes.SimpleNamespace(
                generate_content=lambda **kw: pytypes.SimpleNamespace(text=json.dumps(answer, ensure_ascii=False))
            )

    monkeypatch.setattr(genai, "Client", FakeClient)
    batch = [
        {"item_number": "0123", "raw_description": "A"},
        {"item_number": "123", "raw_description": "B"},
        {"item_number": "000", "raw_description": "C"},
    ]
    results = GeminiItemClassifier(db)._call_gemini_batch(batch, db.get_all_categories(), "key")

    assert [(r["item_number"], r["clean_name"]) for r in results] == [("123", "Exact")]


def test_migration_unlocks_groceries_an_old_parser_took_for_fuel(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    db.upsert_receipts(
        parse_any_costco_payload(
            {
                "transactionBarcode": "R-OLD-GAS",
                "transactionDate": "2026-10-01",
                "documentType": "WarehouseReceiptDetail",
                "itemArray": [
                    {"itemNumber": "1000001", "itemDescription01": "REGULAR COFFEE", "unit": 1, "amount": 14.99},
                    {"itemNumber": "1000002", "itemDescription01": "MILK", "unit": 1, "amount": 4.59},
                ],
            }
        )
    )
    with db.connect() as conn:
        # What the old parser and the old startup migration left behind
        conn.execute("UPDATE receipts SET receipt_type = 'gas' WHERE transaction_barcode = 'R-OLD-GAS'")
        conn.execute(
            "UPDATE item_catalog SET category = 'Авто и заправка (Gas Station)', clean_name = 'Топливо Costco (x)', "
            "classified_by = 'parser'"
        )
        conn.execute("PRAGMA user_version = 0")

    db.init_db()

    with db.connect() as conn:
        receipt_type = conn.execute("SELECT receipt_type FROM receipts").fetchone()["receipt_type"]
        rows = conn.execute("SELECT category, classified_by FROM item_catalog").fetchall()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert receipt_type == "warehouse"
    assert {(r["category"], r["classified_by"]) for r in rows} == {("Прочее", "pending")}
    assert {i["item_number"] for i in db.get_pending_items_for_gemini()} == {"1000001", "1000002"}
    assert version == ENGLISH_NAMES_SCHEMA_VERSION


def test_unmatched_coupon_does_not_overwrite_product_with_same_number(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    db.save_categorized_items(
        [{"item_number": "1234567", "raw_description": "KS EVOO", "clean_name": "Olive Oil",
          "category": "Бакалея, масла, специи и консервы"}]
    )

    db.upsert_receipts(
        parse_any_costco_payload(
            {
                "transactionBarcode": "R-CPN",
                "transactionDate": "2026-10-01",
                "documentType": "WarehouseReceiptDetail",
                "itemArray": [
                    {"itemNumber": "111", "itemDescription01": "MILK", "unit": 1, "amount": 5.0},
                    {"itemNumber": "1234567", "itemDescription01": "/ 222", "unit": 1, "amount": -2.0},
                ],
            }
        )
    )

    with db.connect() as conn:
        row = conn.execute("SELECT clean_name, category FROM item_catalog WHERE item_number = '1234567'").fetchone()
        coupon = conn.execute("SELECT classified_by FROM item_catalog WHERE item_number = 'DISCOUNT-222'").fetchone()
    assert (row["clean_name"], row["category"]) == ("Olive Oil", "Бакалея, масла, специи и консервы")
    assert coupon["classified_by"] == "parser"


def test_migration_translates_generated_names_to_english(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    with db.connect() as conn:
        for item_number, raw, name, by in (
            ("800877", "REGULAR", "Топливо Costco (REGULAR)", "parser"),
            ("DISCOUNT-222", "/ 222", "Скидка / купон (/ 222)", "parser"),
            ("1", "ORG STRAWBERRIES", "Органическая клубника", "fallback"),
            ("2", "KS EVOO", "Моё название", "manual"),
        ):
            conn.execute(
                """
                INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
                VALUES (?, ?, ?, 'Прочее', ?, ?, '2026-10-01')
                """,
                (item_number, raw, name, int(by == "manual"), by),
            )
        conn.execute("PRAGMA user_version = 1")

    db.init_db()

    with db.connect() as conn:
        names = dict(conn.execute("SELECT item_number, clean_name FROM item_catalog").fetchall())
    assert names == {
        "800877": "Costco Fuel (REGULAR)",
        "DISCOUNT-222": "Coupon (/ 222)",
        "1": "Organic Strawberries",
        "2": "Моё название",
    }


def test_custom_category_english_name_can_be_added_later(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    db.add_custom_category("Питомцы")
    assert db.get_category_labels_en() == {}

    db.add_custom_category("Питомцы", "Pets")
    db.add_custom_category("Питомцы")  # an empty English name keeps the existing one

    assert db.get_category_labels_en() == {"Питомцы": "Pets"}
    assert db.get_all_categories().count("Питомцы") == 1


def test_gemini_prompt_schema_and_safeway_prefix_handling(tmp_path: Path, monkeypatch):
    import json
    import re
    import types as pytypes

    from google import genai
    from google.genai._transformers import t_schema

    from app.categorizer.gemini_classifier import (
        SYSTEM_INSTRUCTION,
        CategorizedItemSchema,
        _build_batch_schema,
        _build_classification_prompt,
    )

    db = Database(tmp_path / "test_costco.db")
    db.add_custom_category("Питомцы", "Pets")
    # Setting an English label on a default category must not override the default label in the prompt
    db.add_custom_category("Прочее", "Misc")
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            def gen(**kw):
                captured.update(kw)
                return pytypes.SimpleNamespace(
                    text=json.dumps(
                        {
                            "items": [
                                {
                                    "id": 0,
                                    "clean_name": "O Organics Broth (32 fl oz)",
                                    "category": "Бакалея, масла, специи и консервы",
                                },
                                {
                                    "id": 1,
                                    "clean_name": "Kirkland Dog Treats (5 lb)",
                                    "category": "Питомцы",
                                },
                                {
                                    "id": 2,
                                    "clean_name": "Mystery Item",
                                    "category": "Nonexistent Category",
                                },
                            ]
                        },
                        ensure_ascii=False,
                    )
                )

            self.models = pytypes.SimpleNamespace(generate_content=gen)

    monkeypatch.setattr(genai, "Client", FakeClient)
    categories = db.get_all_categories()
    batch = [
        {"item_number": "SWY-7989311562", "raw_description": "O ORGNC BROTH 32OZ", "sample_unit_price": 3.49},
        {"item_number": "9001", "raw_description": "KS DOG TREATS 5LB", "sample_unit_price": 19.99},
        {"item_number": "9002", "raw_description": "MYSTERY", "sample_unit_price": 1.00},
    ]

    # Static rules live in SYSTEM_INSTRUCTION (English-only)
    assert "Safeway" in SYSTEM_INSTRUCTION and "SWY-" in SYSTEM_INSTRUCTION
    assert "fl oz" in SYSTEM_INSTRUCTION
    assert "Master Case" in SYSTEM_INSTRUCTION
    assert "Custom user-defined categories take priority" in SYSTEM_INSTRUCTION
    assert not re.search(r"[А-Яа-яЁё]", SYSTEM_INSTRUCTION)
    assert not re.search(r"[А-Яа-яЁё]", json.dumps(CategorizedItemSchema.model_json_schema(), ensure_ascii=False))

    prompt = _build_classification_prompt(batch, categories, db.get_category_labels_en())
    for cat in categories:
        assert cat in prompt
    assert '"id": 0' in prompt and '"id": 1' in prompt and '"id": 2' in prompt
    assert '"english_label": "Pets"' in prompt
    assert '"english_label": "Other"' in prompt
    assert '"english_label": "Misc"' not in prompt

    # Dynamic schema is cached, restricts category to an enum of allowed categories, and keeps its description
    schema_cls = _build_batch_schema(categories)
    assert _build_batch_schema(categories) is schema_cls
    cat_prop = schema_cls.model_json_schema()["$defs"]["DynamicCategorizedItemSchema"]["properties"]["category"]
    assert cat_prop["enum"] == categories
    assert cat_prop["description"] == CategorizedItemSchema.model_fields["category"].description
    genai_cat = t_schema(None, schema_cls).properties["items"].items.properties["category"]
    assert genai_cat.enum == categories
    assert genai_cat.description == CategorizedItemSchema.model_fields["category"].description

    results = GeminiItemClassifier(db)._call_gemini_batch(batch, categories, "key")
    assert captured["config"].system_instruction == SYSTEM_INSTRUCTION
    assert "SWY-7989311562" in captured["contents"]
    assert results == [
        {
            "item_number": "SWY-7989311562",
            "raw_description": "O ORGNC BROTH 32OZ",
            "clean_name": "O Organics Broth (32 fl oz)",
            "category": "Бакалея, масла, специи и консервы",
        },
        {
            "item_number": "9001",
            "raw_description": "KS DOG TREATS 5LB",
            "clean_name": "Kirkland Dog Treats (5 lb)",
            "category": "Питомцы",
        },
        {
            "item_number": "9002",
            "raw_description": "MYSTERY",
            "clean_name": "Mystery Item",
            "category": "Прочее",
        },
    ]


def test_safeway_and_costco_same_digits_never_overwrite_each_other(tmp_path: Path, monkeypatch):
    import json
    import types as pytypes

    from google import genai

    db = Database(tmp_path / "test_costco.db")
    categories = db.get_all_categories()
    batch = [
        {"item_number": "1234", "raw_description": "KS OLIVE OIL"},
        {"item_number": "SWY-1234", "raw_description": "O ORGNC MILK"},
    ]

    def set_answer(items):
        monkeypatch.setattr(
            genai,
            "Client",
            lambda **kw: pytypes.SimpleNamespace(
                models=pytypes.SimpleNamespace(
                    generate_content=lambda **gkw: pytypes.SimpleNamespace(
                        text=json.dumps({"items": items}, ensure_ascii=False)
                    )
                )
            ),
        )

    # 1. Both answered -> both match their respective item
    set_answer(
        [
            {"id": 0, "clean_name": "Kirkland Olive Oil", "category": "Бакалея, масла, специи и консервы"},
            {"id": 1, "clean_name": "O Organics Milk", "category": "Молочные продукты, сыр и яйца"},
        ]
    )
    res_both = GeminiItemClassifier(db)._call_gemini_batch(batch, categories, "key")
    assert [(r["item_number"], r["clean_name"]) for r in res_both] == [
        ("1234", "Kirkland Olive Oil"),
        ("SWY-1234", "O Organics Milk"),
    ]

    # 2. Only Costco (id=0) answered -> Costco item is saved, not discarded
    set_answer([{"id": 0, "clean_name": "Kirkland Olive Oil", "category": "Бакалея, масла, специи и консервы"}])
    res_costco_only = GeminiItemClassifier(db)._call_gemini_batch(batch, categories, "key")
    assert [(r["item_number"], r["clean_name"]) for r in res_costco_only] == [("1234", "Kirkland Olive Oil")]

    # 3. Only Safeway (id=1) answered -> Safeway item is saved, Costco 1234 is untouched
    set_answer([{"id": 1, "clean_name": "O Organics Milk", "category": "Молочные продукты, сыр и яйца"}])
    res_swy_only = GeminiItemClassifier(db)._call_gemini_batch(batch, categories, "key")
    assert [(r["item_number"], r["clean_name"]) for r in res_swy_only] == [("SWY-1234", "O Organics Milk")]


def test_classify_new_items_reuses_client_and_falls_back_on_label_error(tmp_path: Path, monkeypatch):
    import json
    import types as pytypes

    from google import genai

    db = Database(tmp_path / "test_costco.db")
    monkeypatch.setattr("app.categorizer.gemini_classifier.get_gemini_api_key", lambda: "test-key")

    client_creations = 0

    class FakeClient:
        def __init__(self, **kwargs):
            nonlocal client_creations
            client_creations += 1
            self.models = pytypes.SimpleNamespace(
                generate_content=lambda **kw: pytypes.SimpleNamespace(
                    text=json.dumps(
                        {"items": [{"id": 0, "clean_name": "Decoded", "category": "Прочее"}]},
                        ensure_ascii=False,
                    )
                )
            )

    monkeypatch.setattr(genai, "Client", FakeClient)
    items = [
        {"item_number": "101", "raw_description": "KS ORG EVOO"},
        {"item_number": "102", "raw_description": "KERRYGOLD BUTTER"},
    ]
    classifier = GeminiItemClassifier(db, batch_size=1)
    res = classifier.classify_new_items(items)
    assert res == {"classified_count": 2, "provider": "gemini"}
    assert client_creations == 1

    # If label lookup fails on a busy DB, classify_new_items falls back instead of raising
    def boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "get_category_labels_en", boom)
    res_fb = classifier.classify_new_items([{"item_number": "103", "raw_description": "KS ORG EVOO"}])
    assert res_fb["classified_count"] == 1
    assert db.get_existing_catalog_map(["103"])["103"]["classified_by"] == "fallback"


def test_config_api_returns_merged_english_category_labels(tmp_path: Path):
    from fastapi.testclient import TestClient

    from app.api.routes import get_db
    from app.config import BASE_DIR, DEFAULT_CATEGORIES, DEFAULT_CATEGORY_LABELS_EN
    from app.main import app

    i18n_js = (BASE_DIR / "static" / "i18n.js").read_text(encoding="utf-8")
    assert "CATEGORY_LABELS_EN" not in i18n_js
    assert list(DEFAULT_CATEGORY_LABELS_EN) == DEFAULT_CATEGORIES

    test_db = Database(tmp_path / "config_labels.db")
    app.dependency_overrides[get_db] = lambda: test_db
    try:
        client = TestClient(app)
        res = client.get("/api/config")
        assert res.status_code == 200
        assert res.json()["category_labels_en"] == DEFAULT_CATEGORY_LABELS_EN

        # Custom category label is added, while default category label cannot be overwritten
        client.post("/api/categories", json={"name": "Питомцы", "name_en": "Pets"})
        res_override = client.post("/api/categories", json={"name": "Прочее", "name_en": "Misc"})
        assert res_override.status_code == 200
        labels = res_override.json()["category_labels_en"]
        assert labels["Питомцы"] == "Pets"
        assert labels["Прочее"] == "Other"
    finally:
        app.dependency_overrides.clear()


def test_gemini_request_and_response_logged_at_debug(tmp_path: Path, caplog):
    import json
    import logging
    import types as pytypes

    db = Database(tmp_path / "test_costco.db")
    answer = {"items": [{"id": 0, "clean_name": "Kirkland Olive Oil", "category": "Прочее"}]}
    usage = pytypes.SimpleNamespace(prompt_token_count=120, candidates_token_count=30)
    client = pytypes.SimpleNamespace(
        models=pytypes.SimpleNamespace(
            generate_content=lambda **kw: pytypes.SimpleNamespace(
                text=json.dumps(answer, ensure_ascii=False), usage_metadata=usage
            )
        )
    )
    batch = [{"item_number": "1234", "raw_description": "KS ORG EVOO", "sample_unit_price": 9.99}]

    with caplog.at_level(logging.DEBUG, logger="app.categorizer.gemini_classifier"):
        GeminiItemClassifier(db)._call_gemini_batch(batch, db.get_all_categories(), "key", {}, client=client)

    log = caplog.text
    assert "KS ORG EVOO" in log
    assert "Kirkland Olive Oil" in log
    assert "120 in / 30 out" in log
