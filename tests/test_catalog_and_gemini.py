"""Tests for Item # caching in the database, first-time Gemini classification, and manual category changes."""
from pathlib import Path

from app.categorizer.gemini_classifier import GeminiItemClassifier
from app.database import TAX_SPLIT_SCHEMA_VERSION, Database
from app.scraper.parser import parse_any_costco_payload


def test_first_time_gemini_classification_and_manual_override(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "test_costco.db")
    gemini_calls: list[list[dict]] = []

    def fake_gemini_batch(self, batch, categories, client):
        gemini_calls.append(batch)
        return [
            {
                "item_number": item["item_number"],
                "raw_description": item["raw_description"],
                "clean_name": f"Gemini Decoded: {item['raw_description']}",
                "category": "Pantry, oils, spices & canned goods",
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
    assert catalog_map["678910"]["category"] == "Pantry, oils, spices & canned goods"
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
        category="Health, pharmacy, vitamins & personal care",
        clean_name="Оливковое масло Kirkland (Моё название)",
    )
    assert updated_item is not None
    assert updated_item["is_manual_override"] == 1
    assert updated_item["category"] == "Health, pharmacy, vitamins & personal care"

    # Stats for both receipts immediately move to the new category
    stats = db.get_statistics(start_date="2026-10-01", end_date="2026-10-31")
    assert len(stats["by_category"]) == 1
    assert stats["by_category"][0]["category"] == "Health, pharmacy, vitamins & personal care"
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
                "category": "Pantry, oils, spices & canned goods",
            },
            # Wrong item_number for id=0 (e.g. if Gemini skipped an item and shifted neighbors) must be rejected
            {"id": 0, "item_number": "999999", "clean_name": "ShiftedNeighbor", "category": "Other"},
            {"id": 999, "item_number": "123456", "clean_name": "OutOfRangeId", "category": "Other"},
            {"id": -1, "item_number": "123456", "clean_name": "NegativeId", "category": "Other"},
            {"id": True, "item_number": "123456", "clean_name": "BoolNotInt", "category": "Other"},
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
    assert category("APPLE AIRPODS PRO 2ND GEN USB-C") == "Electronics & appliances"
    # A longer keyword wins: "AIRPODS" over "APPLE"
    assert category("APPLE AIRPODS MAX") == "Electronics & appliances"
    # No substring hits inside other words
    assert category("PRICE MATCH ADJ") == "Other"
    # A keyword at the end of a compound names the thing; plurals of short keywords match
    assert category("PINEAPPLE CHUNKS") == "Fruits, vegetables & berries"
    assert category("CHEESECAKE") == "Bakery, bread & prepared food (Deli)"
    assert category("FLATBREAD") == "Bakery, bread & prepared food (Deli)"
    assert category("FRY PANS 2PK") == "Home, kitchen & garden"
    assert category("TVORT CHEESE") == "Dairy, cheese & eggs"
    # Prefix keywords still work
    assert category("ORG STRAWBERRIES 2LB") == "Fruits, vegetables & berries"
    assert category("SUNSCREEN CREAM SPF50") == "Health, pharmacy, vitamins & personal care"
    assert category("BOUNTY PAPER TOWEL 12CT") == "Household, paper & cleaning"


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
    assert (row["category"], row["classified_by"]) == ("Auto & fuel (Gas Station)", "parser")


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
        conn.execute("UPDATE item_catalog SET category = 'Other', classified_by = 'pending' WHERE item_number = '800877'")
        conn.execute(
            """
            INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
            VALUES ('FEE1', 'DELIVERY FEE', 'DELIVERY FEE', 'Other', 0, 'pending', '2026-10-01')
            """
        )
        conn.execute("PRAGMA user_version = 0")

    db.init_db()

    assert db.get_pending_items_for_gemini() == []
    with db.connect() as conn:
        row = conn.execute("SELECT category, classified_by FROM item_catalog WHERE item_number = '800877'").fetchone()
    assert (row["category"], row["classified_by"]) == ("Auto & fuel (Gas Station)", "parser")


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
            VALUES ('800877', 'REGULAR', 'REGULAR', 'Other', 0, 'fallback', '2026-10-01')
            """
        )

    db.upsert_receipts(parse_any_costco_payload(gas))

    with db.connect() as conn:
        row = conn.execute("SELECT category, classified_by FROM item_catalog WHERE item_number = '800877'").fetchone()
    assert (row["category"], row["classified_by"]) == ("Auto & fuel (Gas Station)", "parser")


def test_gemini_item_numbers_differing_by_leading_zeros_and_duplicate_ids(tmp_path: Path):
    import json
    import types as pytypes

    db = Database(tmp_path / "test_costco.db")
    answer = {
        "items": [
            # Swapped item_number ('123' at id=0 where '0123' is expected) is rejected because '123' is in the batch
            {"id": 0, "item_number": "123", "clean_name": "Swapped", "category": "Other"},
            {"id": 1, "item_number": "123", "clean_name": "First Draft", "category": "Other"},
            {"id": "", "item_number": "123", "clean_name": "Empty", "category": "Other"},
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
            "UPDATE item_catalog SET category = 'Auto & fuel (Gas Station)', clean_name = 'Топливо Costco (x)', "
            "classified_by = 'parser'"
        )
        conn.execute("PRAGMA user_version = 0")

    db.init_db()

    with db.connect() as conn:
        receipt_type = conn.execute("SELECT receipt_type FROM receipts").fetchone()["receipt_type"]
        rows = conn.execute("SELECT category, classified_by FROM item_catalog").fetchall()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert receipt_type == "warehouse"
    assert {(r["category"], r["classified_by"]) for r in rows} == {("Other", "pending")}
    assert {i["item_number"] for i in db.get_pending_items_for_gemini()} == {"1000001", "1000002"}
    assert version == TAX_SPLIT_SCHEMA_VERSION


def test_unmatched_coupon_does_not_overwrite_product_with_same_number(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    db.save_categorized_items(
        [{"item_number": "1234567", "raw_description": "KS EVOO", "clean_name": "Olive Oil",
          "category": "Pantry, oils, spices & canned goods"}]
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
    assert (row["clean_name"], row["category"]) == ("Olive Oil", "Pantry, oils, spices & canned goods")
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
                VALUES (?, ?, ?, 'Other', ?, ?, '2026-10-01')
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


def test_custom_category_russian_label_can_be_added_later(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    from app.config import DEFAULT_CATEGORY_LABELS_RU

    db.add_custom_category("Pets")
    assert db.get_category_labels_ru() == DEFAULT_CATEGORY_LABELS_RU

    db.add_custom_category("Pets", "Питомцы")
    db.add_custom_category("Pets")  # an empty Russian label keeps the existing one

    assert db.get_category_labels_ru() == {**DEFAULT_CATEGORY_LABELS_RU, "Pets": "Питомцы"}
    assert db.get_all_categories().count("Pets") == 1


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
    db.add_custom_category("Pets", "Питомцы")
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
                            "category": "Pantry, oils, spices & canned goods",
                        },
                        {
                            "id": 1,
                            "item_number": "9001",
                            "clean_name": "Kirkland Dog Treats (5 lb)",
                            "category": "Pets",
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

    # The per-batch prompt is English-only too: categories go by their English names, Russian labels stay out
    prompt = _build_classification_prompt(batch, categories)
    for cat in categories:
        assert cat in prompt
    assert '"id":0' in prompt and '"id":1' in prompt and '"id":2' in prompt
    assert not re.search(r"[А-Яа-яЁё]", prompt)

    # Dynamic schema is cached, restricts category to an enum of allowed categories, and keeps its description
    schema_cls = _build_batch_schema(tuple(categories))
    assert _build_batch_schema(tuple(categories)) is schema_cls
    cat_prop = schema_cls.model_json_schema()["$defs"]["DynamicCategorizedItemSchema"]["properties"]["category"]
    assert cat_prop["enum"] == categories
    assert cat_prop["description"] == CategorizedItemSchema.model_fields["category"].description
    genai_cat = t_schema(None, schema_cls).properties["items"].items.properties["category"]
    assert genai_cat.enum == categories
    assert genai_cat.description == CategorizedItemSchema.model_fields["category"].description

    results = GeminiItemClassifier(db)._call_gemini_batch(batch, categories, fake_client)
    assert captured["config"].system_instruction == SYSTEM_INSTRUCTION
    assert "SWY-7989311562" in captured["contents"]
    assert results == [
        {
            "item_number": "SWY-7989311562",
            "raw_description": "O ORGNC BROTH 32OZ",
            "clean_name": "O Organics Broth (32 fl oz)",
            "category": "Pantry, oils, spices & canned goods",
        },
        {
            "item_number": "9001",
            "raw_description": "KS DOG TREATS 5LB",
            "clean_name": "Kirkland Dog Treats (5 lb)",
            "category": "Pets",
        },
        {
            "item_number": "9002",
            "raw_description": "MYSTERY",
            "clean_name": "Mystery Item",
            "category": "Other",
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
                    "category": "Pantry, oils, spices & canned goods",
                },
                {
                    "id": 1,
                    "item_number": "swy-01234",
                    "clean_name": "O Organics Milk",
                    "category": "Dairy, cheese & eggs",
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
                    "category": "Pantry, oils, spices & canned goods",
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
                {"id": 0, "item_number": "SWY-1234", "clean_name": "Wrong", "category": "Other"},
                {"id": 1, "item_number": "1234", "clean_name": "Ambiguous", "category": "Other"},
            ]
        ),
    )
    assert res_swapped == []


def test_classify_new_items_reuses_client_and_skips_non_english_categories(tmp_path: Path, monkeypatch, caplog):
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
                                    "category": "Other",
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

    # A custom category left without an English name by an old database is not sent to Gemini
    with db.connect() as conn:
        conn.execute("INSERT INTO custom_categories (name, name_ru, created_at) VALUES ('Подарки', '', '2026-10-01')")
    sent: list[list[str]] = []
    monkeypatch.setattr(
        GeminiItemClassifier,
        "_call_gemini_batch",
        lambda self, batch, categories, client: sent.append(categories) or [],
    )
    classifier.classify_new_items([{"item_number": "103", "raw_description": "KS ORG EVOO"}])
    assert sent and "Подарки" not in sent[0] and "Other" in sent[0]
    assert "Подарки" in caplog.text


def test_config_api_serves_category_labels_and_reserves_default_names(tmp_path: Path):
    from fastapi.testclient import TestClient

    from app.api.routes import get_db
    from app.config import DEFAULT_CATEGORIES, DEFAULT_CATEGORY_LABELS_RU
    from app.main import app

    assert list(DEFAULT_CATEGORY_LABELS_RU) == DEFAULT_CATEGORIES

    test_db = Database(tmp_path / "config_labels.db")
    app.dependency_overrides[get_db] = lambda: test_db
    try:
        client = TestClient(app)
        res = client.get("/api/config")
        assert res.status_code == 200
        assert res.json()["categories"][: len(DEFAULT_CATEGORIES)] == DEFAULT_CATEGORIES
        assert res.json()["category_labels_ru"] == DEFAULT_CATEGORY_LABELS_RU

        res_custom = client.post("/api/categories", json={"name": "Pets", "name_ru": "Питомцы"})
        assert res_custom.status_code == 200
        assert res_custom.json()["category_labels_ru"]["Pets"] == "Питомцы"

        # Default names and their Russian labels can't be reused: the label would be ignored,
        # or a custom "other" would compete with "Other" in the Gemini prompt
        for payload in (
            {"name": "Other", "name_ru": "Разное"},
            {"name": "other"},
            {"name": "Wine", "name_ru": "Алкоголь"},
        ):
            res = client.post("/api/categories", json=payload)
            assert res.status_code == 400, payload
            assert res.json()["detail"]["code"] == "category_reserved"
        labels = client.get("/api/config").json()["category_labels_ru"]
        assert labels["Other"] == "Прочее"
        assert "Wine" not in labels

        # The name goes to Gemini, so it must be English; Russian belongs in name_ru
        res = client.post("/api/categories", json={"name": "Подарки"})
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "category_not_english"

        # Re-entering a custom category to change its translation is allowed,
        # but another category can't take its Russian label or name
        assert client.post("/api/categories", json={"name": "Pets", "name_ru": "Зоотовары"}).status_code == 200
        for payload in ({"name": "Pet food", "name_ru": "зоотовары"}, {"name": "PETS food", "name_ru": "Pets"}):
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
    answer = {"items": [{"id": 0, "item_number": "1234", "clean_name": "Kirkland Olive Oil", "category": "Other"}]}
    usage = pytypes.SimpleNamespace(prompt_token_count=120, candidates_token_count=30)
    batch = [{"item_number": "1234", "raw_description": "KS ORG EVOO", "sample_unit_price": 9.99}]

    with caplog.at_level(logging.DEBUG, logger="app.categorizer.gemini_classifier"):
        GeminiItemClassifier(db)._call_gemini_batch(
            batch, db.get_all_categories(), _fake_gemini_client(answer, usage)
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
            {"id": 0, "item_number": "1001", "clean_name": "Kirkland Olive Oil", "category": "Other"},
            {"id": 1, "item_number": "9999", "clean_name": "Wrong Item", "category": "Other"},
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


def _legacy_russian_db(db_path: Path) -> None:
    """A database from before English category names: custom_categories(name, name_en), Russian defaults."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE custom_categories (name TEXT PRIMARY KEY, created_at TEXT NOT NULL, name_en TEXT NOT NULL DEFAULT '');
        CREATE TABLE item_catalog (
            item_number TEXT PRIMARY KEY, raw_description TEXT NOT NULL, clean_name TEXT NOT NULL,
            category TEXT NOT NULL, is_manual_override INTEGER NOT NULL DEFAULT 0,
            classified_by TEXT NOT NULL DEFAULT 'pending', updated_at TEXT NOT NULL
        );
        INSERT INTO custom_categories VALUES
            ('Детские товары', '2026-10-01', 'Kids'), ('Подарки', '2026-10-01', ''),
            ('прочее', '2026-10-01', ''), ('Вино', '2026-10-01', 'Alcohol'), (' ', '2026-10-01', '');
        INSERT INTO item_catalog VALUES
            ('1', 'LEGO', 'LEGO', 'Детские товары', 1, 'manual', '2026-10-01'),
            ('2', 'EVOO', 'Olive Oil', 'Бакалея, масла, специи и консервы', 0, 'gemini', '2026-10-01'),
            ('3', 'CPN', 'Coupon', 'Прочее', 0, 'parser', '2026-10-01'),
            ('4', 'X', 'X', 'прочее', 1, 'manual', '2026-10-01'),
            ('5', 'MERLOT', 'Merlot', 'Вино', 1, 'manual', '2026-10-01'),
            ('6', 'Y', 'Y', ' ', 1, 'manual', '2026-10-01');
        PRAGMA user_version = 2;
        """
    )
    conn.close()


def test_startup_migrates_russian_categories_to_english(tmp_path: Path):
    from app.config import DEFAULT_CATEGORIES

    db_path = tmp_path / "test_costco.db"
    _legacy_russian_db(db_path)

    db = Database(db_path)
    Database(db_path)  # the migration runs on every start and changes nothing the second time

    with db.connect() as conn:
        rows = dict(conn.execute("SELECT item_number, category FROM item_catalog").fetchall())
    assert rows == {
        "1": "Kids",
        "2": "Pantry, oils, spices & canned goods",
        "3": "Other",
        "4": "Other",
        # "Вино" had the English name "Alcohol": it is renamed to it, which merges it into the default
        "5": "Alcohol",
        "6": "Other",
    }
    categories = db.get_all_categories()
    assert "Kids" in categories and "Подарки" in categories
    assert not {"Детские товары", "прочее", "Вино", "Прочее", " "} & set(categories)
    assert {k: v for k, v in db.get_category_labels_ru().items() if k not in DEFAULT_CATEGORIES} == {
        "Kids": "Детские товары"
    }


def test_blank_category_names_are_rejected(tmp_path: Path):
    import pytest

    from app.errors import AppError

    db = Database(tmp_path / "test_costco.db")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
            VALUES ('5555', 'DOG FOOD', 'Dog Food', 'Other', 0, 'fallback', '2026-10-01')
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
            {"id": 0, "item_number": "1234", "clean_name": "First", "category": "Other"},
            {"id": 0, "item_number": "1234", "clean_name": "Second", "category": "Alcohol"},
        ]
    }
    batch = [{"item_number": "1234", "raw_description": "KS ORG EVOO"}]
    results = GeminiItemClassifier(db)._call_gemini_batch(batch, db.get_all_categories(), _fake_gemini_client(answer))
    assert [r["clean_name"] for r in results] == ["Second"]
    assert "id=0" in caplog.text


def test_category_names_are_checked_in_database(tmp_path: Path):
    import pytest

    from app.errors import AppError

    db = Database(tmp_path / "test_costco.db")
    db.add_custom_category("Pets", "Питомцы")

    # Re-entering in another letter case updates the existing category instead of failing or duplicating it
    categories = db.add_custom_category("pets", "Зоотовары")
    assert "pets" not in categories
    assert db.get_category_labels_ru()["Pets"] == "Зоотовары"

    for name, name_ru in (("Other", ""), ("Прочее", ""), ("Pet food", "ЗООТОВАРЫ"), ("Wine", "Алкоголь")):
        with pytest.raises(AppError) as exc_info:
            db.add_custom_category(name, name_ru)
        assert exc_info.value.code == "category_reserved", name
    with pytest.raises(AppError) as exc_info:
        db.add_custom_category("Подарки", "Подарки")
    assert exc_info.value.code == "category_not_english"


def test_manual_category_edit_reuses_existing_category(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    db.add_custom_category("Pets", "Питомцы")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
            VALUES ('5555', 'DOG FOOD', 'Dog Food', 'Other', 0, 'fallback', '2026-10-01')
            """
        )

    # Typed names and Russian labels map to the stored English category
    assert db.update_item_category_manual("5555", "прочее")["category"] == "Other"
    assert db.update_item_category_manual("5555", "питомцы")["category"] == "Pets"
    assert db.update_item_category_manual("5555", "PETS")["category"] == "Pets"
    categories = db.get_all_categories()
    assert "прочее" not in categories and "PETS" not in categories


