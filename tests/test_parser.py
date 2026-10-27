"""Tests for the Costco receipt parser (In-Warehouse, Gas Station, Online Orders and TPD coupons)."""
from app.scraper.parser import parse_any_costco_payload


def test_parse_warehouse_receipt_with_tpd_coupons():
    payload = {
        "data": {
            "receiptsWithCounts": {
                "receipts": [
                    {
                        "transactionBarcode": "24000111",
                        "transactionDate": "10/15/2026",
                        "receiptType": "In-Warehouse",
                        "warehouseName": "SAN JOSE #148",
                        "subTotal": 44.48,
                        "taxes": 2.10,
                        "total": 46.58,
                        "instantSavings": 5.50,
                        "itemArray": [
                            {
                                "itemNumber": "678910",
                                "itemDescription01": "KS ORG EVOO",
                                "unit": 1,
                                "amount": 20.00,
                                "taxFlag": "N",
                            },
                            {
                                "itemNumber": "1299331",
                                "itemDescription01": "CHARMIN ULTRA SOFT",
                                "unit": 1,
                                "amount": 29.98,
                                "taxFlag": "Y",
                            },
                            {
                                "itemNumber": "356112",
                                "itemDescription01": "TPD/1299331",
                                "unit": -1,
                                "amount": -5.50,
                                "taxFlag": "Y",
                            },
                        ],
                    }
                ]
            }
        }
    }

    receipts = parse_any_costco_payload(payload)
    assert len(receipts) == 1
    r = receipts[0]
    assert r["transaction_barcode"] == "24000111"
    assert r["transaction_date"] == "2026-10-15"
    assert r["receipt_type"] == "warehouse"
    assert len(r["items"]) == 2

    evoo = next(i for i in r["items"] if i["item_number"] == "678910")
    assert evoo["gross_amount"] == 20.00
    assert evoo["discount_amount"] == 0.0
    assert evoo["net_amount"] == 20.00

    charmin = next(i for i in r["items"] if i["item_number"] == "1299331")
    assert charmin["gross_amount"] == 29.98
    assert charmin["discount_amount"] == 5.50
    assert charmin["net_amount"] == 24.48


def test_parse_gas_and_online_orders():
    gas_and_online = [
        {
            "transactionBarcode": "GAS-999",
            "transactionDate": "2026-10-01",
            "documentType": "FuelReceipt",
            "warehouseName": "SUNNYVALE GAS",
            "total": 45.00,
            "itemArray": [
                {
                    "itemNumber": "800877",
                    "itemDescription01": "UNLEADED GASOLINE 87",
                    "fuelGradeDescription": "Regular",
                    "unit": 10.0,
                    "amount": 45.00,
                }
            ],
        },
        {
            "orderNumber": "987654321",
            "orderPlacedDate": "2026-10-05T12:00:00",
            "merchandiseTotal": 199.99,
            "orderTax": 18.00,
            "orderTotal": 217.99,
            "orderLineItems": [
                {
                    "itemNumber": "1799882",
                    "itemDescription": "APPLE AIRPODS PRO",
                    "quantity": 1,
                    "unitPrice": 229.99,
                    "merchandiseTotal": 229.99,
                    "discountAmount": 30.00,
                }
            ],
        },
    ]

    parsed = parse_any_costco_payload(gas_and_online)
    assert len(parsed) == 2

    gas = next(p for p in parsed if p["receipt_type"] == "gas")
    assert gas["transaction_barcode"] == "GAS-999"
    assert gas["items"][0]["default_category"] == "Auto & fuel (Gas Station)"

    online = next(p for p in parsed if p["receipt_type"] == "online")
    assert online["transaction_barcode"] == "ONLINE-987654321"
    assert online["items"][0]["gross_amount"] == 229.99
    assert online["items"][0]["discount_amount"] == 30.00
    assert online["items"][0]["net_amount"] == 199.99


