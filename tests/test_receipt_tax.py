"""Tests for per-item tax and CRV on a receipt."""
from app.receipt_tax import crv_owners, is_crv_line, split_receipt


def _item(item_number, description, net, flag=""):
    return {"item_number": item_number, "raw_description": description, "net_amount": net, "tax_flag": flag}


def add_tax_and_crv(items, receipt_tax):
    """The lines a receipt view shows (CRV lines folded into their items), with their stored split."""
    rows = []
    for item, split in zip(items, split_receipt(items, receipt_tax), strict=True):
        if split["crv_owner"] is None:
            tax_crv = round((split["tax_amount"] or 0.0) + split["crv_amount"], 2)
            rows.append(dict(item, **split, tax_crv=tax_crv))
    return rows


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

    from app.database import ENGLISH_NAMES_SCHEMA_VERSION, TAX_SPLIT_SCHEMA_VERSION, Database
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == TAX_SPLIT_SCHEMA_VERSION


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


def test_coupon_without_item_number_skips_the_crv_line():
    from app.scraper.parser import parse_warehouse_or_gas_receipt

    parsed = parse_warehouse_or_gas_receipt(
        {
            "transactionBarcode": "1004",
            "transactionDate": "2026-06-04",
            "receiptType": "In-Warehouse",
            "itemArray": [
                {"itemNumber": "919140", "itemDescription01": "MILESTONE", "unit": 1, "amount": 9.99, "taxFlag": "Y"},
                {"itemNumber": "1254", "itemDescription01": "CA REDEMP VAL T EI/919140", "unit": 1, "amount": 0.1},
                {"itemNumber": "999", "itemDescription01": "INSTANT SAVINGS", "unit": -1, "amount": -3.0},
            ],
        }
    )
    wine, crv = parsed["items"]
    assert (wine["net_amount"], wine["discount_amount"]) == (6.99, 3.0)
    assert (crv["net_amount"], crv["discount_amount"]) == (0.1, 0.0)


def test_each_bottle_gets_its_own_deposit():
    items = [
        _item("942740", "PROSECCO", 9.99, "Y"),
        _item("942740", "PROSECCO", 9.99, "Y"),
        _item("1254", "CA REDEMP VAL T EI/942740", 0.10),
        _item("1254", "CA REDEMP VAL T EI/942740", 0.10),
    ]
    assert sorted(crv_owners(items).values()) == [0, 1]
    assert [r["crv_amount"] for r in add_tax_and_crv(items, 2.0)] == [0.10, 0.10]


def test_untaxed_lines_keep_their_total_when_the_tax_is_not_split():
    items = [_item("1", "TV", 100.00, "Y"), _item("1", "TV RETURN", -99.99, "Y"), _item("2", "KIWI", 5.0, "N")]
    tv, tv_return, kiwi = add_tax_and_crv(items, 0.83)
    assert tv["tax_unsplit"] and tv_return["tax_unsplit"]
    assert kiwi["tax_unsplit"] is False


def test_coupon_after_normalized_lines_without_description_does_not_crash():
    from app.scraper.parser import parse_warehouse_or_gas_receipt

    parsed = parse_warehouse_or_gas_receipt(
        {
            "transactionBarcode": "1005",
            "transactionDate": "2026-06-05",
            "receiptType": "In-Warehouse",
            "itemArray": [
                {"item_number": "777", "gross_amount": 10.0, "net_amount": 10.0, "discount_amount": 0.0},
                {"itemNumber": "999", "itemDescription01": "INSTANT SAVINGS", "unit": -1, "amount": -2.0},
            ],
        }
    )
    assert parsed["items"][0]["item_number"] == "777"


def test_untaxed_item_with_a_taxable_deposit_is_marked_unsplit():
    items = [
        _item("1", "TV", 100.00, "Y"),
        _item("1", "TV RETURN", -99.99, "Y"),
        _item("729441", "WATER", 5.0, "N"),
        _item("1254", "CA REDEMP VAL T EI/729441", 0.10),
    ]
    rows = add_tax_and_crv(items, 0.83)
    water = next(r for r in rows if r["item_number"] == "729441")
    assert water["tax_unsplit"] is True


def test_refunded_deposit_goes_to_the_returned_bottle():
    items = [
        _item("919140", "MILESTONE", 12.99, "Y"),
        _item("919140", "MILESTONE RETURN", -12.99, "Y"),
        _item("1254", "CA REDEMP VAL T EI/919140", 0.10),
        _item("1254", "CA REDEMP VAL T EI/919140", -0.10),
    ]
    assert crv_owners(items) == {2: 0, 3: 1}


