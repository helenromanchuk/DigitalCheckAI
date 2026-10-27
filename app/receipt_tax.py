"""Per-item tax and CRV for a receipt: the receipt's tax spread over taxable items, plus the bottle deposit lines."""
import re
from bisect import bisect_left
from typing import Any

# "CA REDEMP VAL T EI/919140": a CRV line for item 919140; "T" means the deposit itself is taxed, "N" that it isn't.
# Text after the item number (a second description field) is allowed
_CRV_RE = re.compile(r"^CA REDEMP VAL\s+([TN])\b[^/]*/\s*(\d+)\b", re.IGNORECASE)

# California sales tax tops out around 11%; a higher implied rate means the taxable lines nearly cancel out
# (a purchase and a return on one receipt), and splitting the tax over them would give absurd shares
MAX_TAX_RATE = 0.15


def _crv_link(raw_description: str | None) -> tuple[str, bool] | None:
    """Item number a CRV line belongs to and whether the deposit is taxable; None for other lines."""
    match = _CRV_RE.match((raw_description or "").strip())
    if not match:
        return None
    return match.group(2), match.group(1).upper() == "T"


def is_crv_line(raw_description: str | None) -> bool:
    """Whether a receipt line is a California bottle deposit (CRV)."""
    return _crv_link(raw_description) is not None


def crv_owners(items: list[dict[str, Any]]) -> dict[int, int]:
    """Maps the index of each CRV line to the index of the receipt line it belongs to, from the line descriptions.

    The owner is the nearest earlier line with the linked item number, else the first later one; a CRV line
    whose item isn't on the receipt has no owner.
    """
    links = {i: link for i, row in enumerate(items) if (link := _crv_link(row.get("raw_description")))}
    lines_by_item: dict[str, list[int]] = {}
    for i, row in enumerate(items):
        if i not in links:
            lines_by_item.setdefault(str(row.get("item_number")), []).append(i)

    owners: dict[int, int] = {}
    for index, (item_number, _) in links.items():
        candidates = lines_by_item.get(item_number)
        if not candidates:
            continue
        position = bisect_left(candidates, index)
        owners[index] = candidates[position - 1] if position > 0 else candidates[0]
    return owners


def empty_tax_crv() -> dict[str, Any]:
    """Tax and CRV totals of an item with no purchases to add up."""
    return {"tax_amount": None, "crv_amount": 0.0, "tax_crv": 0.0, "tax_partial": False}


def add_tax_and_crv(items: list[dict[str, Any]], receipt_tax: float) -> list[dict[str, Any]]:
    """Returns the items with tax_amount, crv_amount and tax_crv; CRV lines are folded into the item they belong to.

    Each item carries its receipt_items id, and a CRV line the id of its owner in crv_owner_id (stored on insert
    from crv_owners). Costco gives only the receipt's total tax and a Y/N flag per item, so the tax is split in
    proportion to the net amount of taxable items (taxable CRV lines count towards their item) and rounded to cents
    so the parts add up to the receipt's tax. tax_amount is None when there is nothing sensible to split the tax
    over: no taxable flags, or taxable lines that cancel out so the implied rate exceeds MAX_TAX_RATE. In that case
    tax_unsplit is True on every item of a receipt that has tax, since none of them can show a total with tax.
    """
    rows = [dict(item) for item in items]
    index_by_id = {row.get("id"): i for i, row in enumerate(rows)}
    owners = {
        i: index_by_id[row["crv_owner_id"]]
        for i, row in enumerate(rows)
        if row.get("crv_owner_id") in index_by_id
    }

    kept = [i for i in range(len(rows)) if i not in owners]
    crv = {i: 0.0 for i in kept}
    taxable_base = {i: 0.0 for i in kept}
    for index in kept:
        if str(rows[index].get("tax_flag") or "").upper() == "Y":
            taxable_base[index] += float(rows[index].get("net_amount") or 0.0)
    for crv_index, owner in owners.items():
        amount = float(rows[crv_index].get("net_amount") or 0.0)
        crv[owner] += amount
        link = _crv_link(rows[crv_index].get("raw_description"))
        if link and link[1]:
            taxable_base[owner] += amount

    tax = round(float(receipt_tax or 0.0), 2)
    base_total = sum(taxable_base.values())
    taxes: dict[int, float | None] = {i: None for i in kept}
    split = abs(base_total) > 1e-9 and 0.0 <= tax / base_total <= MAX_TAX_RATE
    if split:
        taxed = [i for i in kept if taxable_base[i] != 0.0]
        for i in taxed:
            taxes[i] = round(tax * taxable_base[i] / base_total, 2)
        # Rounding leftovers go to the line with the largest base, so the column adds up to the receipt's tax
        leftover = round(tax - sum(taxes[i] for i in taxed), 2)
        if leftover and taxed:
            biggest = max(taxed, key=lambda i: abs(taxable_base[i]))
            taxes[biggest] = round(taxes[biggest] + leftover, 2)

    result = []
    for index in kept:
        row = rows[index]
        row["crv_amount"] = round(crv[index], 2)
        row["tax_amount"] = taxes[index]
        row["tax_crv"] = round((taxes[index] or 0.0) + crv[index], 2)
        row["tax_unsplit"] = bool(tax) and not split
        result.append(row)
    return result
