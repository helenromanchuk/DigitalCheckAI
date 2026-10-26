"""Тесты парсера чеков Costco (In-Warehouse, Gas Station, Online Orders и купоны TPD)."""
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
    assert gas["items"][0]["default_category"] == "Авто и заправка (Gas Station)"

    online = next(p for p in parsed if p["receipt_type"] == "online")
    assert online["transaction_barcode"] == "ONLINE-987654321"
    assert online["items"][0]["gross_amount"] == 229.99
    assert online["items"][0]["discount_amount"] == 30.00
    assert online["items"][0]["net_amount"] == 199.99


def test_hash_and_slash_coupons_and_weight_items():
    # Фрагмент реального чека: купоны в форматах "#1327673" и "/ 1881410", свинина на вес
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

    # Купоны не стали отдельными товарами, а вычлись из своих позиций
    assert set(items) == {"1327673", "1881410", "21647"}
    assert (items["1327673"]["discount_amount"], items["1327673"]["net_amount"]) == (1.0, 7.89)
    assert (items["1881410"]["discount_amount"], items["1881410"]["net_amount"]) == (2.5, 7.39)
    assert r["instant_savings"] == 3.5

    # Весовой товар: количество — это вес (18.92 / 3.49 ≈ 5.421 lb), сумма не меняется
    assert items["21647"]["quantity"] == 5.421
    assert items["21647"]["net_amount"] == 18.92
    assert round(sum(i["net_amount"] for i in r["items"]), 2) == r["total"]
