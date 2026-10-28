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


def test_crv_without_its_item_stays_a_line_shown_as_a_deposit():
    rows = add_tax_and_crv([_item("1254", "CA REDEMP VAL T EI/111", 0.10)], 0.0)
    assert len(rows) == 1
    assert (rows[0]["crv_amount"], rows[0]["is_deposit"]) == (0.10, True)


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
    # Found only as a deposit (by its own text), never as a product
    assert [r["is_deposit"] for r in db.search_purchases("REDEMP")] == [1]
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


def test_deposit_that_states_nothing_is_taxed_like_its_bottle():
    items = [_item("919140", "MILESTONE", 4.99, "Y"), _item("1254", "CA REDEMP VAL /919140", 0.10)]
    (wine,) = add_tax_and_crv(items, 0.51)
    assert (wine["crv_amount"], wine["tax_amount"], wine["tax_unsplit"]) == (0.10, 0.51, False)


def test_orphan_deposit_that_states_nothing_leaves_the_tax_unsplit():
    items = [_item("1", "SOAP", 10.0, "Y"), _item("1254", "CA REDEMP VAL /111", 0.10)]
    soap, deposit = add_tax_and_crv(items, 1.0)
    assert soap["tax_amount"] is None and soap["tax_unsplit"] and deposit["tax_unsplit"]


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
        conn.execute("ALTER TABLE receipts DROP COLUMN tax_split_version")
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
        conn.execute("UPDATE receipts SET tax_split_version = NULL")
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



def test_coupon_on_a_returned_item_is_taxed_like_it():
    from app.scraper.parser import parse_warehouse_or_gas_receipt

    parsed = parse_warehouse_or_gas_receipt(
        {
            "transactionBarcode": "1009",
            "transactionDate": "2026-06-09",
            "receiptType": "In-Warehouse",
            "itemArray": [
                {"itemNumber": "5555555", "itemDescription01": "DRILL", "unit": -1, "amount": -20.0, "taxFlag": "Y"},
                {"itemNumber": "999", "itemDescription01": "/ 5555555", "unit": 1, "amount": -3.0},
            ],
        }
    )
    coupon = next(i for i in parsed["items"] if i["item_number"].startswith("DISCOUNT-"))
    assert coupon["tax_flag"] == "Y"


def test_receipts_split_by_older_rules_are_split_again(tmp_path, monkeypatch):
    import app.database as database
    from app.scraper.parser import parse_any_costco_payload

    db = database.Database(tmp_path / "rules.db")
    db.init_db()
    db.upsert_receipts(parse_any_costco_payload([_wine_receipt("1010")]))
    with db.connect() as conn:
        conn.execute("UPDATE receipt_items SET tax_amount = NULL, crv_amount = 0, crv_owner_id = NULL")

    monkeypatch.setattr(database, "SPLIT_RULES_VERSION", database.SPLIT_RULES_VERSION + 1)
    db.init_db()
    (wine,) = db.get_receipts_list()[0]["items"]
    assert (wine["tax_amount"], wine["crv_amount"]) == (0.51, 0.10)
    with db.connect() as conn:
        assert conn.execute("SELECT tax_split_version FROM receipts").fetchone()[0] == database.SPLIT_RULES_VERSION


def test_oldest_database_resplits_online_orders_whose_flags_were_reset(tmp_path):
    import sqlite3

    from app.database import Database

    path = tmp_path / "v0.db"
    db = Database(path)
    db.init_db()
    db.upsert_receipts(
        [
            {
                "transaction_barcode": "ONL-2",
                "transaction_date": "2026-02-20",
                "receipt_type": "online",
                "tax": 1.5,
                "total": 16.5,
                "items": [
                    {"item_number": "1", "raw_description": "TV", "net_amount": 10.0, "gross_amount": 10.0, "tax_flag": "Y"},
                    {"item_number": "2", "raw_description": "KIWI", "net_amount": 5.0, "gross_amount": 5.0, "tax_flag": "Y"},
                ],
            }
        ]
    )
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE receipts SET raw_json = '{}'")
        conn.execute("PRAGMA user_version = 0")

    db.init_db()
    tv, kiwi = db.get_receipts_list()[0]["items"]
    assert (tv["tax_amount"], kiwi["tax_amount"]) == (None, None)
    assert tv["tax_unsplit"] and kiwi["tax_unsplit"]


def test_a_letter_glued_to_va_is_not_a_marker():
    assert not is_crv_line("CA REDEMP VAN")
    assert crv_owners([_item("919140", "MILESTONE", 4.99, "Y"), _item("1254", "CA REDEMP VALN/919140", 0.1)]) == {1: 0}


