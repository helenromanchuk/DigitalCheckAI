"""Per-line tax and CRV for a receipt: the receipt's tax spread over taxable lines, plus the bottle deposit lines.

The split depends only on the receipt, so it is worked out once when the receipt is saved (split_receipt) and
stored with its lines; the views read the stored values.
"""
import re
from bisect import bisect_left
from typing import Any

# A California bottle deposit (CRV) line: "CA REDEMP VAL T EI/919140" is the deposit for item 919140; "T" means
# the deposit itself is taxed, "N" that it isn't. Truncated wordings ("CA REDEMP VA"), a marker glued to the
# code ("TEI/") and text after the item number are allowed; the marker and the item number may be missing
_CRV_RE = re.compile(
    r"^CA\s+REDEMP(?:TION)?\s+VA(?:L(?:UE)?)?\b"
    r"(?:\s*([TN])(?:\s*E[A-Z])?(?=[\s/]|$))?"
    r"[^/]*(?:/\s*(\d+)\b)?",
    re.IGNORECASE,
)

# California sales tax tops out around 11%; a higher implied rate means the taxable lines nearly cancel out
# (a purchase and a return on one receipt), and splitting the tax over them would give absurd shares
MAX_TAX_RATE = 0.15


def _crv_info(raw_description: str | None) -> tuple[str | None, bool | None] | None:
    """For a CRV line: the item number it is for and whether it is taxed (None when not stated); else None."""
    match = _CRV_RE.match((raw_description or "").strip())
    if not match:
        return None
    marker = match.group(1)
    return match.group(2), (marker.upper() == "T") if marker else None


def is_crv_line(raw_description: str | None) -> bool:
    """Whether a receipt line is a California bottle deposit (CRV)."""
    return _crv_info(raw_description) is not None


def _taxable(row: dict[str, Any], crv: tuple[str | None, bool | None] | None) -> bool | None:
    """Whether a line is taxed: a deposit by its T/N marker, other lines by their Y/N flag; None when unknown."""
    if crv is not None:
        return crv[1]
    flag = str(row.get("tax_flag") or "").upper()
    return True if flag == "Y" else False if flag == "N" else None


def crv_owners(items: list[dict[str, Any]]) -> dict[int, int]:
    """Maps the index of each CRV line to the index of the receipt line it belongs to.

    The owner is the nearest earlier line with the linked item number that has no CRV yet ("wine, wine, CRV, CRV"
    gives each bottle one deposit), else the first later one without a CRV, else the nearest line of the item.
    Lines whose amount has the CRV's sign come first, so a refunded deposit goes to the returned bottle. A CRV line
    without an item number, or whose item isn't on the receipt, has no owner.
    """
    links = {i: info[0] for i, row in enumerate(items) if (info := _crv_info(row.get("raw_description")))}
    lines_by_item: dict[str, list[int]] = {}
    for i, row in enumerate(items):
        if i not in links:
            lines_by_item.setdefault(str(row.get("item_number")), []).append(i)

    owners: dict[int, int] = {}
    claimed: set[int] = set()
    for index, item_number in links.items():
        candidates = lines_by_item.get(item_number) if item_number else None
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
            # More deposits than bottles: stack the extra one on the nearest bottle, on either side
            owner = min(candidates, key=lambda i: abs(i - index))
        owners[index] = owner
        claimed.add(owner)
    return owners


def split_receipt(lines: list[dict[str, Any]], receipt_tax: float) -> list[dict[str, Any]]:
    """Works out each line's CRV owner, tax share and deposit, in the order of the receipt's lines.

    Each result has crv_owner (index of the line a CRV line is folded into, else None), tax_amount, crv_amount
    (deposits folded into this line) and tax_unsplit. Costco gives only the receipt's total tax and a Y/N flag
    per item, so the tax is split in proportion to the net amount of taxable lines (a taxable deposit counts
    towards its item) and rounded to cents so the parts add up to the receipt's tax.

    The tax is split only when the taxability of every line is known (no line without a Y/N flag or T/N marker)
    and the implied rate is plausible (at most MAX_TAX_RATE). Otherwise tax_amount is None and tax_unsplit is True
    on the lines that may carry some of the tax: a total with tax can't be shown for them. A tax-free receipt has
    nothing to split, so its lines get None too, not a computed $0.00. A folded CRV line gets None, 0 and False.
    """
    infos = [_crv_info(line.get("raw_description")) for line in lines]
    owners = crv_owners(lines)
    kept = [i for i in range(len(lines)) if i not in owners]

    crv = {i: 0.0 for i in kept}
    base = {i: 0.0 for i in kept}
    may_be_taxed = {i: False for i in kept}
    unknown = False
    for i in range(len(lines)):
        target = owners.get(i, i)
        taxable = _taxable(lines[i], infos[i])
        amount = float(lines[i].get("net_amount") or 0.0)
        if i in owners:
            crv[target] += amount
        if taxable is None:
            unknown = True
        if taxable is not False:
            may_be_taxed[target] = True
        if taxable:
            base[target] += amount

    tax = round(float(receipt_tax or 0.0), 2)
    base_total = sum(base.values())
    split = (
        bool(tax) and not unknown and abs(base_total) > 1e-9 and 0.0 < tax / base_total <= MAX_TAX_RATE
    )
    taxes: dict[int, float | None] = {i: None for i in kept}
    if split:
        taxed = [i for i in kept if base[i] != 0.0]
        for i in taxed:
            taxes[i] = round(tax * base[i] / base_total, 2)
        # Rounding leftovers go to the line with the largest base, so the column adds up to the receipt's tax
        leftover = round(tax - sum(taxes[i] for i in taxed), 2)
        if leftover and taxed:
            biggest = max(taxed, key=lambda i: abs(base[i]))
            taxes[biggest] = round(taxes[biggest] + leftover, 2)

    results = []
    for i in range(len(lines)):
        if i in owners:
            results.append({"crv_owner": owners[i], "tax_amount": None, "crv_amount": 0.0, "tax_unsplit": False})
        else:
            results.append(
                {
                    "crv_owner": None,
                    "tax_amount": taxes[i],
                    "crv_amount": round(crv[i], 2),
                    "tax_unsplit": bool(tax) and not split and may_be_taxed[i],
                }
            )
    return results