def test_duplicate_id_warning_skips_rejected_answers(tmp_path: Path, caplog):
    db = Database(tmp_path / "test_costco.db")
    answer = {
        "items": [
            {"id": 0, "item_number": "1234", "clean_name": "Right", "category": "Other"},
            {"id": 0, "item_number": "5678", "clean_name": "Wrong", "category": "Other"},
        ]
    }
    batch = [
        {"item_number": "1234", "raw_description": "A"},
        {"item_number": "5678", "raw_description": "B"},
    ]
    results = GeminiItemClassifier(db)._call_gemini_batch(batch, db.get_all_categories(), _fake_gemini_client(answer))
    assert [r["clean_name"] for r in results] == ["Right"]
    assert "повторный" not in caplog.text


def test_legacy_russian_category_can_be_used_and_translated(tmp_path: Path):
    db = Database(tmp_path / "test_costco.db")
    with db.connect() as conn:
        conn.execute("INSERT INTO custom_categories (name, name_ru, created_at) VALUES ('Питомцы', '', '2026-10-01')")
        conn.execute(
            """
            INSERT INTO item_catalog (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
            VALUES ('5555', 'DOG FOOD', 'Dog Food', 'Питомцы', 1, 'manual', '2026-10-01')
            """
        )

    # The UI sends the item's current category with a rename; an existing Russian category is accepted
    assert db.update_item_category_manual("5555", "Питомцы", "Dog Food 20 lb")["clean_name"] == "Dog Food 20 lb"

    # Adding the English name with the old Russian one as its label renames the category and its items
    categories = db.add_custom_category("Pets", "Питомцы")
    assert "Pets" in categories and "Питомцы" not in categories
    assert db.get_existing_catalog_map(["5555"])["5555"]["category"] == "Pets"
    assert db.get_category_labels_ru()["Pets"] == "Питомцы"
    assert "Питомцы" not in db.get_all_categories()


def test_swap_migration_merges_case_insensitively_and_keeps_labels(tmp_path: Path):
    import sqlite3

    db_path = tmp_path / "test_costco.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE custom_categories (name TEXT PRIMARY KEY, created_at TEXT NOT NULL, name_en TEXT NOT NULL DEFAULT '');
        INSERT INTO custom_categories VALUES
            ('pets', '2026-10-01', ''), ('Питомцы', '2026-10-01', 'Pets'), ('Игрушки', '2026-10-01', '   ');
        PRAGMA user_version = 2;
        """
    )
    conn.close()

    db = Database(db_path)

    categories = db.get_all_categories()
    assert "pets" in categories and "Pets" not in categories and "Питомцы" not in categories
    # A whitespace-only English name is ignored: the category keeps its name
    assert "Игрушки" in categories
    assert db.get_category_labels_ru()["pets"] == "Питомцы"

