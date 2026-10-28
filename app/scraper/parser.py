"""Normalizer for Costco receipts (In-Warehouse, Gas Station and Online Orders)."""
import re
from typing import Any

from app.config import FUEL_CATEGORY, OTHER_CATEGORY
from app.item_number import core_item_number
from app.money import allocate_cents
from app.receipt_tax import is_crv_line

# Names for items the parser categorizes itself. Product names are stored in English, like the names
# Gemini decodes, so the catalog has one language whatever the UI language is
FUEL_NAME_PREFIX = "Costco Fuel ("
COUPON_NAME_PREFIX = "Coupon ("
CRV_NAME = "California Redemption Value (CRV)"


# A coupon's reference to its item on a Costco receipt: "TPD/1234567", "/1234567", "/ 1234567" or "#1234567".
# Only at the start of the description; otherwise a return like "KS TOWEL 12/120CT" is taken for a coupon.
DISCOUNT_REF_RE = re.compile(r"^(?:TPD/|/|#)\s*(\d{3,12})\b")
# Fuel words that are unambiguous on their own. "REGULAR"/"PREMIUM"/"FUEL" are left out: they also occur
# in grocery descriptions ("REGULAR COFFEE", "PREMIUM CHICKEN"). Whole words only.
GAS_KEYWORDS_RE = re.compile(r"\b(?:UNLEADED|GASOLINE|DIESEL)\b")
# Costco departments with sold-by-weight items (meat, deli and cheese, seafood/service deli).
# Based on real receipts, only in these is the line amount = weight × price per pound
WEIGHT_DEPARTMENTS = {19, 61, 63}


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        if isinstance(val, str):
            val = val.replace("$", "").replace(",", "").strip()
        return float(val)
    except (ValueError, TypeError):
        return default


def _normalize_date(date_str: Any) -> str:
    """Converts a date string to ISO format YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD."""
    if not date_str:
        return "1970-01-01"
    s = str(date_str).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s
    # Support the US MM/DD/YYYY format
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    return s


