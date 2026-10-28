"""Playwright client that downloads Safeway in-store receipts (In-Store Purchases).

safeway.com loads receipts from its own /order-account/api/instore endpoint (same domain).
Auth is the SWY_SHOP_TOKEN token and the club card number from window.AB.userInfo, both
sent in the request body. No anti-bot headers are needed as with Costco: a plain fetch
from a signed-in user's page works.
"""
import asyncio
import json
import logging
import time
from typing import Any

from app.config import BROWSER_PROFILE_DIR, FUEL_CATEGORY
from app.errors import AppError
from app.receipt_tax import is_crv_line
from app.scraper.costco_client import _sync_lock
from app.scraper.parser import mark_crv, spread_discount

logger = logging.getLogger(__name__)

SAFEWAY_ORDERS_URL = "https://www.safeway.com/order-account/orders"
SAFEWAY_INSTORE_API_PATH = "/order-account/api/instore"
SAFEWAY_BANNER = "safeway"
REQUEST_PAUSE_SECONDS = 0.3

# Public Albertsons/Safeway image CDN keyed by item number (bpn); without a bpn there is no image (403)
SAFEWAY_IMAGE_URL = "https://images.albertsons-media.com/is/image/ABS/{bpn}?wid=300&hei=300&fmt=jpg"

ITEM_NUMBER_PREFIX = "SWY-"
BARCODE_PREFIX = "SWY-"

# Calls the receipts API from inside a safeway.com page (cookies and token come from the session).
_PAGE_INSTORE_JS = """
async ({ path, params, banner }) => {
    const info = (window.AB && window.AB.userInfo) || {};
    const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            params: { clubcard: info.clubCard, ...params },
            token: info.SWY_SHOP_TOKEN,
            banner,
        }),
    });
    return { status: res.status, text: await res.text() };
}
"""

# The user is signed in when the page has both a token and a club card number (guests have no card).
_IS_LOGGED_IN_JS = """
() => {
    const info = (window.AB && window.AB.userInfo) || {};
    return Boolean(info.SWY_SHOP_TOKEN && info.clubCard);
}
"""


class SafewayApiError(AppError):
    """Error response from the Safeway receipts API."""


def _money(val: Any) -> float:
    if val is None or val == "":
        return 0.0
    try:
        return float(str(val).replace("$", "").replace(",", "").strip())
    except ValueError:
        return 0.0


def parse_safeway_receipt(raw: dict[str, Any]) -> dict[str, Any] | None:
    """
    Converts a Safeway receipt (receipts[0] from /order-account/api/instore with the id parameter)
    to the app's common receipt format.

    Receipt-level discounts not tied to any line (the difference between the sum of the lines
    and the receipt total) are spread across lines in proportion to their amounts so that
    category spending adds up to the receipt total.
    """
    barcode = str(raw.get("barCode") or raw.get("transactionId") or raw.get("_id") or "").strip()
    if not barcode:
        return None

    tax = _money(raw.get("taxAmount"))
    total = _money(raw.get("finalTotal"))
    subtotal = round(total - tax, 2)

    items: list[dict[str, Any]] = []
    for line in raw.get("items") or []:
        sign = -1.0 if "return" in str(line.get("lineItemType") or "").lower() else 1.0
        upc = str(line.get("id") or line.get("bpn") or "").strip()
        name = str(line.get("name") or "").strip()
        department = str(line.get("department") or "").strip()
        if not name or "not found" in name.lower():
            name = f"Item name not found ({department or 'UNKNOWN'})"
        if not upc:
            upc = name.upper()[:20]

        qty = float(line.get("quantity") or 1.0) or 1.0
        gross = round(sign * _money(line.get("regularPriceTotal")), 2)
        discount = round(sign * _money(line.get("discountTotal")), 2)
        net = round(sign * _money(line.get("reducedPriceTotal")), 2)

        item: dict[str, Any] = {
            "item_number": f"{ITEM_NUMBER_PREFIX}{upc}",
            "raw_description": name,
            "quantity": round(qty, 3),
            "unit_price": round(_money(line.get("regularPrice")), 2),
            "gross_amount": gross,
            "discount_amount": discount,
            "net_amount": net,
            # The receipts API has no per-line taxable flag (only the receipt's taxAmount), so the receipt's tax
            # can't be split across its items (see app.receipt_tax)
            "tax_flag": "",
            # For sold-by-weight items, quantity is the weight in pounds
            "is_weight": bool(line.get("weightItem")) and not line.get("isFuel"),
        }
        if line.get("isFuel"):
            item["default_category"] = FUEL_CATEGORY
        elif is_crv_line(name):
            # Deposits are tagged the same way for every store: no discount share, no classifying
            mark_crv(item)
        bpn = str(line.get("bpn") or "").strip()
        if bpn:
            item["image_url"] = SAFEWAY_IMAGE_URL.format(bpn=bpn)
        items.append(item)

    # Receipt-wide discount that isn't on any line
    unallocated = round(sum(i["net_amount"] for i in items) - subtotal, 2)
    spread_discount(items, unallocated, "net_amount")

    city = str((raw.get("address") or {}).get("city") or "").strip().title()
    store_name = str(raw.get("storeName") or "Safeway").strip()

    return {
        "transaction_barcode": f"{BARCODE_PREFIX}{barcode}",
        "transaction_date": str(raw.get("posDateTime") or "")[:19] or "1970-01-01",
        "receipt_type": "safeway",
        "store": "safeway",
        "warehouse_name": f"{store_name} {city}".strip(),
        "subtotal": subtotal,
        "tax": round(tax, 2),
        "total": round(total, 2),
        "instant_savings": round(_money(raw.get("discountTotal")), 2),
        "items": items,
        "raw_json": raw,
    }