def test_ordinary_lines_are_never_folded_as_crv():
    rows = add_tax_and_crv([_item("1", "SOAP", 6.0, "Y"), _item("2", "TOWEL", 4.0, "Y")], 1.0)
    assert [r["item_number"] for r in rows] == ["1", "2"]


def test_online_line_keeps_its_own_tax_flag():
    from app.scraper.parser import parse_online_order

    order = {
        "orderNumber": "1265538996",
        "orderPlacedDate": "2026-02-21T09:41:21",
        "orderTax": 1.0,
        "orderLineItems": [
            {"itemNumber": "1", "itemDescription": "TV", "quantity": 1, "merchandiseTotal": 10.0, "taxFlag": "Y"},
            {"itemNumber": "2", "itemDescription": "KIWI", "quantity": 1, "merchandiseTotal": 5.0, "taxFlag": "N"},
        ],
    }
    assert [i["tax_flag"] for i in parse_online_order(order)["items"]] == ["Y", "N"]


def test_a_bottle_already_given_a_deposit_lets_the_next_deposit_go_to_a_later_bottle():
    items = [
        _item("919140", "MILESTONE", 6.99, "Y"),
        _item("1254", "CA REDEMP VAL T EI/919140", 0.10),
        _item("1254", "CA REDEMP VAL T EI/919140", 0.10),
        _item("919140", "MILESTONE", 6.99, "Y"),
    ]
    assert crv_owners(items) == {1: 0, 2: 3}
    deposits_first = [
        _item("1254", "CA REDEMP VAL T EI/919140", 0.10),
        _item("1254", "CA REDEMP VAL T EI/919140", 0.10),
        _item("919140", "MILESTONE", 6.99, "Y"),
        _item("919140", "MILESTONE", 6.99, "Y"),
    ]
    assert sorted(crv_owners(deposits_first).values()) == [2, 3]


def test_orphan_deposit_is_taxed_by_its_description():
    items = [_item("1", "SOAP", 10.0, "Y"), _item("5235", "CA REDEMP VAL N EE/111", 2.50, "Y")]
    soap, deposit = add_tax_and_crv(items, 1.0)
    assert soap["tax_amount"] == 1.0
    assert deposit["tax_amount"] is None


def test_tax_free_receipt_shows_no_computed_zero_tax():
    (row,) = add_tax_and_crv([_item("1", "SOAP", 10.0, "Y")], 0.0)
    assert row["tax_amount"] is None
    assert row["tax_unsplit"] is False


def test_an_extra_deposit_stacks_on_the_nearest_bottle_either_side():
    items = [_item("919140", "MILESTONE", 6.99, "Y")]
    items += [_item(str(n), f"ITEM {n}", 1.0, "N") for n in range(5)]
    items += [
        _item("1254", "CA REDEMP VAL T EI/919140", 0.10),
        _item("1254", "CA REDEMP VAL T EI/919140", 0.10),
        _item("919140", "MILESTONE", 6.99, "Y"),
        _item("1254", "CA REDEMP VAL T EI/919140", 0.10),
    ]
    owners = crv_owners(items)
    assert owners[6] == 0 and owners[7] == 8 and owners[9] == 8


def test_untaxed_orphan_deposit_keeps_its_total_when_the_tax_is_not_split():
    items = [_item("1", "TV", 100.00, "Y"), _item("1", "TV RETURN", -99.99, "Y"), _item("5235", "CA REDEMP VAL N EE/111", 2.5)]
    deposit = add_tax_and_crv(items, 0.83)[-1]
    assert deposit["tax_unsplit"] is False


def test_single_item_online_order_with_a_fee_is_not_taxed_per_item():
    from app.scraper.parser import parse_online_order

    order = {
        "orderNumber": "1265538997",
        "orderPlacedDate": "2026-02-22T09:41:21",
        "orderTax": 1.0,
        "orderLineItems": [
            {"itemNumber": "1", "itemDescription": "MILK", "quantity": 1, "merchandiseTotal": 5.0},
            {"itemNumber": "F", "itemDescription": "DELIVERY", "quantity": 1, "merchandiseTotal": 10.0, "isFeeItem": True},
        ],
    }
    (milk,) = parse_online_order(order)["items"]
    assert milk["tax_flag"] == ""


