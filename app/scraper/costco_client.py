"""Клиент Playwright для авторизации на costco.com и выгрузки всех типов чеков через GraphQL API.

Эндпоинт заказов защищён антибот-системой Kasada: каждый запрос должен нести одноразовые
заголовки x-kpsdk-*, которые генерирует её скрипт на странице. Поэтому запросы отправляются
через XMLHttpRequest изнутри открытой страницы costco.com — Kasada перехватывает XHR и
подставляет заголовки сам. Прямые запросы (httpx, fetch) получают 404.
"""
import asyncio
from datetime import datetime, timedelta
import json
import logging
import threading
import time
from typing import Any

from app.config import BROWSER_PROFILE_DIR
from app.scraper.parser import parse_online_order, parse_warehouse_or_gas_receipt

logger = logging.getLogger(__name__)

COSTCO_MYACCOUNT_ORDERS_URL = (
    "https://www.costco.com/myaccount/#/app/4900eb1f-0c10-4bd9-99c3-c59e6c1ecebf/ordersandpurchases"
)
COSTCO_GRAPHQL_URL = "https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql"
DEFAULT_WCS_CLIENT_ID = "4900eb1f-0c10-4bd9-99c3-c59e6c1ecebf"
DEFAULT_CLIENT_IDENTIFIER = "481b1aec-aa3b-454b-b81b-48187e28f205"
ONLINE_WAREHOUSE_NUMBER = "847"
ONLINE_PAGE_SIZE = 10
REQUEST_PAUSE_SECONDS = 0.3

_sync_lock = threading.Lock()

# Список чеков за период: только заголовки, товары приходят без описаний и цен.
RECEIPTS_LIST_QUERY = """
query receiptsWithCounts($startDate: String!, $endDate: String!, $documentType: String!, $documentSubType: String!) {
  receiptsWithCounts(startDate: $startDate, endDate: $endDate, documentType: $documentType, documentSubType: $documentSubType) {
    inWarehouse
    gasStation
    carWash
    receipts {
      warehouseName
      receiptType
      documentType
      transactionDateTime
      transactionBarcode
      transactionType
      total
      totalItemCount
    }
  }
}
"""

# Полный чек по штрихкоду. documentType: "warehouse" для склада, "fuel" для заправки.
RECEIPT_DETAIL_QUERY = """
query receiptsWithCounts($barcode: String!, $documentType: String!) {
  receiptsWithCounts(barcode: $barcode, documentType: $documentType) {
    receipts {
      warehouseName
      receiptType
      documentType
      transactionDateTime
      transactionDate
      companyNumber
      warehouseNumber
      operatorNumber
      warehouseShortName
      registerNumber
      transactionNumber
      transactionType
      transactionBarcode
      total
      warehouseAddress1
      warehouseAddress2
      warehouseCity
      warehouseState
      warehouseCountry
      warehousePostalCode
      totalItemCount
      subTotal
      taxes
      invoiceNumber
      sequenceNumber
      itemArray {
        itemNumber
        itemDescription01
        frenchItemDescription1
        itemDescription02
        frenchItemDescription2
        itemIdentifier
        itemDepartmentNumber
        unit
        amount
        taxFlag
        merchantID
        entryMethod
        transDepartmentNumber
        fuelUnitQuantity
        fuelGradeCode
        itemUnitPriceAmount
        fuelUomCode
        fuelUomDescription
        fuelUomDescriptionFr
        fuelGradeDescription
        fuelGradeDescriptionFr
      }
      tenderArray {
        tenderTypeCode
        tenderDescription
        amountTender
      }
    }
  }
}
"""

ONLINE_ORDERS_LIST_QUERY = """
query getOnlineOrders($startDate: String!, $endDate: String!, $pageNumber: Int, $pageSize: Int, $warehouseNumber: String!) {
  getOnlineOrders(startDate: $startDate, endDate: $endDate, pageNumber: $pageNumber, pageSize: $pageSize, warehouseNumber: $warehouseNumber) {
    pageNumber
    pageSize
    totalNumberOfRecords
    bcOrders {
      orderHeaderId
      orderPlacedDate: orderedDate
      orderNumber: sourceOrderNumber
      orderTotal
      status
    }
  }
}
"""

