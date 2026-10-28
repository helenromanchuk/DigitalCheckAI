// Run with: node --test tests/test_search_totals.js
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(`${__dirname}/../static/app.js`, "utf8");

function renderSummary(results) {
  const elements = { searchResultsBody: {}, searchSummary: {} };
  const context = vm.createContext({
    window: { addEventListener() {} },
    document: { getElementById: id => elements[id] },
    t: (key, params) => {
      if (key === "search.summary") return `${params.count} products, ${params.net}`;
      if (key === "search.crvTotal") return `CRV ${params.amount}`;
      return key;
    }
  });
  vm.runInContext(source, context);
  context.renderPurchaseSearch({ results }, "al");
  return elements.searchSummary.textContent;
}

const wine = {
  id: 1, item_number: "919140", raw_description: "MILESTONE CALIFORNIA",
  store: "costco", quantity: 1, gross_amount: 4.99, net_amount: 4.99,
  is_deposit: false, crv_amount: 0.1, crv_owner_id: null
};
const deposit = {
  id: 2, item_number: "1254", raw_description: "CA REDEMP VAL T EI/919140",
  store: "costco", quantity: 1, gross_amount: 0.1, net_amount: 0.1,
  is_deposit: true, crv_amount: 0, crv_owner_id: 1
};

for (const [name, rows, expected] of [
  ["product alone includes its deposit", [wine], "1 products, $4.99 • CRV $0.10"],
  ["deposit alone includes its amount", [deposit], "0 products, $0.00 • CRV $0.10"],
  ["product and deposit count CRV once", [deposit, wine], "1 products, $4.99 • CRV $0.10"],
  ["orphan deposits still count", [wine, { ...deposit, crv_owner_id: null }],
    "1 products, $4.99 • CRV $0.20"],
  ["same product on another receipt does not hide a deposit", [wine, { ...deposit, crv_owner_id: 3 }],
    "1 products, $4.99 • CRV $0.20"],
  ["multiple deposits folded into one owner count once", [
    { ...wine, crv_amount: 0.2 }, deposit, { ...deposit, id: 3 }
  ], "1 products, $4.99 • CRV $0.20"],
  ["refunded deposits count once", [
    { ...wine, net_amount: -4.99, crv_amount: -0.1 }, { ...deposit, net_amount: -0.1 }
  ], "1 products, -$4.99 • CRV -$0.10"]
]) {
  test(name, () => assert.equal(renderSummary(rows), expected));
}