def test_tax_is_not_split_when_a_line_has_an_unknown_flag():
    items = [_item("1", "SOAP", 10.0, "Y"), _item("2", "MYSTERY", 10.0, "")]
    soap, mystery = add_tax_and_crv(items, 1.0)
    assert soap["tax_amount"] is None and mystery["tax_amount"] is None
    assert soap["tax_unsplit"] and mystery["tax_unsplit"]


def test_crv_wordings_are_recognized():
    for description in (
        "CA REDEMP VAL T EI/919140",
        "CA REDEMP VAL TEI/919140",
        "CA REDEMP VA T EI/919140",
        "CA REDEMP VAL /919140",
        "CA REDEMP VAL T",
    ):
        assert is_crv_line(description), description
    assert not is_crv_line("CAB SAUV NAPA 750ML")
    items = [_item("919140", "MILESTONE", 4.99, "Y"), _item("1254", "CA REDEMP VAL TEI/919140", 0.10)]
    assert crv_owners(items) == {1: 0}


def test_deposit_without_a_marker_leaves_the_tax_unsplit():
    items = [_item("919140", "MILESTONE", 4.99, "Y"), _item("1254", "CA REDEMP VAL /919140", 0.10)]
    (wine,) = add_tax_and_crv(items, 0.51)
    assert wine["crv_amount"] == 0.10
    assert wine["tax_amount"] is None and wine["tax_unsplit"] is True


def test_old_database_drops_crv_for_item_and_gets_the_split_stored(tmp_path):
    import sqlite3

    from app.database import CRV_SCHEMA_VERSION, Database
    from app.scraper.parser import parse_any_costco_payload

    path = tmp_path / "old.db"
    db = Database(path)
    db.init_db()
    receipt = {
        "transactionBarcode": "21126701000062606120935",
        "transactionDate": "2026-06-12",
        "receiptType": "In-Warehouse",
        "taxes": 0.51,
        "itemArray": [
            {"itemNumber": "919140", "itemDescription01": "MILESTONE", "unit": 1, "amount": 4.99, "taxFlag": "Y"},
            {"itemNumber": "1254", "itemDescription01": "CA REDEMP VAL T EI/919140", "unit": 1, "amount": 0.1},
        ],
    }
    db.upsert_receipts(parse_any_costco_payload([receipt]))
    # The previous layout: crv_for_item, and no stored split
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE receipt_items ADD COLUMN crv_for_item TEXT")
        for column in ("tax_amount", "crv_amount", "tax_unsplit"):
            conn.execute(f"ALTER TABLE receipt_items DROP COLUMN {column}")
        conn.execute("UPDATE receipt_items SET crv_owner_id = NULL")
        conn.execute(f"PRAGMA user_version = {CRV_SCHEMA_VERSION}")

    db.init_db()
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(receipt_items)")}
    assert "crv_for_item" not in columns
    (wine,) = db.get_receipts_list()[0]["items"]
    assert (wine["tax_amount"], wine["crv_amount"], wine["tax_crv"]) == (0.51, 0.10, 0.61)
    assert db.get_statistics()["by_category"][0]["net_spent"] == 5.09


def test_deposit_without_a_marker_falls_back_to_its_flag():
    items = [_item("919140", "MILESTONE", 4.99, "Y"), _item("1254", "CA REDEMP VAL /919140", 0.10, "Y")]
    (wine,) = add_tax_and_crv(items, 0.51)
    assert wine["tax_amount"] == 0.51


def test_zero_padded_item_number_still_gets_its_deposit():
    items = [_item("0919140", "MILESTONE", 4.99, "Y"), _item("1254", "CA REDEMP VAL T EI/919140", 0.10)]
    assert crv_owners(items) == {1: 0}


def test_unmatched_coupon_does_not_stop_the_split():
    from app.scraper.parser import parse_warehouse_or_gas_receipt

    parsed = parse_warehouse_or_gas_receipt(
        {
            "transactionBarcode": "1006",
            "transactionDate": "2026-06-06",
            "receiptType": "In-Warehouse",
            "taxes": 1.0,
            "itemArray": [
                {"itemNumber": "777", "itemDescription01": "SOAP", "unit": 1, "amount": 10.0, "taxFlag": "Y"},
                {"itemNumber": "999", "itemDescription01": "/ 5555555", "unit": 1, "amount": -2.0},
            ],
        }
    )
    soap = next(r for r in add_tax_and_crv(parsed["items"], 1.0) if r["item_number"] == "777")
    assert soap["tax_amount"] == 1.0