def test_coupon_reference_with_leading_zeros_finds_the_returned_item():
    from app.scraper.parser import parse_warehouse_or_gas_receipt

    parsed = parse_warehouse_or_gas_receipt(
        {
            "transactionBarcode": "1011",
            "transactionDate": "2026-06-11",
            "receiptType": "In-Warehouse",
            "itemArray": [
                {"itemNumber": "5555555", "itemDescription01": "DRILL", "unit": -1, "amount": -20.0, "taxFlag": "Y"},
                {"itemNumber": "999", "itemDescription01": "/ 05555555", "unit": 1, "amount": -3.0},
            ],
        }
    )
    coupon = next(i for i in parsed["items"] if i["item_number"].startswith("DISCOUNT-"))
    assert coupon["tax_flag"] == "Y"


def test_orphan_deposit_and_items_with_no_split_tax_in_the_views(tmp_path):
    from app.database import Database

    db = Database(tmp_path / "views.db")
    db.init_db()
    db.upsert_receipts(
        [
            {
                "transaction_barcode": "SWY-1",
                "transaction_date": "2026-06-12",
                "receipt_type": "safeway",
                "store": "safeway",
                "tax": 0.5,
                "items": [
                    {"item_number": "SWY-1", "raw_description": "SOAP", "net_amount": 5.0, "gross_amount": 5.0},
                    {"item_number": "1254", "raw_description": "CA REDEMP VAL N EE/111", "net_amount": 0.1, "gross_amount": 0.1},
                ],
            }
        ]
    )
    soap, deposit = db.get_receipts_list()[0]["items"]
    assert deposit["is_deposit"] and deposit["crv_amount"] == 0.1
    listed = {i["item_number"]: i for i in db.get_items_summary()}
    assert listed["SWY-1"]["tax_unknown"] is True
    # The deposit whose bottle is elsewhere keeps its money in view: under Tax/CRV, not Net
    assert (listed["1254"]["net_spent"], listed["1254"]["crv_amount"], listed["1254"]["total_quantity"]) == (0.0, 0.1, 0.0)
    # Search finds it too, marked as a deposit
    (found,) = db.search_purchases("REDEMP")
    assert found["is_deposit"] == 1


def test_catalog_rows_of_unparseable_receipts_get_the_crv_category(tmp_path):
    import sqlite3

    from app.database import ENGLISH_NAMES_SCHEMA_VERSION, Database
    from app.scraper.parser import parse_any_costco_payload

    path = tmp_path / "catalog.db"
    db = Database(path)
    db.init_db()
    db.upsert_receipts(parse_any_costco_payload([_wine_receipt("1012")]))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE item_catalog SET classified_by = 'pending' WHERE item_number = '1254'")
        conn.execute("UPDATE receipts SET raw_json = '{}'")
        conn.execute(f"PRAGMA user_version = {ENGLISH_NAMES_SCHEMA_VERSION}")

    db.init_db()
    assert "1254" not in [i["item_number"] for i in db.get_pending_items_for_gemini()]


def test_tax_on_a_receipt_with_only_untaxed_lines_is_flagged():
    (row,) = add_tax_and_crv([_item("1", "KIWI", 5.0, "N")], 1.25)
    assert row["tax_amount"] is None and row["tax_unsplit"] is True


def test_taxable_line_that_nets_to_zero_shows_a_zero_tax():
    # A fully discounted taxable item: taxed, at $0.00, unlike an untaxed one
    items = [_item("1", "TV", 100.0, "Y"), _item("2", "FREE LAMP", 0.0, "Y"), _item("3", "KIWI", 5.0, "N")]
    rows = add_tax_and_crv(items, 10.0)
    assert [r["tax_amount"] for r in rows] == [10.0, 0.0, None]


def test_coupon_with_leading_zeros_attaches_to_its_sale():
    from app.scraper.parser import parse_warehouse_or_gas_receipt

    parsed = parse_warehouse_or_gas_receipt(
        {
            "transactionBarcode": "1013",
            "transactionDate": "2026-06-13",
            "receiptType": "In-Warehouse",
            "itemArray": [
                {"itemNumber": "0919140", "itemDescription01": "MILESTONE", "unit": 1, "amount": 20.0, "taxFlag": "Y"},
                {"itemNumber": "999", "itemDescription01": "/ 919140", "unit": 1, "amount": -4.0},
            ],
        }
    )
    (wine,) = parsed["items"]
    assert wine["net_amount"] == 16.0