def _looks_like_safeway_receipt(entry: Any) -> bool:
    return isinstance(entry, dict) and "posDateTime" in entry and ("finalTotal" in entry or "items" in entry)


def parse_safeway_payload(payload: Any) -> list[dict[str, Any]]:
    """
    Parses a JSON file of Safeway receipts for import: an /order-account/api/instore response
    ({"receipts": [...]}), a list of receipts, or a single receipt. Returns [] if it isn't Safeway data.
    Raises ValueError for a list of Safeway receipts without line items (the purchase list page).
    """
    if isinstance(payload, dict) and isinstance(payload.get("receipts"), list):
        entries = payload["receipts"]
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = [payload]

    safeway_entries = [e for e in entries if _looks_like_safeway_receipt(e)]
    if not safeway_entries:
        return []

    with_items = [e for e in safeway_entries if isinstance(e.get("items"), list)]
    if not with_items:
        raise AppError(
            "safeway_list_only",
            "This is a Safeway purchase list without items. Receipt details are needed: "
            "use the export script from the import help.",
        )
    return [r for r in (parse_safeway_receipt(e) for e in with_items) if r]


class SafewayClient:
    """Opens safeway.com in a browser, waits for sign-in, and downloads in-store receipts."""

    def __init__(self) -> None:
        self.profile_dir = BROWSER_PROFILE_DIR
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_receipts(
        self,
        start_date: str,
        end_date: str,
        timeout_seconds: int = 240,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_receipts_sync, start_date, end_date, timeout_seconds)

    def _fetch_receipts_sync(
        self,
        start_date: str,
        end_date: str,
        timeout_seconds: int = 240,
    ) -> list[dict[str, Any]]:
        from playwright.sync_api import sync_playwright

        # The browser profile is shared with Costco, so only one sync runs at a time
        with _sync_lock, sync_playwright() as p:
            launch_args = ["--disable-blink-features=AutomationControlled", "--start-maximized"]
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=False,
                    channel="chrome",
                    args=launch_args,
                    no_viewport=True,
                )
            except Exception:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=False,
                    args=launch_args,
                    no_viewport=True,
                )

            try:
                page = context.pages[0] if context.pages else context.new_page()
                logger.info("Открываем страницу покупок Safeway...")
                page.goto(SAFEWAY_ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
                self._wait_for_login(page, timeout_seconds)

                list_receipts = self._call(page, {"client-section": "purchase"})
                logger.info("Найдено чеков Safeway: %d", len(list_receipts))

                parsed: list[dict[str, Any]] = []
                for entry in list_receipts:
                    tx_date = str(entry.get("posDateTime") or "")[:10]
                    if start_date and tx_date < start_date[:10]:
                        continue
                    if end_date and tx_date > end_date[:10]:
                        continue
                    receipt_id = entry.get("_id") or entry.get("barCode")
                    try:
                        details = self._call(page, {"id": receipt_id})
                    except SafewayApiError as exc:
                        logger.warning("Не удалось получить чек Safeway %s: %s", receipt_id, exc)
                        continue
                    if details and (receipt := parse_safeway_receipt(details[0])):
                        parsed.append(receipt)
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        return parsed

    def _wait_for_login(self, page: Any, timeout_seconds: int) -> None:
        deadline = time.time() + timeout_seconds
        reopened_orders_page = False
        while time.time() < deadline:
            if page.is_closed():
                raise AppError("browser_closed", "The browser window was closed before the sync finished.")
            try:
                logged_in = page.evaluate(_IS_LOGGED_IN_JS)
                cur_url = page.url
            except Exception:
                logged_in, cur_url = False, ""

            if logged_in:
                # After sign-in the site may redirect to the home page; call the receipts API from the purchases page
                if "/order-account/" not in cur_url and not reopened_orders_page:
                    reopened_orders_page = True
                    page.goto(SAFEWAY_ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
                    continue
                return

            page.wait_for_timeout(1500)

        raise AppError(
            "safeway_login_timeout",
            "Timed out waiting for the Safeway sign-in. Please sign in in the browser window that opened.",
        )

    def _call(self, page: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Calls the Safeway receipts API from the page and returns the receipts list."""
        resp = page.evaluate(
            _PAGE_INSTORE_JS,
            {"path": SAFEWAY_INSTORE_API_PATH, "params": params, "banner": SAFEWAY_BANNER},
        )
        status = resp.get("status")
        text = resp.get("text") or ""
        if status != 200:
            raise SafewayApiError(
                "safeway_http", f"Safeway API returned HTTP {status}: {text[:200]}", status=status, body=text[:200]
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SafewayApiError(
                "safeway_not_json", f"Safeway API returned non-JSON: {text[:200]}", body=text[:200]
            ) from exc
        time.sleep(REQUEST_PAUSE_SECONDS)
        return payload.get("receipts") or []