def test_single_item_online_order_with_shipping_is_not_taxed_per_item():
    from app.scraper.parser import parse_online_order

    order = {
        "orderNumber": "1265538998",
        "orderPlacedDate": "2026-02-23T09:41:21",
        "orderTax": 1.0,
        "shippingAndHandling": 15.0,
        "orderLineItems": [{"itemNumber": "1", "itemDescription": "LAMP", "quantity": 1, "merchandiseTotal": 5.0}],
    }
    (lamp,) = parse_online_order(order)["items"]
    assert lamp["tax_flag"] == ""



def _wine_receipt(barcode):
    return {
        "transactionBarcode": barcode,
        "transactionDate": "2026-06-12",
        "receiptType": "In-Warehouse",
        "taxes": 0.51,
        "itemArray": [
            {"itemNumber": "919140", "itemDescription01": "MILESTONE", "unit": 1, "amount": 4.99, "taxFlag": "Y"},
            {"itemNumber": "1254", "itemDescription01": "CA REDEMP VAL T EI/919140", "unit": 1, "amount": 0.1},
        ],
    }


def test_marker_glued_to_the_word_is_recognized():
    assert is_crv_line("CA REDEMP VALT EI/919140")
    assert crv_owners([_item("919140", "MILESTONE", 4.99, "Y"), _item("1254", "CA REDEMP VALT/919140", 0.1)]) == {1: 0}


def test_partial_receipt_lines_lose_their_tax_flags():
    from app.scraper.costco_client import _drop_tax_flags_if_partial

    parsed = {"is_partial": True, "items": [{"tax_flag": "Y"}]}
    _drop_tax_flags_if_partial(parsed)
    assert parsed["items"][0]["tax_flag"] == ""
    complete = {"is_partial": False, "items": [{"tax_flag": "Y"}]}
    _drop_tax_flags_if_partial(complete)
    assert complete["items"][0]["tax_flag"] == "Y"


def test_alcohol_deposits_stay_in_the_reward_base(tmp_path):
    from app.database import Database
    from app.scraper.parser import parse_any_costco_payload

    db = Database(tmp_path / "reward.db")
    db.init_db()
    db.upsert_receipts(parse_any_costco_payload([_wine_receipt("1007")]))
    db.save_categorized_items([{"item_number": "919140", "clean_name": "Milestone wine", "category": "Alcohol"}])
    spend = db.get_membership_analytics()["spend_breakdown"]
    assert spend["excluded_alcohol_spent"] == 4.99
    assert spend["eligible_spend"] == 0.10


def test_upgrade_resplits_receipts_split_by_older_rules(tmp_path):
    import sqlite3

    from app.database import CRV_SCHEMA_VERSION, Database
    from app.scraper.parser import parse_any_costco_payload

    path = tmp_path / "resplit.db"
    db = Database(path)
    db.init_db()
    db.upsert_receipts(parse_any_costco_payload([_wine_receipt("1008")]))
    # An earlier build stored a different split: the deposit left on its own and no tax
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE receipt_items SET crv_owner_id = NULL, tax_amount = NULL, crv_amount = 0, tax_unsplit = 0")
        conn.execute(f"PRAGMA user_version = {CRV_SCHEMA_VERSION}")

    db.init_db()
    (wine,) = db.get_receipts_list()[0]["items"]
    assert (wine["tax_amount"], wine["crv_amount"]) == (0.51, 0.10)


def test_upgrade_keeps_imported_online_flags(tmp_path):
    import sqlite3

    from app.database import ENGLISH_NAMES_SCHEMA_VERSION, Database

    path = tmp_path / "online_flags.db"
    db = Database(path)
    db.init_db()
    db.upsert_receipts(
        [
            {
                "transaction_barcode": "ONL-1",
                "transaction_date": "2026-02-20",
                "receipt_type": "online",
                "tax": 1.0,
                "total": 16.0,
                "items": [
                    {"item_number": "1", "raw_description": "TV", "net_amount": 10.0, "gross_amount": 10.0, "tax_flag": "Y"},
                    {"item_number": "2", "raw_description": "KIWI", "net_amount": 5.0, "gross_amount": 5.0, "tax_flag": "N"},
                ],
            }
        ]
    )
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE receipts SET raw_json = '{}'")
        conn.execute(f"PRAGMA user_version = {ENGLISH_NAMES_SCHEMA_VERSION}")

    db.init_db()
    tv, kiwi = db.get_receipts_list()[0]["items"]
    assert (tv["tax_amount"], kiwi["tax_amount"]) == (1.0, None)