def test_orphan_deposits_count_as_the_items_tab_lists_them(tmp_path):
    from app.database import Database

    db = Database(tmp_path / "unique.db")
    db.init_db()
    db.upsert_receipts(
        [
            {
                "transaction_barcode": "1014",
                "transaction_date": "2026-06-14",
                "receipt_type": "warehouse",
                "tax": 0.0,
                "items": [
                    {"item_number": "1254", "raw_description": "CA REDEMP VAL N EE/111", "net_amount": 0.1, "gross_amount": 0.1},
                    {"item_number": "1254", "raw_description": "CA REDEMP VAL N EE/222", "net_amount": 0.1, "gross_amount": 0.1},
                ],
            }
        ]
    )
    (other,) = db.get_statistics()["by_category"]
    # Listed in the Items tab as one CRV row bought twice
    assert (other["line_count"], other["unique_items"]) == (2, 1)



def test_allocate_cents_keeps_every_share_within_a_cent():
    from app.money import allocate_cents

    weights = [10.0] * 39 + [0.10]
    shares = allocate_cents(3.91, weights)
    assert round(sum(shares), 2) == 3.91
    exact = [3.91 * w / sum(weights) for w in weights]
    assert all(abs(share - value) < 0.01 for share, value in zip(shares, exact, strict=True))
    assert allocate_cents(-1.3, [-12.99]) == [-1.3]


def test_refunded_deposit_without_a_returned_bottle_stays_its_own_line():
    items = [_item("919140", "MILESTONE", 12.99, "Y"), _item("1254", "CA REDEMP VAL T EI/919140", -0.10)]
    assert crv_owners(items) == {}


def test_blank_catalog_categories_move_to_other(tmp_path):
    from app.database import Database
    from app.scraper.parser import parse_any_costco_payload

    db = Database(tmp_path / "blank.db")
    db.init_db()
    db.upsert_receipts(parse_any_costco_payload([_wine_receipt("1015")]))
    with db.connect() as conn:
        conn.execute("UPDATE item_catalog SET category = '' WHERE item_number = '919140'")
    db.init_db()
    with db.connect() as conn:
        assert conn.execute("SELECT category FROM item_catalog WHERE item_number = '919140'").fetchone()[0] == "Other"



def test_item_number_after_a_space_links_the_deposit():
    items = [_item("919140", "MILESTONE", 4.99, "Y"), _item("1254", "CA REDEMP VAL N EI 919140", 0.10)]
    assert crv_owners(items) == {1: 0}
    assert is_crv_line("CA REDEMPTION VALUE")


def test_crv_references_keep_their_store_namespace_and_normalize_leading_zeros():
    items = [
        _item("SWY-0001", "SAFEWAY WATER", 5.0),
        _item("1", "COSTCO WATER", 5.0),
        _item("SWY-2", "CA REDEMP VAL N 001", 0.1),
        _item("2", "CA REDEMP VAL N 0001", 0.2),
    ]
    assert crv_owners(items) == {2: 0, 3: 1}


def test_online_crv_line_gets_the_parser_category():
    from app.scraper.parser import CRV_NAME, parse_online_order

    order = {
        "orderNumber": "1265538999",
        "orderPlacedDate": "2026-02-24T09:41:21",
        "orderTax": 0.0,
        "orderLineItems": [
            {"itemNumber": "919140", "itemDescription": "MILESTONE", "quantity": 1, "merchandiseTotal": 4.99},
            {"itemNumber": "1254", "itemDescription": "CA REDEMP VAL T EI/919140", "quantity": 1, "merchandiseTotal": 0.1},
        ],
    }
    crv = parse_online_order(order)["items"][1]
    assert crv["default_clean_name"] == CRV_NAME


def test_old_single_item_online_order_with_a_fee_loses_its_flag(tmp_path):
    import sqlite3

    from app.database import ENGLISH_NAMES_SCHEMA_VERSION, Database

    path = tmp_path / "fee.db"
    db = Database(path)
    db.init_db()
    db.upsert_receipts(
        [
            {
                "transaction_barcode": "ONL-3",
                "transaction_date": "2026-02-20",
                "receipt_type": "online",
                "subtotal": 10.0,
                "tax": 1.0,
                "total": 21.0,
                "items": [{"item_number": "1", "raw_description": "LAMP", "net_amount": 10.0, "gross_amount": 10.0, "tax_flag": "Y"}],
            }
        ]
    )
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE receipts SET raw_json = '{}'")
        conn.execute(f"PRAGMA user_version = {ENGLISH_NAMES_SCHEMA_VERSION}")

    db.init_db()
    (lamp,) = db.get_receipts_list()[0]["items"]
    assert lamp["tax_amount"] is None and lamp["tax_unsplit"] is True



def test_pack_size_before_the_item_number_is_skipped():
    for description in ("CA REDEMP VAL 24PK/919140", "CA REDEMP VA 12PK 919140", "CA REDEMP VAL T EI/919140 CRV"):
        items = [_item("919140", "MILESTONE", 4.99, "Y"), _item("1254", description, 0.10)]
        assert crv_owners(items) == {1: 0}, description


