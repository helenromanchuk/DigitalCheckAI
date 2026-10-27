"""Tests for per-item tax and CRV on a receipt."""
from app.receipt_tax import add_tax_and_crv as _add_tax_and_crv
from app.receipt_tax import crv_owners


def _item(item_number, description, net, flag=""):
    return {"item_number": item_number, "raw_description": description, "net_amount": net, "tax_flag": flag}


def add_tax_and_crv(items, receipt_tax):
    """Links CRV lines as the database does on insert, then splits the tax."""
    items = [dict(item, id=index + 1, crv_owner_id=None) for index, item in enumerate(items)]
    for crv_index, owner_index in crv_owners(items).items():
        items[crv_index]["crv_owner_id"] = items[owner_index]["id"]
    return _add_tax_and_crv(items, receipt_tax)


def test_crv_line_with_a_second_description_is_still_linked():
    items = [_item("919140", "MILESTONE", 4.99, "Y"), _item("1254", "CA REDEMP VAL T EI/919140 CRV", 0.10)]
    assert crv_owners(items) == {1: 0}


def test_crv_takes_the_nearest_earlier_line_of_its_item():
    items = [
        _item("942740", "PROSECCO", 9.99, "Y"),
        _item("1254", "CA REDEMP VAL T EI/942740", 0.10),
        _item("942740", "PROSECCO", 9.99, "Y"),
        _item("1254", "CA REDEMP VAL T EI/942740", 0.10),
    ]
    assert crv_owners(items) == {1: 0, 3: 2}


def test_crv_is_folded_into_its_item_and_taxed_with_it():
    items = [
        _item("919140", "MILESTONE CALIFORNIA 12/750ML", 4.99, "Y"),
        _item("1254", "CA REDEMP VAL T EI/919140", 0.10),
        _item("1958188", "BM DRESS", 16.99, "Y"),
        _item("48757", "SPRING MIX", 4.99, "N"),
    ]
    rows = add_tax_and_crv(items, 2.21)

    assert [r["item_number"] for r in rows] == ["919140", "1958188", "48757"]
    wine, dress, greens = rows
    assert wine["crv_amount"] == 0.10
    assert wine["tax_amount"] == 0.51
    assert wine["tax_crv"] == 0.61
    assert dress["tax_amount"] == 1.70
    assert greens["tax_amount"] is None and greens["tax_crv"] == 0.0
    assert round(wine["tax_amount"] + dress["tax_amount"], 2) == 2.21


def test_untaxed_crv_does_not_join_the_tax_base():
    items = [_item("729441", "WATER", 5.00, "N"), _item("5235", "CA REDEMP VAL N EE/729441", 2.50)]
    (water,) = add_tax_and_crv(items, 0.0)
    assert water["crv_amount"] == 2.50
    assert water["tax_amount"] is None
    assert water["tax_crv"] == 2.50


def test_crv_without_its_item_stays_a_line():
    rows = add_tax_and_crv([_item("1254", "CA REDEMP VAL T EI/111", 0.10)], 0.0)
    assert len(rows) == 1 and rows[0]["crv_amount"] == 0.0


def test_tax_without_flags_is_not_split():
    rows = add_tax_and_crv([_item("1", "MILK", 4.0), _item("2", "SOAP", 6.0)], 0.55)
    assert [r["tax_amount"] for r in rows] == [None, None]


def test_refund_tax_is_split_too():
    (row,) = add_tax_and_crv([_item("1", "RETURN", -12.99, "Y")], -1.30)
    assert row["tax_amount"] == -1.30


def test_crv_counts_towards_its_item_category_in_the_database(tmp_path):
    from app.database import Database
    from app.scraper.parser import parse_any_costco_payload

    db = Database(tmp_path / "crv.db")
    db.init_db()
    receipt = {
        "transactionBarcode": "21126701000062606120932",
        "transactionDate": "2026-06-12",
        "receiptType": "In-Warehouse",
        "warehouseName": "SAN JOSE #148",
        "subTotal": 5.09,
        "taxes": 0.51,
        "total": 5.60,
        "itemArray": [
            {"itemNumber": "919140", "itemDescription01": "MILESTONE", "unit": 1, "amount": 4.99, "taxFlag": "Y"},
            {"itemNumber": "1254", "itemDescription01": "CA REDEMP VAL T EI/919140", "unit": 1, "amount": 0.1},
        ],
    }
    db.upsert_receipts(parse_any_costco_payload([receipt]))
    db.save_categorized_items(
        [{"item_number": "919140", "clean_name": "Milestone wine", "category": "Alcohol"}]
    )

    stats = db.get_statistics()
    (alcohol,) = stats["by_category"]
    assert alcohol["category"] == "Alcohol"
    assert alcohol["net_spent"] == 5.09
    assert alcohol["line_count"] == 1
    (wine,) = db.get_items_summary()
    assert wine["item_number"] == "919140"
    assert wine["net_spent"] == 4.99
    assert (wine["tax_amount"], wine["crv_amount"], wine["tax_crv"]) == (0.51, 0.10, 0.61)


def test_tax_is_not_split_when_a_return_cancels_a_purchase():
    items = [_item("1", "TV", 100.00, "Y"), _item("1", "TV RETURN", -99.99, "Y")]
    rows = add_tax_and_crv(items, 0.83)
    assert [r["tax_amount"] for r in rows] == [None, None]


