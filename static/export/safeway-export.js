// DigitalCheckAI: exports all Safeway receipts (in-store purchases) to safeway-receipts.json.
// Run it in the browser console (F12 → Console) on www.safeway.com after signing in.
(async () => {
  const info = (window.AB && window.AB.userInfo) || {};
  if (!info.SWY_SHOP_TOKEN || !info.clubCard) {
    console.error("DigitalCheckAI: войдите в аккаунт Safeway и запустите скрипт на странице Purchases.");
    return;
  }

  const call = async (params) => {
    const res = await fetch("/order-account/api/instore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params: { clubcard: info.clubCard, ...params }, token: info.SWY_SHOP_TOKEN, banner: "safeway" }),
    });
    if (!res.ok) throw new Error(`Safeway API вернул HTTP ${res.status}`);
    return (await res.json()).receipts || [];
  };

  const list = await call({ "client-section": "purchase" });
  const receipts = [];
  for (const [i, entry] of list.entries()) {
    console.log(`DigitalCheckAI: чек ${i + 1} из ${list.length} (${String(entry.posDateTime).slice(0, 10)})`);
    const details = await call({ id: entry._id });
    if (details[0]) receipts.push(details[0]);
  }

  const blob = new Blob([JSON.stringify({ source: "safeway", receipts }, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "safeway-receipts.json";
  link.click();
  console.log(`DigitalCheckAI: готово, сохранено чеков: ${receipts.length}`);
})();
