"""Клиент Playwright для выгрузки чеков из магазинов Safeway (In-Store Purchases).

Сайт safeway.com берёт чеки из собственного эндпоинта /order-account/api/instore (тот же домен).
Авторизация — токен SWY_SHOP_TOKEN и номер клубной карты из window.AB.userInfo, которые
передаются в теле запроса. Антибот-заголовков, как у Costco, не требуется: обычный fetch
со страницы залогиненного пользователя работает.
"""
import asyncio
import json
import logging
import time
from typing import Any

from app.config import BROWSER_PROFILE_DIR
from app.scraper.costco_client import _sync_lock

logger = logging.getLogger(__name__)

SAFEWAY_ORDERS_URL = "https://www.safeway.com/order-account/orders"
SAFEWAY_INSTORE_API_PATH = "/order-account/api/instore"
SAFEWAY_BANNER = "safeway"
REQUEST_PAUSE_SECONDS = 0.3

# Публичный CDN картинок Albertsons/Safeway по номеру товара (bpn); без bpn картинки нет (403)
SAFEWAY_IMAGE_URL = "https://images.albertsons-media.com/is/image/ABS/{bpn}?wid=300&hei=300&fmt=jpg"

ITEM_NUMBER_PREFIX = "SWY-"
BARCODE_PREFIX = "SWY-"

# Запрос к API чеков изнутри страницы safeway.com (cookies и токен берутся из сессии).
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

# Пользователь вошёл, когда на странице есть и токен, и номер клубной карты (у гостя карты нет).
_IS_LOGGED_IN_JS = """
() => {
    const info = (window.AB && window.AB.userInfo) || {};
    return Boolean(info.SWY_SHOP_TOKEN && info.clubCard);
}
"""


class SafewayApiError(RuntimeError):
    """Ошибка ответа API чеков Safeway."""


def _money(val: Any) -> float:
    if val is None or val == "":
        return 0.0
    try:
        return float(str(val).replace("$", "").replace(",", "").strip())
    except ValueError:
        return 0.0


def parse_safeway_receipt(raw: dict[str, Any]) -> dict[str, Any] | None:
    """
    Приводит чек Safeway (receipts[0] из /order-account/api/instore с параметром id)
    к единому формату чеков приложения.

    Скидки уровня всего чека, не привязанные к позициям (разница между суммой позиций
    и итогом чека), распределяются по позициям пропорционально их сумме, чтобы
    расходы по категориям сходились с итогом чека.
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
            "tax_flag": "",
            # У развесного товара quantity — вес в фунтах
            "is_weight": bool(line.get("weightItem")) and not line.get("isFuel"),
        }
        if line.get("isFuel"):
            item["default_category"] = "Авто и заправка (Gas Station)"
        bpn = str(line.get("bpn") or "").strip()
        if bpn:
            item["image_url"] = SAFEWAY_IMAGE_URL.format(bpn=bpn)
        items.append(item)

    # Скидка на весь чек, которой нет в позициях
    unallocated = round(sum(i["net_amount"] for i in items) - subtotal, 2)
    positive = [i for i in items if i["net_amount"] > 0]
    base = sum(i["net_amount"] for i in positive)
    if unallocated > 0 and base > 0:
        remaining = unallocated
        for idx, item in enumerate(positive):
            share = remaining if idx == len(positive) - 1 else round(unallocated * item["net_amount"] / base, 2)
            item["discount_amount"] = round(item["discount_amount"] + share, 2)
            item["net_amount"] = round(item["net_amount"] - share, 2)
            remaining = round(remaining - share, 2)

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
    Разбирает JSON-файл с чеками Safeway для импорта: ответ /order-account/api/instore
    ({"receipts": [...]}), список чеков или один чек. Возвращает [], если это не формат Safeway.
    Бросает ValueError, если это список чеков Safeway без позиций (страница списка покупок).
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
        raise ValueError(
            "Это список покупок Safeway без товаров. Нужны детали чеков — "
            "воспользуйтесь скриптом выгрузки из справки к импорту."
        )
    return [r for r in (parse_safeway_receipt(e) for e in with_items) if r]


class SafewayClient:
    """Открывает safeway.com в браузере, ждёт входа и выгружает чеки из магазинов."""

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

        # Общий профиль браузера с Costco, поэтому одновременно работает только одна синхронизация
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
                raise RuntimeError("Окно браузера закрыто до завершения синхронизации.")
            try:
                logged_in = page.evaluate(_IS_LOGGED_IN_JS)
                cur_url = page.url
            except Exception:
                logged_in, cur_url = False, ""

            if logged_in:
                # После входа сайт может увести на главную; API чеков вызываем со страницы покупок
                if "/order-account/" not in cur_url and not reopened_orders_page:
                    reopened_orders_page = True
                    page.goto(SAFEWAY_ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
                    continue
                return

            page.wait_for_timeout(1500)

        raise TimeoutError(
            "Не удалось дождаться входа в аккаунт Safeway. "
            "Пожалуйста, войдите в аккаунт в открывшемся окне браузера."
        )

    def _call(self, page: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Вызывает API чеков Safeway со страницы и возвращает список receipts."""
        resp = page.evaluate(
            _PAGE_INSTORE_JS,
            {"path": SAFEWAY_INSTORE_API_PATH, "params": params, "banner": SAFEWAY_BANNER},
        )
        status = resp.get("status")
        text = resp.get("text") or ""
        if status != 200:
            raise SafewayApiError(f"Safeway API вернул HTTP {status}: {text[:200]}")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SafewayApiError(f"Safeway API вернул не JSON: {text[:200]}") from exc
        time.sleep(REQUEST_PAUSE_SECONDS)
        return payload.get("receipts") or []
