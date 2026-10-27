"""Tests for Item # caching in the database, first-time Gemini classification, and manual category changes."""
from pathlib import Path

from app.categorizer.gemini_classifier import GeminiItemClassifier
from app.database import ENGLISH_NAMES_SCHEMA_VERSION, Database
from app.scraper.parser import parse_any_costco_payload


def test_first_time_gemini_classification_and_manual_override(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "test_costco.db")
    gemini_calls: list[list[dict]] = []

    def fake_gemini_batch(self, batch, categories, client, custom_labels_en=None):
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


def test_gemini_unknown_item_numbers_are_ignored(tmp_path: Path):
    import json
    import types as pytypes

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
            {
                "id": 0,
                "item_number": "0123456 ",
                "clean_name": "Olive Oil",
                "category": "Бакалея, масла, специи и консервы",
            },
            # Wrong item_number for id=0 (e.g. if Gemini skipped an item and shifted neighbors) must be rejected
            {"id": 0, "item_number": "999999", "clean_name": "ShiftedNeighbor", "category": "Прочее"},
            {"id": 999, "item_number": "123456", "clean_name": "OutOfRangeId", "category": "Прочее"},
            {"id": -1, "item_number": "123456", "clean_name": "NegativeId", "category": "Прочее"},
            {"id": True, "item_number": "123456", "clean_name": "BoolNotInt", "category": "Прочее"},
        ]
    }
    fake_client = pytypes.SimpleNamespace(
        models=pytypes.SimpleNamespace(
            generate_content=lambda **kw: pytypes.SimpleNamespace(text=json.dumps(answer, ensure_ascii=False))
        )
    )

    results = GeminiItemClassifier(db)._call_gemini_batch(
        [{"item_number": "123456", "raw_description": "KS EVOO"}],
        db.get_all_categories(),
        fake_client,
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


def test_gemini_item_numbers_differing_by_leading_zeros_and_duplicate_ids(tmp_path: Path):
    import json
    import types as pytypes

    db = Database(tmp_path / "test_costco.db")
    answer = {
        "items": [
            # Swapped item_number ('123' at id=0 where '0123' is expected) is rejected because '123' is in the batch
            {"id": 0, "item_number": "123", "clean_name": "Swapped", "category": "Прочее"},
            {"id": 1, "item_number": "123", "clean_name": "First Draft", "category": "Прочее"},
            {"id": "", "item_number": "123", "clean_name": "Empty", "category": "Прочее"},
            # Duplicate answer for id=1 keeps the last answer, and preserves category with trailing space
            {"id": 1, "item_number": "123", "clean_name": "Exact", "category": "Категория с пробелом "},
        ]
    }
    fake_client = pytypes.SimpleNamespace(
        models=pytypes.SimpleNamespace(
            generate_content=lambda **kw: pytypes.SimpleNamespace(text=json.dumps(answer, ensure_ascii=False))
        )
    )
    batch = [
        {"item_number": "0123", "raw_description": "A"},
        {"item_number": "123", "raw_description": "B"},
        {"item_number": "000", "raw_description": "C"},
    ]
    categories = [*db.get_all_categories(), "Категория с пробелом "]
    results = GeminiItemClassifier(db)._call_gemini_batch(batch, categories, fake_client)

    assert [(r["item_number"], r["clean_name"], r["category"]) for r in results] == [
        ("123", "Exact", "Категория с пробелом ")
    ]


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


def test_gemini_prompt_schema_and_safeway_prefix_handling(tmp_path: Path):
    import json
    import re
    import types as pytypes

    from google.genai._transformers import t_schema

    from app.categorizer.gemini_classifier import (
        SYSTEM_INSTRUCTION,
        CategorizedItemSchema,
        _build_batch_schema,
        _build_classification_prompt,
    )

    db = Database(tmp_path / "test_costco.db")
    db.add_custom_category("Питомцы", "Pets")
    captured: dict = {}

    def gen(**kw):
        captured.update(kw)
        return pytypes.SimpleNamespace(
            text=json.dumps(
                {
                    "items": [
                        {
                            "id": 0,
                            "item_number": "7989311562",
                            "clean_name": "O Organics Broth (32 fl oz)",
                            "category": "Бакалея, масла, специи и консервы",
                        },
                        {
                            "id": 1,
                            "item_number": "9001",
                            "clean_name": "Kirkland Dog Treats (5 lb)",
                            "category": "Питомцы",
                        },
                        {
                            "id": 2,
                            "item_number": "9002",
                            "clean_name": "Mystery Item",
                            "category": "Nonexistent Category",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )

    fake_client = pytypes.SimpleNamespace(models=pytypes.SimpleNamespace(generate_content=gen))
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

    # A label on a default category (stored before such labels were rejected) must not override the default
    custom_labels_en = {**db.get_category_labels_en(), "Прочее": "Misc"}
    prompt = _build_classification_prompt(batch, categories, custom_labels_en)
    for cat in categories:
        assert cat in prompt
    assert '"id":0' in prompt and '"id":1' in prompt and '"id":2' in prompt
    assert '"english_label":"Pets"' in prompt
    assert '"english_label":"Other"' in prompt
    assert '"english_label":"Misc"' not in prompt

    # Dynamic schema is cached, restricts category to an enum of allowed categories, and keeps its description
    schema_cls = _build_batch_schema(tuple(categories))
    assert _build_batch_schema(tuple(categories)) is schema_cls
    cat_prop = schema_cls.model_json_schema()["$defs"]["DynamicCategorizedItemSchema"]["properties"]["category"]
    assert cat_prop["enum"] == categories
    assert cat_prop["description"] == CategorizedItemSchema.model_fields["category"].description
    genai_cat = t_schema(None, schema_cls).properties["items"].items.properties["category"]
    assert genai_cat.enum == categories
    assert genai_cat.description == CategorizedItemSchema.model_fields["category"].description

    results = GeminiItemClassifier(db)._call_gemini_batch(batch, categories, fake_client, custom_labels_en)
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


def test_safeway_and_costco_same_digits_never_overwrite_each_other(tmp_path: Path):
    import json
    import types as pytypes

    db = Database(tmp_path / "test_costco.db")
    categories = db.get_all_categories()
    batch = [
        {"item_number": "1234", "raw_description": "KS OLIVE OIL"},
        {"item_number": "SWY-1234", "raw_description": "O ORGNC MILK"},
    ]

    def make_client(items):
        return pytypes.SimpleNamespace(
            models=pytypes.SimpleNamespace(
                generate_content=lambda **gkw: pytypes.SimpleNamespace(
                    text=json.dumps({"items": items}, ensure_ascii=False)
                )
            )
        )

    # 1. Both answered -> both match their respective item
    res_both = GeminiItemClassifier(db)._call_gemini_batch(
        batch,
        categories,
        make_client(
            [
                {
                    "id": 0,
                    "item_number": "01234",
                    "clean_name": "Kirkland Olive Oil",
                    "category": "Бакалея, масла, специи и консервы",
                },
                {
                    "id": 1,
                    "item_number": "swy-01234",
                    "clean_name": "O Organics Milk",
                    "category": "Молочные продукты, сыр и яйца",
                },
            ]
        ),
    )
    assert [(r["item_number"], r["clean_name"]) for r in res_both] == [
        ("1234", "Kirkland Olive Oil"),
        ("SWY-1234", "O Organics Milk"),
    ]

    # 2. Only Costco (id=0) answered -> Costco item is saved, not discarded
    res_costco_only = GeminiItemClassifier(db)._call_gemini_batch(
        batch,
        categories,
        make_client(
            [
                {
                    "id": 0,
                    "item_number": "1234",
                    "clean_name": "Kirkland Olive Oil",
                    "category": "Бакалея, масла, специи и консервы",
                }
            ]
        ),
    )
    assert [(r["item_number"], r["clean_name"]) for r in res_costco_only] == [("1234", "Kirkland Olive Oil")]

    # 3. Swapped or dropped-prefix collision in a batch with both 1234 and SWY-1234 is rejected
    res_swapped = GeminiItemClassifier(db)._call_gemini_batch(
        batch,
        categories,
        make_client(
            [
                {"id": 0, "item_number": "SWY-1234", "clean_name": "Wrong", "category": "Прочее"},
                {"id": 1, "item_number": "1234", "clean_name": "Ambiguous", "category": "Прочее"},
            ]
        ),
    )
    assert res_swapped == []


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

            def gen(**kw):
                payload = json.loads(kw["contents"].split("Items to decode and categorize:\n", 1)[1])
                return pytypes.SimpleNamespace(
                    text=json.dumps(
                        {
                            "items": [
                                {
                                    "id": p["id"],
                                    "item_number": p["item_number"],
                                    "clean_name": "Decoded",
                                    "category": "Прочее",
                                }
                                for p in payload
                            ]
                        },
                        ensure_ascii=False,
                    )
                )

            self.models = pytypes.SimpleNamespace(generate_content=gen)

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


def test_config_api_serves_category_labels_and_reserves_default_names(tmp_path: Path):
    from fastapi.testclient import TestClient

    from app.api.routes import get_db
    from app.config import DEFAULT_CATEGORIES, DEFAULT_CATEGORY_LABELS_EN
    from app.main import app

    assert list(DEFAULT_CATEGORY_LABELS_EN) == DEFAULT_CATEGORIES

    test_db = Database(tmp_path / "config_labels.db")
    app.dependency_overrides[get_db] = lambda: test_db
    try:
        client = TestClient(app)
        res = client.get("/api/config")
        assert res.status_code == 200
        assert res.json()["category_labels_en"] == DEFAULT_CATEGORY_LABELS_EN

        res_custom = client.post("/api/categories", json={"name": "Питомцы", "name_en": "Pets"})
        assert res_custom.status_code == 200
        assert res_custom.json()["category_labels_en"]["Питомцы"] == "Pets"

        # Default names and their English labels can't be reused: the label would be ignored,
        # or a custom "Other" would compete with "Прочее" in the Gemini prompt
        for payload in (
            {"name": "Прочее", "name_en": "Misc"},
            {"name": "other"},
            {"name": "Вино", "name_en": "Alcohol"},
        ):
            res = client.post("/api/categories", json=payload)
            assert res.status_code == 400, payload
            assert res.json()["detail"]["code"] == "category_reserved"
        labels = client.get("/api/config").json()["category_labels_en"]
        assert labels["Прочее"] == "Other"
        assert "Вино" not in labels

        # Re-entering a custom category to change its translation is allowed,
        # but another category can't take its English label or name
        assert client.post("/api/categories", json={"name": "Питомцы", "name_en": "Pet supplies"}).status_code == 200
        for payload in ({"name": "Зоотовары", "name_en": "pet supplies"}, {"name": "Pet Supplies"}):
            res = client.post("/api/categories", json=payload)
            assert res.status_code == 400, payload
            assert res.json()["detail"]["code"] == "category_reserved"
    finally:
        app.dependency_overrides.clear()


def _fake_gemini_client(answer: dict, usage=None):
    import json
    import types as pytypes

    response = pytypes.SimpleNamespace(text=json.dumps(answer, ensure_ascii=False), usage_metadata=usage)
    return pytypes.SimpleNamespace(models=pytypes.SimpleNamespace(generate_content=lambda **kw: response))


def test_gemini_request_and_response_logged_at_debug(tmp_path: Path, caplog):
    import logging
    import types as pytypes

    db = Database(tmp_path / "test_costco.db")
    answer = {"items": [{"id": 0, "item_number": "1234", "clean_name": "Kirkland Olive Oil", "category": "Прочее"}]}
    usage = pytypes.SimpleNamespace(prompt_token_count=120, candidates_token_count=30)
    batch = [{"item_number": "1234", "raw_description": "KS ORG EVOO", "sample_unit_price": 9.99}]

    with caplog.at_level(logging.DEBUG, logger="app.categorizer.gemini_classifier"):
        GeminiItemClassifier(db)._call_gemini_batch(
            batch, db.get_all_categories(), _fake_gemini_client(answer, usage), {}
        )

    assert "KS ORG EVOO" in caplog.text
    assert "Kirkland Olive Oil" in caplog.text
    assert "120 in / 30 out" in caplog.text


def test_items_dropped_from_gemini_response_get_fallback(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "test_costco.db")
    items = [
        {"item_number": "1001", "raw_description": "KS ORG EVOO", "sample_unit_price": 9.99},
        {"item_number": "1002", "raw_description": "TIDE PODS 152CT", "sample_unit_price": 29.99},
    ]
    # Gemini answers item 0 correctly and returns a wrong item_number for item 1
    answer = {
        "items": [
            {"id": 0, "item_number": "1001", "clean_name": "Kirkland Olive Oil", "category": "Прочее"},
            {"id": 1, "item_number": "9999", "clean_name": "Wrong Item", "category": "Прочее"},
        ]
    }
    saved: list[tuple[list[str], str]] = []
    monkeypatch.setattr("app.categorizer.gemini_classifier.get_gemini_api_key", lambda: "test-key-123")
    monkeypatch.setattr(
        "app.categorizer.gemini_classifier._make_gemini_client", lambda api_key: _fake_gemini_client(answer)
    )
    monkeypatch.setattr(
        db,
        "save_categorized_items",
        lambda results, classified_by="gemini": saved.append(([r["item_number"] for r in results], classified_by))
        or len(results),
    )

    result = GeminiItemClassifier(db).classify_new_items(items)

    assert saved == [(["1001"], "gemini"), (["1002"], "fallback")]
    assert result["classified_count"] == 2


def test_gemini_setup_failure_falls_back_once(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "test_costco.db")
    items = [{"item_number": str(1000 + i), "raw_description": "KS ORG EVOO"} for i in range(5)]
    client_calls: list[str] = []

    def broken_client(api_key):
        client_calls.append(api_key)
        raise RuntimeError("bad client")

    monkeypatch.setattr("app.categorizer.gemini_classifier.get_gemini_api_key", lambda: "test-key-123")
    monkeypatch.setattr("app.categorizer.gemini_classifier._make_gemini_client", broken_client)

    result = GeminiItemClassifier(db, batch_size=2).classify_new_items(items)

    assert client_calls == ["test-key-123"]
    assert result["provider"] == "fallback"


def test_startup_merges_custom_categories_clashing_with_defaults(tmp_path: Path):
    db_path = tmp_path / "test_costco.db"
    db = Database(db_path)
    # Rows saved before names were checked, written directly to skip add_custom_category
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO custom_categories (name, name_en, created_at) VALUES (?, ?, '2026-10-01')",
            [("Other", ""), ("Вино", "Alcohol"), ("Прочее", "Misc"), (" ", ""), ("Питомцы", "Pets")],
        )
        conn.executemany(
            """
            INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
            VALUES (?, ?, ?, ?, 1, 'manual', '2026-10-01')
            """,
            [("1", "LEGO", "LEGO", "Other"), ("2", "WINE", "Wine", "Вино"), ("3", "X", "X", " ")],
        )

    db = Database(db_path)

    categories = db.get_all_categories()
    assert "Other" not in categories and " " not in categories
    assert "Вино" in categories and "Питомцы" in categories
    assert db.get_category_labels_en() == {"Питомцы": "Pets"}
    with db.connect() as conn:
        rows = dict(conn.execute("SELECT item_number, category FROM item_catalog").fetchall())
    assert rows == {"1": "Прочее", "2": "Вино", "3": "Прочее"}


def test_blank_category_names_are_rejected(tmp_path: Path):
    import pytest

    from app.errors import AppError

    db = Database(tmp_path / "test_costco.db")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
            VALUES ('5555', 'DOG FOOD', 'Dog Food', 'Прочее', 0, 'fallback', '2026-10-01')
            """
        )
    for call in (lambda: db.add_custom_category("   "), lambda: db.update_item_category_manual("5555", "  ")):
        with pytest.raises(AppError) as exc_info:
            call()
        assert exc_info.value.code == "category_empty"
    assert "" not in db.get_all_categories()


def test_duplicate_gemini_ids_are_logged(tmp_path: Path, caplog):
    db = Database(tmp_path / "test_costco.db")
    answer = {
        "items": [
            {"id": 0, "item_number": "1234", "clean_name": "First", "category": "Прочее"},
            {"id": 0, "item_number": "1234", "clean_name": "Second", "category": "Алкоголь"},
        ]
    }
    batch = [{"item_number": "1234", "raw_description": "KS ORG EVOO"}]
    results = GeminiItemClassifier(db)._call_gemini_batch(batch, db.get_all_categories(), _fake_gemini_client(answer), {})
    assert [r["clean_name"] for r in results] == ["Second"]
    assert "id=0" in caplog.text


def test_category_names_are_checked_in_database(tmp_path: Path):
    import pytest

    from app.errors import AppError

    db = Database(tmp_path / "test_costco.db")
    db.add_custom_category("Питомцы", "Pets")

    # Re-entering in another letter case updates the existing category instead of failing or duplicating it
    categories = db.add_custom_category("питомцы", "Pet supplies")
    assert "питомцы" not in categories
    assert db.get_category_labels_en()["Питомцы"] == "Pet supplies"

    for name, name_en in (("Other", ""), ("Прочее", "Misc"), ("Pet Supplies", ""), ("Зоотовары", "PET SUPPLIES")):
        with pytest.raises(AppError) as exc_info:
            db.add_custom_category(name, name_en)
        assert exc_info.value.code == "category_reserved"


def test_manual_category_edit_reuses_existing_category(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    db.add_custom_category("Питомцы", "Pets")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
            VALUES ('5555', 'DOG FOOD', 'Dog Food', 'Прочее', 0, 'fallback', '2026-10-01')
            """
        )

    assert db.update_item_category_manual("5555", "other")["category"] == "Прочее"
    assert db.update_item_category_manual("5555", "PETS")["category"] == "Питомцы"
    categories = db.get_all_categories()
    assert "other" not in categories and "PETS" not in categories


def test_duplicate_id_warning_skips_rejected_answers(tmp_path: Path, caplog):
    db = Database(tmp_path / "test_costco.db")
    answer = {
        "items": [
            {"id": 0, "item_number": "1234", "clean_name": "Right", "category": "Прочее"},
            {"id": 0, "item_number": "5678", "clean_name": "Wrong", "category": "Прочее"},
        ]
    }
    batch = [
        {"item_number": "1234", "raw_description": "A"},
        {"item_number": "5678", "raw_description": "B"},
    ]
    results = GeminiItemClassifier(db)._call_gemini_batch(batch, db.get_all_categories(), _fake_gemini_client(answer), {})
    assert [r["clean_name"] for r in results] == ["Right"]
    assert "повторный" not in caplog.text
