// DigitalCheckAI: exports all Safeway receipts (in-store purchases) to safeway-receipts.json.
// Run it in the browser console (F12 → Console) on www.safeway.com after signing in.
(async () => {
  // Message language; the dashboard sets it to its UI language when it copies the script
  const LANG = "ru";
  const MESSAGES = {
    ru: {
      signIn: "войдите в аккаунт Safeway и запустите скрипт на странице Purchases.",
      http: (status) => `Safeway API вернул HTTP ${status}`,
      receipt: (n, total, date) => `чек ${n} из ${total} (${date})`,
      done: (count) => `готово, сохранено чеков: ${count}`,
    },
    en: {
      signIn: "sign in to Safeway and run the script on the Purchases page.",
      http: (status) => `Safeway API returned HTTP ${status}`,
      receipt: (n, total, date) => `receipt ${n} of ${total} (${date})`,
      done: (count) => `done, receipts saved: ${count}`,
    },
  };
  const msg = MESSAGES[LANG] || MESSAGES.en;

  const info = (window.AB && window.AB.userInfo) || {};
  if (!info.SWY_SHOP_TOKEN || !info.clubCard) {
    console.error(`DigitalCheckAI: ${msg.signIn}`);
    return;
  }

  const call = async (params) => {
    const res = await fetch("/order-account/api/instore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params: { clubcard: info.clubCard, ...params }, token: info.SWY_SHOP_TOKEN, banner: "safeway" }),
    });
    if (!res.ok) throw new Error(msg.http(res.status));
    return (await res.json()).receipts || [];
  };

  const list = await call({ "client-section": "purchase" });
  const receipts = [];
  for (const [i, entry] of list.entries()) {
    console.log(`DigitalCheckAI: ${msg.receipt(i + 1, list.length, String(entry.posDateTime).slice(0, 10))}`);
    const details = await call({ id: entry._id });
    if (details[0]) receipts.push(details[0]);
  }

  const blob = new Blob([JSON.stringify({ source: "safeway", receipts }, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "safeway-receipts.json";
  link.click();
  console.log(`DigitalCheckAI: ${msg.done(receipts.length)}`);
})();