def test_hash_and_slash_coupons_and_weight_items():
    # Excerpt from a real receipt: coupons in "#1327673" and "/ 1881410" formats, pork sold by weight
    receipt = {
        "transactionBarcode": "21000000000002609111020",
        "transactionDateTime": "2026-09-11T10:20:00",
        "documentType": "WarehouseReceiptDetail",
        "receiptType": "In-Warehouse",
        "warehouseName": "SAN JOSE BUS CTR",
        "subTotal": 34.20,
        "taxes": 0,
        "total": 34.20,
        "itemArray": [
            {"itemNumber": "1327673", "itemDescription01": "HONEYCRISP", "unit": 1, "amount": 8.89, "itemUnitPriceAmount": 8.89},
            {"itemNumber": "387027", "itemDescription01": "#1327673", "unit": -1, "amount": -1, "itemUnitPriceAmount": 0},
            {"itemNumber": "1881410", "itemDescription01": "FD STRWBERRY", "unit": 1, "amount": 9.89, "itemUnitPriceAmount": 9.89},
            {"itemNumber": "388948", "itemDescription01": "/ 1881410", "unit": -1, "amount": -2.5, "itemUnitPriceAmount": 0},
            {"itemNumber": "21647", "itemDescription01": "PORK TENDER", "unit": 1, "amount": 18.92, "itemUnitPriceAmount": 3.49, "itemDepartmentNumber": 61},
        ],
    }

    r = parse_any_costco_payload([receipt])[0]
    items = {i["item_number"]: i for i in r["items"]}

    # Coupons didn't become separate items; they were subtracted from their items
    assert set(items) == {"1327673", "1881410", "21647"}
    assert (items["1327673"]["discount_amount"], items["1327673"]["net_amount"]) == (1.0, 7.89)
    assert (items["1881410"]["discount_amount"], items["1881410"]["net_amount"]) == (2.5, 7.39)
    assert r["instant_savings"] == 3.5

    # Sold by weight: quantity is the weight (18.92 / 3.49 ≈ 5.421 lb), the amount is unchanged
    assert items["21647"]["quantity"] == 5.421
    assert items["21647"]["net_amount"] == 18.92
    assert round(sum(i["net_amount"] for i in r["items"]), 2) == r["total"]


def test_return_line_with_slash_is_not_a_coupon():
    # A return with "/" in its description must not be subtracted from the last item on the receipt
    receipt = {
        "transactionBarcode": "R-RETURN",
        "transactionDate": "2026-10-10",
        "itemArray": [
            {"itemNumber": "111", "itemDescription01": "KS TOWEL 12/120CT", "unit": -1, "amount": -19.99},
            {"itemNumber": "222", "itemDescription01": "BANANAS", "unit": 1, "amount": 1.99},
        ],
    }
    items = {i["item_number"]: i for i in parse_any_costco_payload([receipt])[0]["items"]}
    assert items["111"]["net_amount"] == -19.99
    assert (items["222"]["discount_amount"], items["222"]["net_amount"]) == (0.0, 1.99)


def test_unmatched_coupon_stays_separate_line():
    receipt = {
        "transactionBarcode": "R-ORPHAN",
        "transactionDate": "2026-10-10",
        "itemArray": [
            {"itemNumber": "222", "itemDescription01": "BANANAS", "unit": 1, "amount": 1.99},
            {"itemNumber": "387027", "itemDescription01": "/ 9999999", "unit": -1, "amount": -3.00},
        ],
    }
    r = parse_any_costco_payload([receipt])[0]
    items = {i["item_number"]: i for i in r["items"]}
    assert items["222"]["net_amount"] == 1.99
    assert items["DISCOUNT-9999999"]["net_amount"] == -3.00
    assert round(sum(i["net_amount"] for i in r["items"]), 2) == -1.01


