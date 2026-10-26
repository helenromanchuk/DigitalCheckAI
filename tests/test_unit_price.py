"""Tests for price per pound and per ounce."""
import pytest

from app.scraper.parser import parse_any_costco_payload
from app.unit_price import add_unit_prices, package_weight_lb


@pytest.mark.parametrize(
    "text, expected_lb",
    [
        ("Whole Bay Leaves (2 oz)", 0.125),
        ("BAYLEAVES2OZ SL270 T10H6P720", 0.125),
        ("HONEYCRISP 2.27 KG / 5 LB. BAG", 5.0),
        ("KS 3LBS ALMONDS", 3.0),
        ("Rice 25 Pound", 25.0),
        ("Coffee 12 x 16 oz", 12.0),
        ("Nuts 2 X 3LB", 6.0),
        ("Kirkland Water 40 x 16.9 fl oz", None),
        ("Kirkland Signature Frozen Seafood Mix", None),
        ("", None),
    ],
)
def test_package_weight_from_name(text, expected_lb):
    assert package_weight_lb(text) == expected_lb


def test_unit_prices_use_clean_name_then_receipt_text():
    row = add_unit_prices(
        {"clean_name": "Honeycrisp Apples (5 lb bag)", "raw_description": "HONEYCRISP", "quantity": 2, "net_amount": 15.78},
        "net_amount",
        "quantity",
    )
    # 2 bags of 5 lb for $15.78 → $1.578/lb
    assert row["price_per_lb"] == 1.578
    assert row["price_per_oz"] == round(15.78 / 160, 4)

    from_raw = add_unit_prices(
        {"clean_name": "Whole Bay Leaves", "raw_description": "BAYLEAVES2OZ", "quantity": 1, "net_amount": 3.39},
        "net_amount",
        "quantity",
    )
    assert from_raw["price_per_oz"] == 1.695


def test_unit_prices_for_weight_items_and_missing_weight():
    pork = add_unit_prices(
        {"clean_name": "Pork Tenderloin", "raw_description": "PORK TENDER", "quantity": 5.421, "is_weight": 1, "net_amount": 18.92},
        "net_amount",
        "quantity",
    )
    assert pork["price_per_lb"] == round(18.92 / 5.421, 4)

    seafood = add_unit_prices(
        {"clean_name": "Frozen Seafood Mix", "raw_description": "SEAFOOD MIX", "quantity": 1, "net_amount": 19.99},
        "net_amount",
        "quantity",
    )
    assert seafood["price_per_lb"] is None and seafood["price_per_oz"] is None


def test_parser_marks_weight_items_but_not_fuel():
    receipt = {
        "transactionBarcode": "W-1",
        "transactionDateTime": "2026-09-11T10:20:00",
        "receiptType": "In-Warehouse",
        "subTotal": 113.92,
        "total": 113.92,
        "itemArray": [
            {"itemNumber": "21647", "itemDescription01": "PORK TENDER", "unit": 1, "amount": 18.92, "itemUnitPriceAmount": 3.49, "itemDepartmentNumber": 61},
            {"itemNumber": "1", "itemDescription01": "MILK", "unit": 1, "amount": 5.0, "itemUnitPriceAmount": 5.0},
            # Markdown in the garden department: sold for $90 at a $149.99 price, not a weight
            {"itemNumber": "1759323", "itemDescription01": "PLANTER", "unit": 1, "amount": 90, "itemUnitPriceAmount": 149.99,
             "itemDepartmentNumber": 27},
        ],
    }
    gas = {
        "transactionBarcode": "G-1",
        "transactionDateTime": "2026-10-21T10:31:00",
        "documentType": "FuelReceipts",
        "receiptType": "Gas Station",
        "total": 44.62,
        "itemArray": [
            {"itemNumber": "800599", "itemDescription01": "REGULAR GAS", "unit": 1, "amount": 44.62,
             "itemUnitPriceAmount": 5.659, "fuelGradeDescription": "Regular"},
        ],
    }
    parsed = {r["transaction_barcode"]: r for r in parse_any_costco_payload([receipt, gas])}

    items = {i["item_number"]: i for i in parsed["W-1"]["items"]}
    assert items["21647"]["is_weight"] is True
    assert items["1"]["is_weight"] is False
    assert (items["1759323"]["quantity"], items["1759323"]["is_weight"]) == (1.0, False)

    fuel = parsed["G-1"]["items"][0]
    assert fuel["quantity"] == 7.885
    assert fuel["is_weight"] is False
