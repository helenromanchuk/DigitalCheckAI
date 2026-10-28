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

    The owner is the nearest earlier line with the linked item number that has no CRV yet ("wine, wine, CRV, CRV"
    gives each bottle one deposit), else the first later one without a CRV, else the nearest line of the item;
    a CRV line whose item isn't on the receipt has no owner. Lines whose amount has the CRV's sign come first, so a refunded deposit
    goes to the returned bottle and not to the purchase.
    """
    links = {i: link for i, row in enumerate(items) if (link := _crv_link(row.get("raw_description")))}
    lines_by_item: dict[str, list[int]] = {}
    for i, row in enumerate(items):
        if i not in links:
            lines_by_item.setdefault(str(row.get("item_number")), []).append(i)

    owners: dict[int, int] = {}
    claimed: set[int] = set()
    for index, (item_number, _) in links.items():
        candidates = lines_by_item.get(item_number)
        if not candidates:
            continue
        refund = float(items[index].get("net_amount") or 0.0) < 0
        same_sign = [i for i in candidates if (float(items[i].get("net_amount") or 0.0) < 0) == refund]
        candidates = same_sign or candidates
        split_at = bisect_left(candidates, index)
        free_earlier = [i for i in candidates[:split_at] if i not in claimed]
        free_later = [i for i in candidates[split_at:] if i not in claimed]
        if free_earlier:
            owner = free_earlier[-1]
        elif free_later:
            owner = free_later[0]
        else:
            # More deposits than bottles: stack the extra one on the nearest bottle
            owner = candidates[split_at - 1] if split_at else candidates[0]
        owners[index] = owner
        claimed.add(owner)
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
    tax_unsplit is True on the items of a receipt with tax that may carry some of it (all but those flagged "N",
    whose total is exact), since they can't show a total with tax.
    """
    rows = [dict(item) for item in items]
    index_by_id = {row["id"]: i for i, row in enumerate(rows) if row.get("id") is not None}
    owners = {
        i: index_by_id[row["crv_owner_id"]]
        for i, row in enumerate(rows)
        if row.get("crv_owner_id") is not None and row["crv_owner_id"] in index_by_id
    }

    kept = [i for i in range(len(rows)) if i not in owners]
    crv = {i: 0.0 for i in kept}
    taxable_base = {i: 0.0 for i in kept}
    for index in kept:
        # A deposit whose item isn't on the receipt stays a line of its own, taxed by the T/N in its description
        link = _crv_link(rows[index].get("raw_description"))
        taxable = link[1] if link else str(rows[index].get("tax_flag") or "").upper() == "Y"
        if taxable:
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
        # A line flagged "N" carries no tax unless a taxable deposit was folded into it
        may_be_taxed = str(row.get("tax_flag") or "").upper() != "N" or taxable_base[index] != 0.0
        row["tax_unsplit"] = bool(tax) and not split and may_be_taxed
        result.append(row)
    return result