def test_order_discount_never_reduces_a_deposit():
    from app.scraper.parser import parse_online_order

    order = {
        "orderNumber": "1265539000",
        "orderPlacedDate": "2026-02-25T09:41:21",
        "orderTax": 0.0,
        "discountAmount": 5.0,
        "orderLineItems": [
            {"itemNumber": "919140", "itemDescription": "WINE", "quantity": 1, "merchandiseTotal": 45.0},
            {"itemNumber": "1254", "itemDescription": "CA REDEMP VAL T EI/919140", "quantity": 1, "merchandiseTotal": 5.0},
        ],
    }
    wine, crv = parse_online_order(order)["items"]
    assert (wine["net_amount"], crv["net_amount"]) == (40.0, 5.0)


def test_orphan_deposit_counts_as_a_listed_line_in_the_stats(tmp_path):
    from app.database import Database

    db = Database(tmp_path / "orphan_stats.db")
    db.init_db()
    db.upsert_receipts(
        [
            {
                "transaction_barcode": "1016",
                "transaction_date": "2026-06-16",
                "receipt_type": "warehouse",
                "tax": 0.0,
                "items": [{"item_number": "1254", "raw_description": "CA REDEMP VAL N EE/123", "net_amount": 0.3, "gross_amount": 0.3}],
            }
        ]
    )
    (other,) = db.get_statistics()["by_category"]
    assert (other["line_count"], other["unique_items"], other["net_spent"]) == (1, 1, 0.3)
    (row,) = db.get_items_summary()
    assert row["is_deposit"] is True



def test_zero_amount_line_without_a_flag_does_not_stop_the_split():
    items = [_item("1", "SOAP", 10.0, "Y"), _item("2", "INFO LINE", 0.0, "")]
    soap, info = add_tax_and_crv(items, 1.0)
    assert soap["tax_amount"] == 1.0 and not info["tax_unsplit"]


def test_search_finds_folded_deposits_by_their_own_text_only(tmp_path):
    from app.database import Database
    from app.scraper.parser import parse_any_costco_payload

    db = Database(tmp_path / "search_crv.db")
    db.init_db()
    db.upsert_receipts(parse_any_costco_payload([_wine_receipt("1017")]))
    (deposit,) = db.search_purchases("REDEMP")
    assert deposit["is_deposit"] == 1 and deposit["net_amount"] == 0.1
    (wine,) = db.search_purchases("919140")
    assert wine["item_number"] == "919140" and wine["crv_amount"] == 0.1
    assert deposit["crv_owner_id"] == wine["id"]
    assert deposit["id"] != wine["id"] and wine["crv_owner_id"] is None


def test_safeway_deposit_is_tagged_and_linked_to_its_product(tmp_path):
    from app.database import Database
    from app.scraper.parser import CRV_NAME
    from app.scraper.safeway_client import parse_safeway_receipt

    raw = {
        "barCode": "999",
        "posDateTime": "2026-06-18T10:00:00",
        "finalTotal": "5.10",
        "taxAmount": 0,
        "discountTotal": "0",
        "items": [
            {"id": "1", "name": "WATER 24PK", "quantity": 1, "regularPrice": "5.00", "regularPriceTotal": "5.00",
             "discountTotal": "0", "reducedPriceTotal": "5.00"},
            {"id": "2", "name": "CA REDEMP VAL T 1", "quantity": 1, "regularPrice": "0.10", "regularPriceTotal": "0.10",
             "discountTotal": "0", "reducedPriceTotal": "0.10"},
        ],
    }
    receipt = parse_safeway_receipt(raw)
    deposit = receipt["items"][1]
    assert deposit["default_clean_name"] == CRV_NAME and deposit["is_crv"]
    assert crv_owners(receipt["items"]) == {1: 0}

    db = Database(tmp_path / "safeway_crv.db")
    db.init_db()
    db.upsert_receipts([receipt])
    db.save_categorized_items(
        [{"item_number": "SWY-1", "clean_name": "Water", "category": "Beverages"}]
    )

    # Simulate a saved receipt under the old rules, which left Safeway deposits orphaned.
    with db.connect() as conn:
        conn.execute("UPDATE receipts SET tax_split_version = 7")
        conn.execute("UPDATE receipt_items SET crv_owner_id = NULL, crv_amount = 0")
        conn.execute("UPDATE receipt_items SET crv_amount = net_amount WHERE is_deposit = 1")
    db.init_db()

    (water,) = db.get_receipts_list(store="safeway")[0]["items"]
    assert water["item_number"] == "SWY-1" and water["crv_amount"] == 0.1
    (category,) = db.get_statistics(store="safeway")["by_category"]
    assert category["category"] == "Beverages" and category["net_spent"] == 5.1
