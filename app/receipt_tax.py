"""Per-line tax and CRV for a receipt: the receipt's tax spread over taxable lines, plus the bottle deposit lines.

The split depends only on the receipt, so it is worked out once when the receipt is saved (split_receipt) and
stored with its lines; the views read the stored values.
"""
import re
from bisect import bisect_left
from typing import Any

from app.item_number import core_item_number
from app.money import allocate_cents

# Version of the rules below. The split is stored with each receipt (receipts.tax_split_version), and receipts
# split by other rules are split again on startup, so bump this whenever a change here alters any result
SPLIT_RULES_VERSION = 6

# A California bottle deposit (CRV) line: "CA REDEMP VAL T EI/919140" is the deposit for item 919140; "T" means
# the deposit itself is taxed, "N" that it isn't. Truncated wordings ("CA REDEMP VA"), a marker glued to the
# word "VAL" or to the code ("VALT", "TEI/") is allowed; the marker may be missing. A letter glued to "VA"
# ("VAN") is part of a word, not a marker. The item number is read from the rest (see _crv_item_number)
_CRV_RE = re.compile(
    r"^CA\s+REDEMP(?:TION)?\s+VA(?:LUE|L)?"
    r"(?:(?:\s+([TN])|(?<=L)([TN]))(?:\s*E[A-Z])?(?=[\s/]|$)|\b)",
    re.IGNORECASE,
)
_LEADING_DIGITS_RE = re.compile(r"\s*(\d+)")


def _crv_item_number(rest: str) -> str | None:
    """The item number a deposit is for, from the text after its marker.

    It follows the last "/" ("EI/919140", "24PK/919140 CRV"), or, without a "/", it is the last token made of
    digits only ("EI 919140", "12PK 919140"); pack sizes like "24PK" aren't item numbers.
    """
    if "/" in rest:
        match = _LEADING_DIGITS_RE.match(rest.rsplit("/", 1)[1])
        return match.group(1) if match else None
    numbers = [token for token in rest.split() if token.isdigit()]
    return numbers[-1] if numbers else None

# California sales tax tops out around 11%; a higher implied rate means the taxable lines nearly cancel out
# (a purchase and a return on one receipt), and splitting the tax over them would give absurd shares
MAX_TAX_RATE = 0.15


def _crv_info(raw_description: str | None) -> tuple[str | None, bool | None] | None:
    """For a CRV line: the item number it is for and whether it is taxed (None when not stated); else None."""
    description = (raw_description or "").strip()
    match = _CRV_RE.match(description)
    if not match:
        return None
    marker = match.group(1) or match.group(2)
    return _crv_item_number(description[match.end():]), (marker.upper() == "T") if marker else None


def is_crv_line(raw_description: str | None) -> bool:
    """Whether a receipt line is a California bottle deposit (CRV)."""
    return _crv_info(raw_description) is not None


def _taxable(row: dict[str, Any], crv: tuple[str | None, bool | None] | None) -> bool | None:
    """Whether a line is taxed: a deposit by its T/N marker, else by its Y/N flag; None when neither says."""
    if crv is not None and crv[1] is not None:
        return crv[1]
    flag = str(row.get("tax_flag") or "").upper()
    return True if flag == "Y" else False if flag == "N" else None