# Алиасы подогнаны под имена полей, которые ожидает parse_online_order.
ONLINE_ORDER_DETAIL_QUERY = """
query getOrderDetails($orderNumbers: [String]) {
  getOrderDetails(orderNumbers: $orderNumbers) {
    warehouseNumber
    orderNumber: sourceOrderNumber
    orderPlacedDate: orderedDate
    status
    merchandiseTotal
    shippingAndHandling
    orderTax: uSTaxTotal1
    discountAmount
    orderTotal
    orderShipTos {
      orderLineItems {
        itemNumber
        itemDescription: sourceItemDescription
        unitPrice
        quantity: orderedTotalQuantity
        merchandiseTotal: merchandiseTotalAmount
        lineItemId
        isFeeItem
      }
    }
  }
}
"""

# Отправка GraphQL через XHR внутри страницы, чтобы Kasada добавил свои заголовки.
_PAGE_GRAPHQL_JS = """
async ({ url, query, variables, defaultClientId, clientIdentifier }) => {
    const token = localStorage.getItem('idToken');
    const clientId = localStorage.getItem('clientID') || defaultClientId;
    return await new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url);
        const headers = {
            'Content-Type': 'application/json-patch+json',
            'costco.service': 'restOrders',
            'costco.env': 'ecom',
            'costco-x-authorization': 'Bearer ' + token,
            'costco-x-wcs-clientId': clientId,
            'client-identifier': clientIdentifier,
        };
        for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
        xhr.onload = () => resolve({ status: xhr.status, text: xhr.responseText });
        xhr.onerror = () => resolve({ status: 0, text: 'network error' });
        xhr.send(JSON.stringify({ query, variables }));
    });
}
"""

# Секунд до истечения idToken (null, если токена нет или он не разбирается).
_TOKEN_TTL_JS = """
() => {
    const token = localStorage.getItem('idToken');
    if (!token) return null;
    try {
        const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        return Math.floor(JSON.parse(atob(b64)).exp - Date.now() / 1000);
    } catch (e) {
        return null;
    }
}
"""


class CostcoApiError(RuntimeError):
    """Ошибка ответа GraphQL API Costco."""


def _format_receipts_date(iso_date: str) -> str:
    """Формат дат для receiptsWithCounts, как его шлёт сайт: M/DD/YYYY."""
    dt = datetime.strptime(iso_date[:10], "%Y-%m-%d")
    return f"{dt.month}/{dt.day:02d}/{dt.year}"


def _format_online_date(iso_date: str) -> str:
    """Формат дат для getOnlineOrders, как его шлёт сайт: YYYY-M-DD."""
    dt = datetime.strptime(iso_date[:10], "%Y-%m-%d")
    return f"{dt.year}-{dt.month}-{dt.day:02d}"


def _split_date_chunks(start_iso: str, end_iso: str, max_days: int = 180) -> list[tuple[str, str]]:
    """Делит большой период на интервалы по max_days дней для стабильной работы GraphQL Costco."""
    start_dt = datetime.strptime(start_iso[:10], "%Y-%m-%d")
    end_dt = datetime.strptime(end_iso[:10], "%Y-%m-%d")
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    chunks: list[tuple[str, str]] = []
    cur = start_dt
    while cur <= end_dt:
        nxt = min(cur + timedelta(days=max_days), end_dt)
        chunks.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt + timedelta(days=1)
    return chunks


def _detail_document_type(list_receipt: dict[str, Any]) -> str:
    doc_type = str(list_receipt.get("documentType") or "").lower()
    receipt_type = str(list_receipt.get("receiptType") or "").lower()
    if "fuel" in doc_type or "gas" in receipt_type:
        return "fuel"
    return "warehouse"