def test_online_order_level_discount_and_fee_lines():
    # getOrderDetails format after _flatten_order_details: the discount is only at the order level
    order = {
        "orderNumber": "555",
        "orderPlacedDate": "2026-10-05",
        "merchandiseTotal": 100.00,
        "discountAmount": 20.00,
        "orderTotal": 85.00,
        "orderLineItems": [
            {"itemNumber": "A1", "itemDescription": "TV", "unitPrice": 75, "quantity": 1, "merchandiseTotal": 75},
            {"itemNumber": "B2", "itemDescription": "MOUNT", "unitPrice": 25, "quantity": 1, "merchandiseTotal": 25},
            {"itemNumber": "FEE1", "itemDescription": "DELIVERY FEE", "unitPrice": 5, "quantity": 1,
             "merchandiseTotal": 5, "isFeeItem": True},
        ],
    }
    r = parse_any_costco_payload([order])[0]
    items = {i["item_number"]: i for i in r["items"]}
    assert set(items) == {"A1", "B2"}
    assert (items["A1"]["discount_amount"], items["A1"]["net_amount"]) == (15.0, 60.0)
    assert (items["B2"]["discount_amount"], items["B2"]["net_amount"]) == (5.0, 20.0)
    assert r["instant_savings"] == 20.0


def test_grocery_words_on_warehouse_receipt_are_not_fuel():
    receipt = {
        "transactionBarcode": "21000000000002610261200",
        "transactionDateTime": "2026-10-26T12:00:00",
        "documentType": "WarehouseReceiptDetail",
        "receiptType": "In-Warehouse",
        "itemArray": [
            {"itemNumber": "1000001", "itemDescription01": "REGULAR COFFEE", "unit": 1, "amount": 14.99},
            {"itemNumber": "1000002", "itemDescription01": "MILK", "unit": 1, "amount": 4.59},
            {"itemNumber": "1000003", "itemDescription01": "PREMIUM CHICKEN", "unit": 1, "amount": 9.99},
        ],
    }

    r = parse_any_costco_payload([receipt])[0]
    assert r["receipt_type"] == "warehouse"
    assert all("default_category" not in i for i in r["items"])


def test_fuel_keywords_used_only_when_document_type_is_unknown():
    receipt = {
        "transactionBarcode": "GAS-NO-DOCTYPE",
        "transactionDateTime": "2026-10-26T12:00:00",
        "itemArray": [{"itemNumber": "800877", "itemDescription01": "UNLEADED", "unit": 10, "amount": 45.0}],
    }

    r = parse_any_costco_payload([receipt])[0]
    assert r["receipt_type"] == "gas"
    assert r["items"][0]["default_category"] == "Auto & fuel (Gas Station)"


def test_unmatched_coupon_counts_as_savings_with_negative_quantity():
    receipt = {
        "transactionBarcode": "R-COUPON",
        "transactionDateTime": "2026-10-26T12:00:00",
        "documentType": "WarehouseReceiptDetail",
        "itemArray": [
            {"itemNumber": "111", "itemDescription01": "MILK", "unit": 1, "amount": 5.0},
            {"itemNumber": "999", "itemDescription01": "/ 222", "unit": 1, "amount": -2.0},
        ],
    }

    r = parse_any_costco_payload([receipt])[0]
    # Keyed by the coupon's target, not by its own itemNumber, which may be a real product number
    coupon = next(i for i in r["items"] if i["item_number"] == "DISCOUNT-222")
    assert r["instant_savings"] == 2.0
    assert r["subtotal"] == 3.0
    assert coupon["quantity"] == -1.0
    # A discount, not negative spend: item-level discounts add up to the receipt's savings
    assert (coupon["gross_amount"], coupon["discount_amount"], coupon["net_amount"]) == (0.0, 2.0, -2.0)
    assert sum(i["discount_amount"] for i in r["items"]) == r["instant_savings"]
    assert coupon["default_category"] == "Other"