def crv_owners(
    items: list[dict[str, Any]], infos: list[tuple[str | None, bool | None] | None] | None = None
) -> dict[int, int]:
    """Maps the index of each CRV line to the index of the receipt line it belongs to.

    The owner is the nearest earlier line with the linked item number that has no CRV yet ("wine, wine, CRV, CRV"
    gives each bottle one deposit), else the first later one without a CRV, else the nearest line of the item.
    Lines whose amount has the CRV's sign come first, so a refunded deposit goes to the returned bottle. A CRV line
    without an item number, or whose item isn't on the receipt, has no owner. infos are the lines' parsed CRV
    descriptions when the caller already has them.
    """
    if infos is None:
        infos = [_crv_info(row.get("raw_description")) for row in items]
    links = {i: info[0] for i, info in enumerate(infos) if info is not None}
    lines_by_item: dict[str, list[int]] = {}
    for i, row in enumerate(items):
        if i not in links:
            lines_by_item.setdefault(core_item_number(row.get("item_number")), []).append(i)

    owners: dict[int, int] = {}
    claimed: set[int] = set()
    for index, item_number in links.items():
        candidates = lines_by_item.get(core_item_number(item_number)) if item_number else None
        if not candidates:
            continue
        refund = float(items[index].get("net_amount") or 0.0) < 0
        # A refunded deposit belongs to a returned bottle and a charged one to a sold bottle; with no bottle of
        # that kind on the receipt the deposit stays a line of its own
        candidates = [i for i in candidates if (float(items[i].get("net_amount") or 0.0) < 0) == refund]
        if not candidates:
            continue
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
    (deposits folded into this line, or a deposit's own amount when its item isn't on the receipt), is_deposit
    and tax_unsplit. Costco gives only the receipt's total tax and a Y/N flag
    per item, so the tax is split in proportion to the net amount of taxable lines (a taxable deposit counts
    towards its item) and rounded to cents so the parts add up to the receipt's tax.

    The tax is split only when the taxability of every line is known (a deposit's T/N marker, else a Y/N flag,
    else, for a folded deposit, its bottle's)
    and the implied rate is plausible (at most MAX_TAX_RATE). Otherwise tax_amount is None and tax_unsplit is True
    on the lines that may carry some of the tax: a total with tax can't be shown for them. A tax-free receipt has
    nothing to split, so its lines get None too, not a computed $0.00. A folded CRV line gets None, 0 and False.
    """
    infos = [_crv_info(line.get("raw_description")) for line in lines]
    owners = crv_owners(lines, infos)
    kept = [i for i in range(len(lines)) if i not in owners]

    crv = {i: 0.0 for i in kept}
    base = {i: 0.0 for i in kept}
    may_be_taxed = {i: False for i in kept}
    has_taxable = {i: False for i in kept}
    unknown = False
    for i in range(len(lines)):
        target = owners.get(i, i)
        taxable = _taxable(lines[i], infos[i])
        if taxable is None and i in owners:
            # A deposit that states nothing is taxed like the bottle it is charged for
            taxable = _taxable(lines[target], infos[target])
        amount = float(lines[i].get("net_amount") or 0.0)
        if infos[i] is not None:
            # A deposit is shown under Tax/CRV: on its bottle, or on its own line when the bottle isn't here
            crv[target] += amount
        if taxable is None:
            unknown = True
        if taxable is not False:
            may_be_taxed[target] = True
        if taxable:
            base[target] = round(base[target] + amount, 2)
            has_taxable[target] = True

    # The tax is split by the amount after coupons: on the stored Costco receipts the receipt's tax over the
    # taxable amount after coupons is a steady 10.0% or 9.4% (two tax areas), and over the amount before them
    # it scatters between 6.9% and 9.5%, so instant savings lower the taxed amount
    tax = round(float(receipt_tax or 0.0), 2)
    base_total = round(sum(base.values()), 2)
    split = (
        bool(tax) and not unknown and base_total != 0.0 and 0.0 < tax / base_total <= MAX_TAX_RATE
    )
    taxes: dict[int, float | None] = {i: None for i in kept}
    if split:
        # In whole cents that add up to the receipt's tax (bases are rounded to cents, so a cancelled-out base is
        # exactly zero)
        taxed = [i for i in kept if base[i] != 0.0]
        taxes.update(zip(taxed, allocate_cents(tax, [base[i] for i in taxed]), strict=True))
        # A taxable line whose sale and return cancel out is taxed, at $0.00; only untaxed lines get None
        taxes.update({i: 0.0 for i in kept if has_taxable[i] and base[i] == 0.0})
    elif tax and not any(may_be_taxed.values()):
        # The receipt has tax but no line that may carry it (all flagged untaxed, or a taxed charge that
        # isn't a line): no line's total can include it, so all of them are marked
        may_be_taxed = {i: True for i in kept}

    results = []
    for i in range(len(lines)):
        if i in owners:
            results.append(
                {"crv_owner": owners[i], "tax_amount": None, "crv_amount": 0.0, "is_deposit": True, "tax_unsplit": False}
            )
        else:
            results.append(
                {
                    "crv_owner": None,
                    "tax_amount": taxes[i],
                    "crv_amount": round(crv[i], 2),
                    "is_deposit": infos[i] is not None,
                    "tax_unsplit": bool(tax) and not split and may_be_taxed[i],
                }
            )
    return results
