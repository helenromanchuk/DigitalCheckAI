"""Comparing item numbers that differ only in formatting."""


def core_item_number(item_number: object) -> str:
    """Item number without surrounding spaces and leading zeros: "0919140" and "919140" are the same item."""
    return str(item_number or "").strip().lstrip("0") or "0"