def test_fuel_keyword_in_mixed_receipt_of_unknown_type_is_merchandise():
    receipt = {
        "transactionBarcode": "R-DEF",
        "transactionDateTime": "2026-10-26T12:00:00",
        "itemArray": [
            {"itemNumber": "555", "itemDescription01": "KS DIESEL EXHAUST FLUID", "unit": 1, "amount": 12.99},
            {"itemNumber": "111", "itemDescription01": "MILK", "unit": 1, "amount": 4.59},
        ],
    }

    r = parse_any_costco_payload([receipt])[0]
    assert r["receipt_type"] == "warehouse"
    assert all("default_category" not in i for i in r["items"])


def test_fee_without_total_is_unit_price_times_quantity():
    order = {
        "orderNumber": "FEE-QTY",
        "orderPlacedDate": "2026-10-26",
        "orderLineItems": [
            {"itemNumber": "A1", "itemDescription": "TV", "unitPrice": 100, "quantity": 1, "merchandiseTotal": 100},
            {"itemNumber": "FEE1", "itemDescription": "DISPOSAL FEE", "unitPrice": 25, "quantity": 2, "isFeeItem": True},
        ],
    }

    r = parse_any_costco_payload([order])[0]
    assert r["total"] == 150.0


def test_spread_discount_gives_rounding_remainder_to_last_item():
    from app.scraper.parser import spread_discount

    items = [
        {"gross_amount": 10.0, "discount_amount": 0.0, "net_amount": 10.0},
        {"gross_amount": 10.0, "discount_amount": 0.0, "net_amount": 10.0},
        {"gross_amount": 10.0, "discount_amount": 0.0, "net_amount": 10.0},
        {"gross_amount": -5.0, "discount_amount": 0.0, "net_amount": -5.0},
    ]

    assert spread_discount(items, 1.0, "gross_amount") == 1.0
    assert [i["discount_amount"] for i in items] == [0.33, 0.33, 0.34, 0.0]
    assert [i["net_amount"] for i in items] == [9.67, 9.67, 9.66, -5.0]


def test_keyword_line_on_mixed_receipt_keeps_its_quantity():
    receipt = {
        "transactionBarcode": "R-DEF-MARKDOWN",
        "transactionDateTime": "2026-10-26T12:00:00",
        "itemArray": [
            {"itemNumber": "555", "itemDescription01": "KS DIESEL EXHAUST FLUID", "unit": 1, "amount": 9.99,
             "itemUnitPriceAmount": 12.99},
            {"itemNumber": "111", "itemDescription01": "MILK", "unit": 1, "amount": 4.59},
        ],
    }

    r = parse_any_costco_payload([receipt])[0]
    assert next(i for i in r["items"] if i["item_number"] == "555")["quantity"] == 1.0


def test_keyword_fuel_receipt_of_unknown_type_gets_gallons():
    receipt = {
        "transactionBarcode": "GAS-GALLONS",
        "transactionDateTime": "2026-10-26T12:00:00",
        "itemArray": [{"itemNumber": "800877", "itemDescription01": "UNLEADED", "unit": 1, "amount": 44.62,
                       "itemUnitPriceAmount": 5.659}],
    }

    r = parse_any_costco_payload([receipt])[0]
    assert r["items"][0]["quantity"] == 7.885


def test_order_discount_spread_by_amount_after_line_discounts():
    order = {
        "orderNumber": "ORD-NET",
        "orderPlacedDate": "2026-10-26",
        "discountAmount": 70,
        "orderLineItems": [
            {"itemNumber": "A1", "itemDescription": "FREE ITEM", "unitPrice": 50, "quantity": 1,
             "merchandiseTotal": 50, "discountAmount": 50},
            {"itemNumber": "B2", "itemDescription": "PAID ITEM", "unitPrice": 50, "quantity": 1, "merchandiseTotal": 50},
        ],
    }

    items = {i["item_number"]: i for i in parse_any_costco_payload([order])[0]["items"]}
    assert items["A1"]["net_amount"] == 0.0
    assert items["B2"]["net_amount"] == 30.0