def test_crv_is_parsed_into_other_and_kept_out_of_search_and_gemini(tmp_path):
    from app.database import Database
    from app.scraper.parser import CRV_NAME, parse_any_costco_payload

    db = Database(tmp_path / "crv_search.db")
    db.init_db()
    receipt = {
        "transactionBarcode": "21126701000062606120933",
        "transactionDate": "2026-06-12",
        "receiptType": "In-Warehouse",
        "warehouseName": "SAN JOSE #148",
        "subTotal": 5.09,
        "taxes": 0.51,
        "total": 5.60,
        "itemArray": [
            {"itemNumber": "919140", "itemDescription01": "MILESTONE", "unit": 1, "amount": 4.99, "taxFlag": "Y"},
            {"itemNumber": "1254", "itemDescription01": "CA REDEMP VAL T EI/919140", "unit": 1, "amount": 0.1},
        ],
    }
    (parsed,) = parse_any_costco_payload([receipt])
    assert parsed["items"][1]["default_clean_name"] == CRV_NAME
    db.upsert_receipts([parsed])

    assert [i["item_number"] for i in db.get_pending_items_for_gemini()] == ["919140"]
    assert db.search_purchases("REDEMP") == []
    (receipt_row,) = db.get_receipts_list()
    (wine,) = receipt_row["items"]
    assert wine["tax_crv"] == 0.61


def test_online_order_tax_is_split_only_for_a_single_item():
    from app.scraper.parser import parse_online_order

    def order(*lines):
        return {
            "orderNumber": "1265538995",
            "orderPlacedDate": "2026-02-20T09:41:21",
            "orderTax": 6.07,
            "orderLineItems": [
                {"itemNumber": num, "itemDescription": num, "quantity": 1, "merchandiseTotal": amount}
                for num, amount in lines
            ],
        }

    (single,) = parse_online_order(order(("1", 64.94)))["items"]
    assert single["tax_flag"] == "Y"
    several = parse_online_order(order(("1", 50.0), ("2", 14.94)))["items"]
    assert [i["tax_flag"] for i in several] == ["", ""]


def test_upgrade_reparses_so_pending_crv_rows_leave_the_gemini_queue(tmp_path):
    import sqlite3

    from app.database import CRV_SCHEMA_VERSION, ENGLISH_NAMES_SCHEMA_VERSION, Database
    from app.scraper.parser import parse_any_costco_payload

    path = tmp_path / "upgrade.db"
    db = Database(path)
    db.init_db()
    receipt = {
        "transactionBarcode": "21126701000062606120934",
        "transactionDate": "2026-06-12",
        "receiptType": "In-Warehouse",
        "warehouseName": "SAN JOSE #148",
        "subTotal": 5.09,
        "taxes": 0.51,
        "total": 5.60,
        "itemArray": [
            {"itemNumber": "919140", "itemDescription01": "MILESTONE", "unit": 1, "amount": 4.99, "taxFlag": "Y"},
            {"itemNumber": "1254", "itemDescription01": "CA REDEMP VAL T EI/919140", "unit": 1, "amount": 0.1},
        ],
    }
    db.upsert_receipts(parse_any_costco_payload([receipt]))
    # A database from before this change: the CRV row is waiting for Gemini
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE item_catalog SET classified_by = 'pending' WHERE item_number = '1254'")
        conn.execute(f"PRAGMA user_version = {ENGLISH_NAMES_SCHEMA_VERSION}")
    assert "1254" in [i["item_number"] for i in db.get_pending_items_for_gemini()]

    db.init_db()
    assert "1254" not in [i["item_number"] for i in db.get_pending_items_for_gemini()]
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CRV_SCHEMA_VERSION


def test_unsplit_tax_is_flagged_on_the_receipt_and_the_item_total(tmp_path):
    from app.database import Database
    from app.scraper.parser import parse_any_costco_payload

    def receipt(barcode, date, tax, flag):
        return {
            "transactionBarcode": barcode,
            "transactionDate": date,
            "receiptType": "In-Warehouse",
            "warehouseName": "SAN JOSE #148",
            "taxes": tax,
            "itemArray": [{"itemNumber": "777", "itemDescription01": "SOAP", "unit": 1, "amount": 10.0, "taxFlag": flag}],
        }

    db = Database(tmp_path / "unsplit.db")
    db.init_db()
    db.upsert_receipts(
        parse_any_costco_payload([receipt("1001", "2026-06-01", 1.0, "Y"), receipt("1002", "2026-06-02", 1.0, "")])
    )

    by_barcode = {r["transaction_barcode"]: r["items"][0] for r in db.get_receipts_list()}
    assert by_barcode["1001"]["tax_unsplit"] is False
    assert by_barcode["1002"]["tax_unsplit"] is True
    (soap,) = db.get_items_summary()
    assert soap["tax_amount"] == 1.0
    assert soap["tax_partial"] is True


def test_normalized_crv_line_gets_the_parser_category():
    from app.scraper.parser import CRV_NAME, parse_warehouse_or_gas_receipt

    line = {"item_number": "1254", "raw_description": "CA REDEMP VAL T EI/919140", "gross_amount": 0.1, "net_amount": 0.1}
    parsed = parse_warehouse_or_gas_receipt(
        {"transactionBarcode": "1003", "transactionDate": "2026-06-03", "receiptType": "In-Warehouse", "items": [line]}
    )
    assert parsed["items"][0]["default_clean_name"] == CRV_NAME
