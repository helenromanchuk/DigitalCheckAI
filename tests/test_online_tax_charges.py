"""Online order tax must not assign tax on omitted fees or shipping to merchandise."""
import pytest

from app.database import Database
from app.receipt_tax import SPLIT_RULES_VERSION
from app.scraper.parser import parse_online_order


def _order(charge, flag_key="taxFlag", taxable_items=1):
    amounts = [100] if taxable_items == 1 else [60, 40]
    lines = [
        {"itemNumber": str(i), "itemDescription": f"Taxable item {i}", "merchandiseTotal": amount, flag_key: "Y"}
        for i, amount in enumerate(amounts, start=1)
    ]
    lines.append({"itemNumber": "U", "itemDescription": "GROCERIES", "merchandiseTotal": 20, flag_key: "N"})
    order = {
        "orderNumber": "ONLINE-CHARGES",
        "orderPlacedDate": "2026-10-01",
        "merchandiseTotal": 120,
        "orderTax": 11 if charge else 10,
        "orderTotal": 141 if charge else 130,
        "orderLineItems": lines,
    }
    if charge == "fee":
        lines.append({"itemNumber": "F", "merchandiseTotal": 10, "isFeeItem": True, flag_key: "Y"})
    elif charge in ("shippingAndHandling", "shippingTotal"):
        order[charge] = 10
    return order


@pytest.mark.parametrize("charge", ["fee", "shippingAndHandling", "shippingTotal", "unlisted"])
@pytest.mark.parametrize("flag_key", ["taxFlag", "tax_flag"])
@pytest.mark.parametrize("taxable_items", [1, 2])
def test_online_charges_leave_merchandise_tax_unsplit(tmp_path, charge, flag_key, taxable_items):
    parsed = parse_online_order(_order(charge, flag_key, taxable_items))
    assert [item["tax_flag"] for item in parsed["items"]] == ["Y"] * taxable_items + ["N"]
    db = Database(tmp_path / "charges.db")
    db.upsert_receipts([parsed])

    (receipt,) = db.get_receipts_list()
    assert (receipt["tax"], receipt["total"]) == (11, 141)
    taxable = receipt["items"][:taxable_items]
    assert all(item["tax_amount"] is None and item["tax_unsplit"] for item in taxable)
    groceries = receipt["items"][-1]
    assert groceries["tax_amount"] is None and groceries["tax_unsplit"] is False
    assert len(receipt["items"]) == taxable_items + 1
    summaries = {item["item_number"]: item for item in db.get_items_summary()}
    assert all(summaries[item["item_number"]]["tax_unknown"] for item in taxable)
    with db.connect() as conn:
        assert [row[0] for row in conn.execute("SELECT tax_flag FROM receipt_items ORDER BY id")] == (
            ["Y"] * taxable_items + ["N"]
        )


def test_single_taxable_item_does_not_absorb_fee_tax(tmp_path):
    order = _order("fee")
    order["orderLineItems"] = [line for line in order["orderLineItems"] if line["itemNumber"] != "U"]
    order.update(merchandiseTotal=100, orderTotal=121)
    db = Database(tmp_path / "single-item.db")
    db.upsert_receipts([parse_online_order(order)])
    (item,) = db.get_receipts_list()[0]["items"]
    assert item["net_amount"] == 100
    assert item["tax_amount"] is None and item["tax_unsplit"] is True


def test_online_order_without_charges_still_allocates_explicit_tax(tmp_path):
    db = Database(tmp_path / "no-charges.db")
    db.upsert_receipts([parse_online_order(_order(None, taxable_items=2))])
    first, second, groceries = db.get_receipts_list()[0]["items"]
    assert (first["tax_amount"], second["tax_amount"], groceries["tax_amount"]) == (6, 4, None)
    assert not any(item["tax_unsplit"] for item in (first, second, groceries))


def test_tax_free_online_charges_do_not_mark_item_totals_unknown(tmp_path):
    order = _order("fee")
    order.update(orderTax=0, orderTotal=130)
    db = Database(tmp_path / "tax-free.db")
    db.upsert_receipts([parse_online_order(order)])
    assert all(item["tax_amount"] is None and not item["tax_unsplit"] for item in db.get_receipts_list()[0]["items"])


@pytest.mark.parametrize("has_raw_json", [False, True])
def test_restart_repairs_old_allocations_without_losing_flags(tmp_path, has_raw_json):
    db = Database(tmp_path / "old-charges.db")
    db.upsert_receipts([parse_online_order(_order("fee"))])
    with db.connect() as conn:
        conn.execute("UPDATE receipt_items SET tax_amount = 11, tax_unsplit = 0 WHERE item_number = '1'")
        conn.execute("UPDATE receipts SET tax_split_version = 8")
        if not has_raw_json:
            conn.execute("UPDATE receipts SET raw_json = '{}'")

    for _ in range(2):
        db.init_db()
        taxable, groceries = db.get_receipts_list()[0]["items"]
        assert taxable["tax_amount"] is None and taxable["tax_unsplit"] is True
        assert groceries["tax_amount"] is None and groceries["tax_unsplit"] is False
        with db.connect() as conn:
            assert conn.execute("SELECT tax_split_version FROM receipts").fetchone()[0] == SPLIT_RULES_VERSION
            assert [row[0] for row in conn.execute("SELECT tax_flag FROM receipt_items ORDER BY id")] == ["Y", "N"]
