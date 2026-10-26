// DigitalCheckAI: exports Costco receipts (warehouse, gas, online orders) to costco-receipts.json.
// Run it in the browser console (F12 → Console) on www.costco.com after signing in,
// on the Account → Orders & Purchases page. Change YEARS_BACK to adjust how far back it goes.
(async () => {
  const YEARS_BACK = 3;
  const URL_GQL = "https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql";
  const token = localStorage.getItem("idToken");
  const clientId = localStorage.getItem("clientID") || "4900eb1f-0c10-4bd9-99c3-c59e6c1ecebf";
  if (!token) {
    console.error("DigitalCheckAI: войдите в аккаунт Costco и откройте Orders & Purchases.");
    return;
  }

  // Requests go through XMLHttpRequest so the page's Kasada protection adds its own headers to them
  const gql = (query, variables) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", URL_GQL);
    const headers = {
      "Content-Type": "application/json-patch+json",
      "costco.service": "restOrders",
      "costco.env": "ecom",
      "costco-x-authorization": "Bearer " + token,
      "costco-x-wcs-clientId": clientId,
      "client-identifier": "481b1aec-aa3b-454b-b81b-48187e28f205",
    };
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.onload = () => {
      if (xhr.status !== 200) return reject(new Error(`Costco API вернул HTTP ${xhr.status}`));
      const json = JSON.parse(xhr.responseText);
      if (json.errors) return reject(new Error(json.errors.map(e => e.message).join("; ")));
      resolve(json.data || {});
    };
    xhr.onerror = () => reject(new Error("Сетевая ошибка"));
    xhr.send(JSON.stringify({ query, variables }));
  });

  const RECEIPTS_LIST = `query r($startDate:String!,$endDate:String!,$documentType:String!,$documentSubType:String!){
    receiptsWithCounts(startDate:$startDate,endDate:$endDate,documentType:$documentType,documentSubType:$documentSubType){
      receipts{ transactionBarcode documentType receiptType transactionDateTime } } }`;
  const RECEIPT_DETAIL = `query d($barcode:String!,$documentType:String!){
    receiptsWithCounts(barcode:$barcode,documentType:$documentType){ receipts{
      warehouseName receiptType documentType transactionDateTime transactionDate warehouseNumber warehouseShortName
      transactionType transactionBarcode total totalItemCount subTotal taxes
      itemArray{ itemNumber itemDescription01 itemDescription02 itemDepartmentNumber unit amount taxFlag
        fuelUnitQuantity itemUnitPriceAmount fuelGradeDescription }
      tenderArray{ tenderTypeCode tenderDescription amountTender } } } }`;
  const ONLINE_LIST = `query o($startDate:String!,$endDate:String!,$pageNumber:Int,$pageSize:Int,$warehouseNumber:String!){
    getOnlineOrders(startDate:$startDate,endDate:$endDate,pageNumber:$pageNumber,pageSize:$pageSize,warehouseNumber:$warehouseNumber){
      totalNumberOfRecords bcOrders{ orderNumber: sourceOrderNumber } } }`;
  const ONLINE_DETAIL = `query g($orderNumbers:[String]){ getOrderDetails(orderNumbers:$orderNumbers){
    warehouseNumber orderNumber: sourceOrderNumber orderPlacedDate: orderedDate status merchandiseTotal
    shippingAndHandling orderTax: uSTaxTotal1 discountAmount orderTotal
    orderShipTos{ orderLineItems{ itemNumber itemDescription: sourceItemDescription unitPrice
      quantity: orderedTotalQuantity merchandiseTotal: merchandiseTotalAmount lineItemId isFeeItem } } } }`;

  const pad = n => String(n).padStart(2, "0");
  const fmtReceipt = d => `${d.getMonth() + 1}/${pad(d.getDate())}/${d.getFullYear()}`;
  const fmtOnline = d => `${d.getFullYear()}-${d.getMonth() + 1}-${pad(d.getDate())}`;

  // The period is split into 180-day chunks
  const chunks = [];
  const end = new Date();
  let cur = new Date();
  cur.setFullYear(cur.getFullYear() - YEARS_BACK);
  while (cur <= end) {
    const next = new Date(Math.min(cur.getTime() + 180 * 864e5, end.getTime()));
    chunks.push([new Date(cur), next]);
    cur = new Date(next.getTime() + 864e5);
  }

  const receipts = [];
  const seenBarcodes = new Set();
  for (const [from, to] of chunks) {
    const data = await gql(RECEIPTS_LIST, { startDate: fmtReceipt(from), endDate: fmtReceipt(to), documentType: "all", documentSubType: "all" });
    for (const r of (data.receiptsWithCounts || {}).receipts || []) {
      if (seenBarcodes.has(r.transactionBarcode)) continue;
      seenBarcodes.add(r.transactionBarcode);
      const docType = /fuel/i.test(r.documentType) || /gas/i.test(r.receiptType) ? "fuel" : "warehouse";
      console.log(`DigitalCheckAI: чек ${String(r.transactionDateTime).slice(0, 10)} (${r.receiptType})`);
      const detail = await gql(RECEIPT_DETAIL, { barcode: r.transactionBarcode, documentType: docType });
      const full = ((detail.receiptsWithCounts || {}).receipts || [])[0];
      if (full) receipts.push(full);
    }
  }

  const orders = [];
  const seenOrders = new Set();
  for (const [from, to] of chunks) {
    for (let page = 1; ; page++) {
      const data = await gql(ONLINE_LIST, { startDate: fmtOnline(from), endDate: fmtOnline(to), pageNumber: page, pageSize: 10, warehouseNumber: "847" });
      const groups = [].concat(data.getOnlineOrders || []);
      const batch = groups.flatMap(g => g.bcOrders || []);
      for (const o of batch) {
        if (seenOrders.has(o.orderNumber)) continue;
        seenOrders.add(o.orderNumber);
        console.log(`DigitalCheckAI: онлайн-заказ ${o.orderNumber}`);
        const detail = await gql(ONLINE_DETAIL, { orderNumbers: [o.orderNumber] });
        const full = [].concat(detail.getOrderDetails || [])[0];
        if (full) orders.push(full);
      }
      const total = Math.max(0, ...groups.map(g => g.totalNumberOfRecords || 0));
      if (!batch.length || page * 10 >= total) break;
    }
  }

  const blob = new Blob([JSON.stringify({ source: "costco", receipts, bcOrders: orders }, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "costco-receipts.json";
  link.click();
  console.log(`DigitalCheckAI: готово, чеков: ${receipts.length}, онлайн-заказов: ${orders.length}`);
})();