def _is_discount_row(raw_item: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Determines whether an itemArray line is a coupon/discount (Instant Savings / TPD).
    Returns (is_discount, target_item_number).
    """
    desc1 = str(raw_item.get("itemDescription01") or "").strip().upper()
    desc2 = str(raw_item.get("itemDescription02") or "").strip().upper()
    amount = _to_float(raw_item.get("amount"), 0.0)

    for text in (desc1, desc2):
        match = DISCOUNT_REF_RE.search(text)
        if match and amount < 0:
            return True, match.group(1)

    if amount < 0 and (
        desc1.startswith(("/", "#", "TPD"))
        or "INSTANT SAVINGS" in desc1
        or "CPN" in desc1
        or "COUPON" in desc1
    ):
        digits = re.findall(r"\d{4,12}", desc1 + " " + desc2)
        return True, (digits[0] if digits else None)

    return False, None


def spread_discount(items: list[dict[str, Any]], amount: float, base_key: str) -> float:
    """
    Spreads a receipt- or order-level discount across items with a positive base_key amount,
    in proportion to it, and returns the amount allocated. The last item gets the remainder
    so rounding cents aren't lost.
    """
    allocatable = [i for i in items if i[base_key] > 0]
    base = sum(i[base_key] for i in allocatable)
    if amount <= 0 or base <= 0:
        return 0.0
    for item, share in zip(allocatable, allocate_cents(amount, [i[base_key] for i in allocatable]), strict=True):
        item["discount_amount"] = round(item["discount_amount"] + share, 2)
        item["net_amount"] = round(item["net_amount"] - share, 2)
    return amount


def _mark_crv(item_entry: dict[str, Any]) -> None:
    item_entry["is_crv"] = True
    # A bottle deposit takes the category of the item it is charged for, so it needs no classifying
    item_entry["default_category"] = OTHER_CATEGORY
    item_entry["default_clean_name"] = CRV_NAME


def _mark_fuel(item_entry: dict[str, Any]) -> None:
    item_entry["default_category"] = FUEL_CATEGORY
    item_entry["default_clean_name"] = f"{FUEL_NAME_PREFIX}{item_entry['raw_description']})"


def parse_warehouse_or_gas_receipt(raw_receipt: dict[str, Any]) -> dict[str, Any] | None:
    """
    Parses a receipt from a Costco GraphQL response (getReceiptsWithCounts / receipts).
    Detects the type ('warehouse' or 'gas') and attaches coupons to their items.
    """
    barcode = str(
        raw_receipt.get("transactionBarcode")
        or raw_receipt.get("transaction_barcode")
        or raw_receipt.get("id")
        or ""
    ).strip()
    if not barcode:
        return None

    tx_date = _normalize_date(
        raw_receipt.get("transactionDateTime")
        or raw_receipt.get("transactionDate")
        or raw_receipt.get("transaction_date")
    )
    warehouse_name = str(
        raw_receipt.get("warehouseName")
        or raw_receipt.get("warehouseShortName")
        or raw_receipt.get("warehouse_name")
        or f"Warehouse #{raw_receipt.get('warehouseNumber', '')}"
    ).strip()

    doc_type = str(raw_receipt.get("documentType") or raw_receipt.get("receiptType") or "").lower()
    is_gas_doc = "fuel" in doc_type or "gas" in doc_type
    is_warehouse_doc = "warehouse" in doc_type
    is_gas = is_gas_doc

    raw_items = raw_receipt.get("itemArray") or raw_receipt.get("items") or []
    normalized_items: list[dict[str, Any]] = []
    pending_discounts: list[tuple[str | None, float, dict[str, Any]]] = []
    # Lines that look like fuel only by a keyword on a receipt of unknown type (with their amount and
    # raw unit price), and how many lines don't
    keyword_fuel_items: list[tuple[dict[str, Any], float, float]] = []
    non_fuel_lines = 0

    for raw_item in raw_items:
        # The receipt is already in the normalized internal format
        if "gross_amount" in raw_item and "net_amount" in raw_item and "item_number" in raw_item:
            item_copy = dict(raw_item)
            if is_crv_line(item_copy.get("raw_description")):
                if item_copy.get("default_category"):
                    item_copy["is_crv"] = True
                else:
                    _mark_crv(item_copy)
            normalized_items.append(item_copy)
            continue

        is_disc, target_item_num = _is_discount_row(raw_item)
        amount = _to_float(raw_item.get("amount"), 0.0)

        if is_disc:
            discount_val = abs(amount)
            # If target_item_num isn't given, attach the coupon to the most recent item. A bottle deposit
            # isn't one: it follows its item on the receipt, and a coupon never discounts it
            if not target_item_num:
                product = next((i for i in reversed(normalized_items) if not i.get("is_crv")), None)
                if product:
                    target_item_num = product["item_number"]
            pending_discounts.append((target_item_num, discount_val, raw_item))
            continue

        item_number = str(raw_item.get("itemNumber") or raw_item.get("item_number") or "").strip()
        desc1 = str(raw_item.get("itemDescription01") or raw_item.get("raw_description") or "").strip()
        desc2 = str(raw_item.get("itemDescription02") or "").strip()
        full_raw_desc = f"{desc1} {desc2}".strip() if desc2 and desc2 not in desc1 else desc1
        if not full_raw_desc:
            full_raw_desc = f"ITEM #{item_number}"
        if not item_number:
            item_number = re.sub(r"\W+", "_", full_raw_desc.upper())[:20] or "UNKNOWN"

        qty = _to_float(raw_item.get("unit") or raw_item.get("quantity") or raw_item.get("fuelUnitQuantity"), 1.0)
        if qty == 0:
            qty = 1.0
        upper_desc = full_raw_desc.upper()
        # Every line of a gas station receipt is fuel, and so is a line with explicit fuel fields.
        # Description keywords are checked only when the document type is unknown, and they are
        # trusted only if the whole receipt is fuel (see below): "KS DIESEL EXHAUST FLUID" is merchandise
        is_explicit_fuel = is_gas_doc or bool(raw_item.get("fuelGradeDescription"))
        is_keyword_fuel = (
            not is_explicit_fuel and not is_warehouse_doc and bool(GAS_KEYWORDS_RE.search(upper_desc))
        )
        if is_explicit_fuel:
            is_gas = True

        unit_price = _to_float(raw_item.get("itemUnitPriceAmount") or raw_item.get("unit_price"), 0.0)
        sold_by_measure = False
        if unit_price == 0.0 and qty != 0:
            unit_price = round(abs(amount / qty), 4)
        elif (
            abs(qty) == 1
            and unit_price > 0
            and abs(abs(amount) - unit_price) > 0.01
            and (is_explicit_fuel or raw_item.get("itemDepartmentNumber") in WEIGHT_DEPARTMENTS)
        ):
            # Sold by weight (or fuel): unit is 1 and the price is per pound/gallon.
            # In other departments this mismatch is a markdown, not a weight.
            qty = round(amount / unit_price, 3)
            sold_by_measure = True

        item_entry: dict[str, Any] = {
            "item_number": item_number,
            "raw_description": full_raw_desc,
            "quantity": round(qty, 3),
            "unit_price": round(unit_price, 2),
            "gross_amount": round(amount, 2),
            "discount_amount": 0.0,
            "net_amount": round(amount, 2),
            "tax_flag": str(raw_item.get("taxFlag") or ""),
            # Fuel is sold in gallons, not pounds
            "is_weight": sold_by_measure and not is_explicit_fuel,
        }
        if is_explicit_fuel:
            _mark_fuel(item_entry)
        elif is_crv_line(full_raw_desc):
            _mark_crv(item_entry)
            non_fuel_lines += 1
        elif is_keyword_fuel:
            keyword_fuel_items.append((item_entry, amount, unit_price))
        else:
            non_fuel_lines += 1

        normalized_items.append(item_entry)

    if keyword_fuel_items and non_fuel_lines == 0:
        for item_entry, amount, unit_price in keyword_fuel_items:
            _mark_fuel(item_entry)
            # Only now is it known to be fuel: unit 1 with a per-gallon price means the quantity is in gallons
            if abs(item_entry["quantity"]) == 1 and unit_price > 0 and abs(abs(amount) - unit_price) > 0.01:
                item_entry["quantity"] = round(amount / unit_price, 3)
        is_gas = True

    # Apply discounts to their items
    total_discounts_applied = 0.0
    for target_num, disc_val, raw_item in pending_discounts:
        target = None
        if target_num:
            target = next(
                (
                    i
                    for i in reversed(normalized_items)
                    if core_item_number(i["item_number"]) == core_item_number(target_num) and i["gross_amount"] > 0
                ),
                None,
            )
        # Count every coupon as savings, including one kept as its own discount line below
        total_discounts_applied = round(total_discounts_applied + disc_val, 2)
        if target is not None:
            target["discount_amount"] = round(target["discount_amount"] + disc_val, 2)
            target["net_amount"] = round(target["gross_amount"] - target["discount_amount"], 2)
            continue
        # The coupon's item isn't on the receipt: don't attach it to an arbitrary item, keep it as
        # a separate discount line (gross 0, so the discount shows up as savings, not as spend).
        # Its category is unknown, so it goes to "Other" as a parser row and isn't sent to the classifier.
        # The DISCOUNT- prefix keeps it from colliding with a real product's catalog row: the coupon row's
        # own itemNumber may be a product number
        coupon_ref = target_num or str(raw_item.get("itemNumber") or "").strip() or "UNKNOWN"
        # Its item can still be on the receipt as a return (a coupon is only applied to a sale): then the coupon
        # changes that item's taxable amount and is taxed like it. Otherwise it changes no line's taxable amount,
        # so without a flag of its own it counts as untaxed and doesn't stop the receipt's tax from being split
        returned = next(
            (i for i in normalized_items if target_num and core_item_number(i["item_number"]) == core_item_number(target_num)),
            None,
        )
        coupon_flag = str(raw_item.get("taxFlag") or (returned or {}).get("tax_flag") or "N")
        item_number = f"DISCOUNT-{coupon_ref}"
        desc = str(raw_item.get("itemDescription01") or "").strip() or f"DISCOUNT #{coupon_ref}"
        normalized_items.append(
            {
                "item_number": item_number,
                "raw_description": desc,
                # Coupon rows carry unit=1; the line takes money off, so its quantity is negative
                "quantity": -abs(round(_to_float(raw_item.get("unit"), 1.0) or 1.0, 3)),
                "unit_price": 0.0,
                "gross_amount": 0.0,
                "discount_amount": disc_val,
                "net_amount": -disc_val,
                "tax_flag": coupon_flag,
                "is_weight": False,
                "default_category": OTHER_CATEGORY,
                "default_clean_name": f"{COUPON_NAME_PREFIX}{desc})",
            }
        )

    subtotal = _to_float(raw_receipt.get("subTotal") or raw_receipt.get("subtotal"), 0.0)
    tax = _to_float(raw_receipt.get("taxes") or raw_receipt.get("tax"), 0.0)
    total = _to_float(raw_receipt.get("total"), 0.0)
    instant_savings = _to_float(
        raw_receipt.get("instantSavings") or raw_receipt.get("instant_savings"),
        total_discounts_applied,
    )
    if instant_savings == 0.0 and total_discounts_applied > 0:
        instant_savings = total_discounts_applied

    items_net_sum = round(sum(i["net_amount"] for i in normalized_items), 2)
    if subtotal == 0.0:
        subtotal = items_net_sum
    if total == 0.0:
        total = round(subtotal + tax, 2)

    receipt_type = str(raw_receipt.get("receipt_type") or ("gas" if is_gas else "warehouse"))

    return {
        "transaction_barcode": barcode,
        "transaction_date": tx_date,
        "receipt_type": receipt_type,
        "warehouse_name": warehouse_name,
        "subtotal": round(subtotal, 2),
        "tax": round(tax, 2),
        "total": round(total, 2),
        "instant_savings": round(instant_savings, 2),
        "items": normalized_items,
        "raw_json": raw_receipt,
    }


def parse_online_order(raw_order: dict[str, Any]) -> dict[str, Any] | None:
    """Parses a Costco.com online order (getOnlineOrders / bcOrders) into the common receipt format."""
    order_num = str(
        raw_order.get("orderNumber")
        or raw_order.get("orderHeaderId")
        or raw_order.get("transaction_barcode")
        or ""
    ).strip()
    if not order_num:
        return None

    barcode = order_num if order_num.startswith("ONLINE-") else f"ONLINE-{order_num}"
    tx_date = _normalize_date(
        raw_order.get("orderPlacedDate")
        or raw_order.get("orderedDate")
        or raw_order.get("transaction_date")
    )

    line_items = raw_order.get("orderLineItems") or raw_order.get("lineItems") or raw_order.get("items") or []
    if not line_items:
        # getOrderDetails response: lines are grouped by shipping address
        line_items = [
            line
            for ship_to in raw_order.get("orderShipTos") or []
            for line in ship_to.get("orderLineItems") or []
        ]
    normalized_items: list[dict[str, Any]] = []
    total_discount = 0.0

    fees = 0.0

    for idx, line in enumerate(line_items, start=1):
        if line.get("isFeeItem"):
            # Fees (delivery, disposal, etc.) aren't merchandise: don't classify them or count them in categories
            fee = _to_float(line.get("merchandiseTotal"), 0.0)
            if fee == 0.0:
                fee_qty = _to_float(line.get("quantity") or line.get("orderedTotalQuantity"), 1.0)
                fee = _to_float(line.get("unitPrice"), 0.0) * (fee_qty if fee_qty > 0 else 1.0)
            fees = round(fees + fee, 2)
            continue
        item_num = str(line.get("itemNumber") or line.get("item_number") or f"ONL-{idx}").strip()
        desc = str(
            line.get("itemDescription")
            or line.get("productName")
            or line.get("raw_description")
            or f"Online Item #{item_num}"
        ).strip()
        qty = _to_float(line.get("quantity") or line.get("orderedTotalQuantity"), 1.0)
        if qty <= 0:
            qty = 1.0
        unit_price = _to_float(line.get("unitPrice") or line.get("price") or line.get("unit_price"), 0.0)
        gross = _to_float(line.get("ExtendedPrice") or line.get("merchandiseTotal") or line.get("gross_amount"), 0.0)
        if gross == 0.0 and unit_price > 0:
            gross = round(unit_price * qty, 2)
        if unit_price == 0.0 and gross > 0:
            unit_price = round(gross / qty, 2)

        disc = abs(_to_float(line.get("discountAmount") or line.get("discount_amount") or line.get("promoDiscount"), 0.0))
        net = round(gross - disc, 2)
        total_discount = round(total_discount + disc, 2)

        normalized_items.append(
            {
                "item_number": item_num,
                "raw_description": desc,
                "quantity": round(qty, 3),
                "unit_price": round(unit_price, 2),
                "gross_amount": round(gross, 2),
                "discount_amount": round(disc, 2),
                "net_amount": net,
                "tax_flag": str(line.get("taxFlag") or line.get("tax_flag") or ""),
            }
        )

    # getOrderDetails has no per-line tax flag, only the order's tax. With a single item that tax is all its own;
    # with several it can't be told which are taxable (groceries aren't), so the tax isn't split per item.
    # Fees and shipping aren't items, so when the order has them its tax may be theirs. A flag the line does
    # carry (e.g. in an imported file) is kept
    shipping = _to_float(raw_order.get("shippingAndHandling") or raw_order.get("shippingTotal"), 0.0)
    has_charges = bool(fees) or bool(shipping)
    if len(normalized_items) == 1 and not normalized_items[0]["tax_flag"] and not has_charges:
        normalized_items[0]["tax_flag"] = "Y"

    # getOrderDetails only returns the discount at the order level; spread it across items
    # in proportion to their amounts so category stats reflect what was actually paid
    order_discount = abs(_to_float(raw_order.get("discountAmount"), 0.0))
    unallocated = round(order_discount - total_discount, 2)
    # Spread by the amount after line discounts, so an already discounted line can't go negative
    allocated = spread_discount(normalized_items, unallocated, "net_amount")
    total_discount = round(total_discount + allocated, 2)

    subtotal = _to_float(raw_order.get("merchandiseTotal") or raw_order.get("subTotal") or raw_order.get("subtotal"), 0.0)
    tax = _to_float(raw_order.get("orderTax") or raw_order.get("totalTax") or raw_order.get("tax"), 0.0)
    total = _to_float(raw_order.get("orderTotal") or raw_order.get("total"), 0.0)

    items_net_sum = round(sum(i["net_amount"] for i in normalized_items), 2)
    if subtotal == 0.0:
        subtotal = items_net_sum
    if total == 0.0:
        total = round(subtotal + tax + shipping + fees, 2)

    return {
        "transaction_barcode": barcode,
        "transaction_date": tx_date,
        "receipt_type": "online",
        "warehouse_name": "Costco.com Online",
        "subtotal": round(subtotal, 2),
        "tax": round(tax, 2),
        "total": round(total, 2),
        "instant_savings": round(
            _to_float(raw_order.get("instantSavings") or raw_order.get("totalDiscount"), total_discount),
            2,
        ),
        "items": normalized_items,
        "raw_json": raw_order,
    }


def parse_any_costco_payload(payload: Any) -> list[dict[str, Any]]:
    """
    Generic parser: takes any JSON response from the Costco GraphQL API
    (getReceiptsWithCounts, getOnlineOrders, or an array of receipts/orders) and returns
    a list of normalized receipts.
    """
    results: list[dict[str, Any]] = []

    if isinstance(payload, list):
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            if (
                "orderNumber" in entry
                or "orderLineItems" in entry
                or "orderShipTos" in entry
                or entry.get("receipt_type") == "online"
            ):
                parsed = parse_online_order(entry)
            else:
                parsed = parse_warehouse_or_gas_receipt(entry)
            if parsed:
                results.append(parsed)
        return results

    if not isinstance(payload, dict):
        return results

    # Unwrap a nested GraphQL response {"data": ...}
    data_node = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    # 1. In-Warehouse & Gas receipts (receiptsWithCounts / receipts)
    rwc = data_node.get("receiptsWithCounts") or data_node.get("getReceiptsWithCounts")
    if isinstance(rwc, dict) and isinstance(rwc.get("receipts"), list):
        for r in rwc["receipts"]:
            parsed = parse_warehouse_or_gas_receipt(r)
            if parsed:
                results.append(parsed)
    elif isinstance(data_node.get("receipts"), list):
        for r in data_node["receipts"]:
            parsed = parse_warehouse_or_gas_receipt(r)
            if parsed:
                results.append(parsed)

    # 2. Online Orders (getOnlineOrders)
    online_node = data_node.get("getOnlineOrders")
    if isinstance(online_node, list):
        for group in online_node:
            if isinstance(group, dict) and isinstance(group.get("bcOrders"), list):
                for o in group["bcOrders"]:
                    parsed = parse_online_order(o)
                    if parsed:
                        results.append(parsed)
    elif isinstance(online_node, dict) and isinstance(online_node.get("bcOrders"), list):
        for o in online_node["bcOrders"]:
            parsed = parse_online_order(o)
            if parsed:
                results.append(parsed)

    if isinstance(data_node.get("bcOrders"), list):
        for o in data_node["bcOrders"]:
            parsed = parse_online_order(o)
            if parsed:
                results.append(parsed)

    # 3. A single receipt or online order
    if not results:
        if "orderNumber" in data_node or "orderLineItems" in data_node:
            parsed = parse_online_order(data_node)
            if parsed:
                results.append(parsed)
        elif "transactionBarcode" in data_node or "transaction_barcode" in data_node:
            parsed = parse_warehouse_or_gas_receipt(data_node)
            if parsed:
                results.append(parsed)

    return results