def _flatten_order_details(details: dict[str, Any]) -> dict[str, Any]:
    """Собирает строки заказа из всех адресов доставки в плоский orderLineItems."""
    order = {k: v for k, v in details.items() if k != "orderShipTos"}
    order["orderLineItems"] = [
        line
        for ship_to in details.get("orderShipTos") or []
        for line in ship_to.get("orderLineItems") or []
    ]
    return order


class CostcoClient:
    """Управляет браузерной сессией Costco.com и выгружает чеки через GraphQL."""

    def __init__(self) -> None:
        self.profile_dir = BROWSER_PROFILE_DIR
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_receipts(
        self,
        start_date: str,
        end_date: str,
        timeout_seconds: int = 240,
    ) -> list[dict[str, Any]]:
        """
        Запускает синхронный Playwright в отдельном потоке (чтобы избежать ошибки
        WindowsSelectorEventLoopPolicy NotImplementedError при работе внутри uvicorn --reload).
        """
        return await asyncio.to_thread(
            self._fetch_receipts_sync,
            start_date,
            end_date,
            timeout_seconds,
        )

    def _fetch_receipts_sync(
        self,
        start_date: str,
        end_date: str,
        timeout_seconds: int = 240,
    ) -> list[dict[str, Any]]:
        """
        Открывает окно браузера Chrome/Chromium (Playwright), ждёт авторизации пользователя на costco.com
        и выгружает чеки (In-Warehouse, Gas Station и Online Orders) за заданный период.
        """
        from playwright.sync_api import sync_playwright

        with _sync_lock, sync_playwright() as p:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ]
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
                logger.info("Открываем страницу заказов Costco.com...")
                page.goto(COSTCO_MYACCOUNT_ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
                self._wait_for_login(page, timeout_seconds)

                parsed: list[dict[str, Any]] = []
                parsed.extend(self._fetch_warehouse_receipts(page, start_date, end_date))
                parsed.extend(self._fetch_online_orders(page, start_date, end_date))
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        by_barcode: dict[str, dict[str, Any]] = {}
        for receipt in parsed:
            tx_date = receipt["transaction_date"][:10]
            if start_date and tx_date < start_date[:10]:
                continue
            if end_date and tx_date > end_date[:10]:
                continue
            by_barcode[receipt["transaction_barcode"]] = receipt

        return list(by_barcode.values())

    def _wait_for_login(self, page: Any, timeout_seconds: int) -> None:
        """Ждёт, пока пользователь войдёт и на странице появится действующий idToken."""
        deadline = time.time() + timeout_seconds
        reopened_orders_page = False
        while time.time() < deadline:
            if page.is_closed():
                raise RuntimeError("Окно браузера закрыто до завершения синхронизации.")
            try:
                ttl = page.evaluate(_TOKEN_TTL_JS)
                cur_url = page.url
            except Exception:
                # Страница в процессе навигации (например, редирект после входа)
                ttl, cur_url = None, ""

            if ttl is not None and ttl > 60:
                # После входа сайт может увести на главную; скрипт Kasada и свежий токен
                # гарантированно есть на странице заказов.
                if "ordersandpurchases" not in cur_url.lower() and not reopened_orders_page:
                    reopened_orders_page = True
                    page.goto(COSTCO_MYACCOUNT_ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
                    continue
                page.wait_for_load_state("networkidle", timeout=30000)
                return

            page.wait_for_timeout(1500)

        raise TimeoutError(
            "Не удалось получить токен авторизации Costco.com. "
            "Пожалуйста, войдите в аккаунт в открывшемся окне браузера."
        )

    def _graphql(self, page: Any, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Выполняет GraphQL-запрос через XHR на странице и возвращает поле data."""
        resp = page.evaluate(
            _PAGE_GRAPHQL_JS,
            {
                "url": COSTCO_GRAPHQL_URL,
                "query": query,
                "variables": variables,
                "defaultClientId": DEFAULT_WCS_CLIENT_ID,
                "clientIdentifier": DEFAULT_CLIENT_IDENTIFIER,
            },
        )
        status = resp.get("status")
        text = resp.get("text") or ""
        if status != 200:
            hint = " (вероятно, запрос отклонила защита Kasada)" if status in (403, 404, 429) else ""
            raise CostcoApiError(f"Costco GraphQL вернул HTTP {status}{hint}: {text[:200]}")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CostcoApiError(f"Costco GraphQL вернул не JSON: {text[:200]}") from exc
        if payload.get("errors"):
            messages = "; ".join(str(e.get("message")) for e in payload["errors"])
            raise CostcoApiError(f"Ошибка Costco GraphQL: {messages}")
        time.sleep(REQUEST_PAUSE_SECONDS)
        return payload.get("data") or {}

    def _fetch_warehouse_receipts(self, page: Any, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Чеки со складов и заправок: список за период, затем полный чек по каждому штрихкоду."""
        list_receipts: list[dict[str, Any]] = []
        for chunk_start, chunk_end in _split_date_chunks(start_date, end_date):
            data = self._graphql(
                page,
                RECEIPTS_LIST_QUERY,
                {
                    "startDate": _format_receipts_date(chunk_start),
                    "endDate": _format_receipts_date(chunk_end),
                    "documentType": "all",
                    "documentSubType": "all",
                },
            )
            list_receipts.extend((data.get("receiptsWithCounts") or {}).get("receipts") or [])
        logger.info("Найдено чеков склада/заправки: %d", len(list_receipts))

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for list_receipt in list_receipts:
            barcode = str(list_receipt.get("transactionBarcode") or "")
            if not barcode or barcode in seen:
                continue
            seen.add(barcode)

            raw = list_receipt
            try:
                data = self._graphql(
                    page,
                    RECEIPT_DETAIL_QUERY,
                    {"barcode": barcode, "documentType": _detail_document_type(list_receipt)},
                )
                details = (data.get("receiptsWithCounts") or {}).get("receipts") or []
                if details:
                    raw = details[0]
                else:
                    logger.warning("Пустой ответ по чеку %s, используем данные из списка", barcode)
            except CostcoApiError as exc:
                logger.warning("Не удалось получить чек %s: %s", barcode, exc)

            parsed = parse_warehouse_or_gas_receipt(raw)
            if parsed:
                results.append(parsed)
        return results

    def _fetch_online_orders(self, page: Any, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Онлайн-заказы: постраничный список за период, затем детали каждого заказа."""
        orders: list[dict[str, Any]] = []
        for chunk_start, chunk_end in _split_date_chunks(start_date, end_date):
            page_number = 1
            while True:
                data = self._graphql(
                    page,
                    ONLINE_ORDERS_LIST_QUERY,
                    {
                        "startDate": _format_online_date(chunk_start),
                        "endDate": _format_online_date(chunk_end),
                        "pageNumber": page_number,
                        "pageSize": ONLINE_PAGE_SIZE,
                        "warehouseNumber": ONLINE_WAREHOUSE_NUMBER,
                    },
                )
                groups = data.get("getOnlineOrders") or []
                if isinstance(groups, dict):
                    groups = [groups]
                batch = [o for g in groups for o in (g.get("bcOrders") or [])]
                orders.extend(batch)
                total = max((int(g.get("totalNumberOfRecords") or 0) for g in groups), default=0)
                if not batch or page_number * ONLINE_PAGE_SIZE >= total:
                    break
                page_number += 1
        logger.info("Найдено онлайн-заказов: %d", len(orders))

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for order in orders:
            order_number = str(order.get("orderNumber") or "")
            if not order_number or order_number in seen:
                continue
            seen.add(order_number)

            raw = order
            try:
                data = self._graphql(page, ONLINE_ORDER_DETAIL_QUERY, {"orderNumbers": [order_number]})
                details = data.get("getOrderDetails")
                if isinstance(details, list):
                    details = details[0] if details else None
                if details:
                    raw = _flatten_order_details(details)
            except CostcoApiError as exc:
                logger.warning("Не удалось получить заказ %s: %s", order_number, exc)

            parsed = parse_online_order(raw)
            if parsed:
                results.append(parsed)
        return results
