"""Цена за фунт и за унцию по весу упаковки из названия товара или по весу развесного товара."""
import re
from typing import Any

OZ_PER_LB = 16.0

_NUM = r"(\d+(?:\.\d+)?)"
# "12 x 16 oz", "2 X 3LB" — упаковка из нескольких штук
_MULTI_RE = re.compile(rf"(?<![\d.]){_NUM}\s*[x×]\s*{_NUM}\s*(lbs?|pounds?|oz|ounces?)\b", re.IGNORECASE)
# "5 lb", "5LB.", "2OZ", "15.9 oz"; "fl oz" — это объём, его вырезаем заранее
_LB_RE = re.compile(rf"(?<![\d.]){_NUM}\s*(?:lbs?|pounds?)\b", re.IGNORECASE)
_OZ_RE = re.compile(rf"(?<![\d.]){_NUM}\s*(?:oz|ounces?)\b", re.IGNORECASE)
_FL_OZ_RE = re.compile(rf"{_NUM}\s*fl\.?\s*oz\b", re.IGNORECASE)


def _to_lb(value: float, unit: str) -> float:
    return value if unit.lower().startswith(("lb", "pound")) else value / OZ_PER_LB


def package_weight_lb(text: str | None) -> float | None:
    """Вес одной упаковки в фунтах по тексту названия; None, если веса в названии нет."""
    if not text:
        return None
    clean = _FL_OZ_RE.sub(" ", text)

    multi = _MULTI_RE.search(clean)
    if multi:
        count, size, unit = float(multi.group(1)), float(multi.group(2)), multi.group(3)
        weight = count * _to_lb(size, unit)
        return weight if weight > 0 else None

    lb = _LB_RE.search(clean)
    if lb:
        weight = float(lb.group(1))
        return weight if weight > 0 else None

    oz = _OZ_RE.search(clean)
    if oz:
        weight = float(oz.group(1)) / OZ_PER_LB
        return weight if weight > 0 else None
    return None


def total_weight_lb(
    clean_name: str | None,
    raw_description: str | None,
    quantity: float,
    is_weight: bool,
) -> float | None:
    """Общий вес покупки в фунтах: для развесного товара это количество, иначе вес упаковки × количество."""
    if is_weight:
        return quantity if quantity and quantity > 0 else None
    per_package = package_weight_lb(clean_name) or package_weight_lb(raw_description)
    if per_package is None or not quantity or quantity <= 0:
        return None
    return per_package * quantity


def add_unit_prices(row: dict[str, Any], amount_key: str, quantity_key: str) -> dict[str, Any]:
    """Добавляет в строку price_per_lb и price_per_oz (по сумме со скидкой) или None."""
    weight = total_weight_lb(
        row.get("clean_name"),
        row.get("raw_description"),
        float(row.get(quantity_key) or 0.0),
        bool(row.get("is_weight")),
    )
    amount = float(row.get(amount_key) or 0.0)
    if weight and amount > 0:
        row["price_per_lb"] = round(amount / weight, 4)
        row["price_per_oz"] = round(amount / (weight * OZ_PER_LB), 4)
    else:
        row["price_per_lb"] = None
        row["price_per_oz"] = None
    return row
