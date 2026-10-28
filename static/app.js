// Client-side logic for the DigitalCheckAI dashboard
let availableCategories = [];
let donutChartInstance = null;
let monthlyChartInstance = null;
let currentStore = "costco";
// Last API responses, so a language switch can re-render without new requests
let lastConfig = null;
let lastStats = null;
let lastMembership = null;
let lastItems = null;
let lastReceipts = null;
let lastSearch = null;

const STORES = ["costco", "safeway", "all", "search"];
const STORE_LABELS = { costco: "Costco", safeway: "Safeway" };
let purchaseSearchTimer = null;
const COSTCO_RECEIPT_TYPES = ["warehouse", "gas", "online"];

function receiptTypeLabel(type) {
  return COSTCO_RECEIPT_TYPES.includes(type) ? t(`receiptType.${type}`) : STORE_LABELS[type] || type;
}

const PALETTE = [
  "#005dab", "#e31837", "#10b981", "#f59e0b", "#8b5cf6",
  "#ec4899", "#06b6d4", "#84cc16", "#f97316", "#6366f1",
  "#14b8a6", "#eab308", "#a855f7", "#ef4444", "#64748b"
];

function formatMoney(val) {
  const num = Number(val || 0);
  const sign = num < 0 ? "-" : "";
  return `${sign}$${Math.abs(num).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// Price per pound/ounce: cents per ounce only show up with three decimals
function formatUnitPrice(val) {
  if (val === null || val === undefined) return `<span class="text-slate-300">—</span>`;
  const digits = val < 1 ? 3 : 2;
  return `$${Number(val).toFixed(digits)}`;
}

// The API returns errors as {code, params, cause?, message}; show them in the UI language.
// An unknown code falls back to the API's English message
function apiErrorText(detail, fallbackKey, fallbackParams = {}) {
  if (!detail || Array.isArray(detail)) return t(fallbackKey, fallbackParams);
  if (typeof detail === "string") return detail;
  const params = { ...(detail.params || {}) };
  if (detail.cause) params.reason = apiErrorText(detail.cause, fallbackKey, fallbackParams);
  const key = `error.${detail.code}`;
  return key in I18N[currentLang] ? t(key, params) : detail.message || t(fallbackKey, fallbackParams);
}

function showBanner(message, type = "info") {
  const banner = document.getElementById("statusBanner");
  const inner = document.getElementById("statusBannerInner");
  const text = document.getElementById("statusBannerText");
  banner.classList.remove("hidden");
  text.textContent = message;

  inner.className = "rounded-xl px-4 py-3 text-sm flex items-center justify-between border ";
  if (type === "error") {
    inner.className += "bg-red-50 border-red-200 text-red-800";
  } else if (type === "success") {
    inner.className += "bg-emerald-50 border-emerald-200 text-emerald-800";
  } else {
    inner.className += "bg-blue-50 border-blue-200 text-blue-800";
  }
}

function hideBanner() {
  document.getElementById("statusBanner").classList.add("hidden");
}

function setPresetDays(days) {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - days);
  document.getElementById("startDate").value = start.toISOString().slice(0, 10);
  document.getElementById("endDate").value = end.toISOString().slice(0, 10);
  refreshAll();
}

function setPresetYear() {
  const now = new Date();
  const start = `${now.getFullYear()}-01-01`;
  document.getElementById("startDate").value = start;
  document.getElementById("endDate").value = now.toISOString().slice(0, 10);
  refreshAll();
}

function setPresetAll() {
  document.getElementById("startDate").value = "";
  document.getElementById("endDate").value = "";
  refreshAll();
}

function getFilterQueryParams() {
  const startDate = document.getElementById("startDate").value;
  const endDate = document.getElementById("endDate").value;
  const receiptType = document.getElementById("receiptTypeFilter").value;
  const params = new URLSearchParams();
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  params.set("store", currentStore);
  // Purchase types (warehouse / gas / online) exist only for Costco
  if (receiptType && currentStore === "costco") params.set("receipt_type", receiptType);
  return params;
}

function applyStoreUi() {
  for (const store of STORES) {
    const active = store === currentStore;
    document.getElementById(`storeTab-${store}`).className = active
      ? "store-tab pb-2.5 text-sm font-bold border-b-2 border-blue-600 text-blue-600"
      : "store-tab pb-2.5 text-sm font-medium border-b-2 border-transparent text-slate-500 hover:text-slate-800";
  }
  const isCostco = currentStore === "costco";
  const isSafeway = currentStore === "safeway";
  const isSearch = currentStore === "search";
  document.getElementById("dashboardView").classList.toggle("hidden", isSearch);
  document.getElementById("searchView").classList.toggle("hidden", !isSearch);
  document.getElementById("membershipSection").classList.toggle("hidden", !isCostco);
  document.getElementById("receiptTypeFilterBox").classList.toggle("hidden", !isCostco);
  document.getElementById("btnSync").classList.toggle("hidden", isSafeway);
  document.getElementById("btnSyncSafeway").classList.toggle("hidden", isCostco);
}

function setStore(store) {
  currentStore = STORES.includes(store) ? store : "costco";
  try {
    localStorage.setItem("selectedStore", currentStore);
  } catch (e) {
    // Storage is unavailable, so the tab just won't be remembered
  }
  applyStoreUi();
  refreshAll();
  if (currentStore === "search") document.getElementById("purchaseSearchInput").focus();
}

// Returns false when the config can't be loaded: without categories and their labels the dashboard can't render
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    lastConfig = await res.json();
  } catch (err) {
    console.warn("Failed to load /api/config:", err);
    showBanner(t("config.loadFailed"), "error");
    return false;
  }
  renderConfig(lastConfig);
  return true;
}

function renderConfig(data) {
  availableCategories = data.categories || [];
  categoryLabelsRu = data.category_labels_ru || {};

  const dot = document.getElementById("geminiStatusDot");
  const txt = document.getElementById("geminiStatusText");
  if (data.has_gemini_key) {
    dot.className = "w-2 h-2 rounded-full bg-emerald-500";
    txt.textContent = t("gemini.active", { model: data.gemini_model });
  } else {
    dot.className = "w-2 h-2 rounded-full bg-amber-400";
    txt.textContent = t("gemini.setKey");
  }
  populateCategoryFilterSelect();
}

function populateCategoryFilterSelect() {
  const sel = document.getElementById("itemCategoryFilter");
  const current = sel.value || "all";
  sel.innerHTML = `<option value="all">${escapeHtml(t("items.allCategories"))}</option>`;
  for (const cat of availableCategories) {
    const opt = document.createElement("option");
    opt.value = cat;
    opt.textContent = categoryLabel(cat);
    sel.appendChild(opt);
  }
  sel.value = availableCategories.includes(current) ? current : "all";
}

async function refreshAll() {
  if (!(await loadConfig())) return;
  if (currentStore === "search") {
    await runPurchaseSearch();
    return;
  }
  await Promise.all([loadStats(), loadMembershipAnalytics(), loadItemsTable(), loadReceiptsList()]);
}

// Re-renders the last loaded data without new requests (e.g. after a language switch)
function rerenderAll() {
  if (lastConfig) renderConfig(lastConfig);
  if (currentStore === "search") {
    const query = document.getElementById("purchaseSearchInput").value.trim();
    if (lastSearch && lastSearch.query === query) renderPurchaseSearch(lastSearch.data, query);
    else runPurchaseSearch();
    return;
  }
  if (lastStats) renderStats(lastStats);
  if (lastMembership && currentStore === "costco") renderMembershipAnalytics(lastMembership.data, lastMembership.ctx);
  if (lastItems) renderItemsTable(lastItems);
  if (lastReceipts) renderReceiptsList(lastReceipts);
}

function schedulePurchaseSearch() {
  clearTimeout(purchaseSearchTimer);
  purchaseSearchTimer = setTimeout(runPurchaseSearch, 300);
}

async function runPurchaseSearch() {
  const query = document.getElementById("purchaseSearchInput").value.trim();
  const tbody = document.getElementById("searchResultsBody");
  const summary = document.getElementById("searchSummary");
  if (query.length < 2) {
    summary.textContent = "";
    tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-slate-400">${escapeHtml(t("search.minChars"))}</td></tr>`;
    return;
  }

  const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
  const data = await res.json();
  // The user may have changed the search text while the request was in flight
  if (document.getElementById("purchaseSearchInput").value.trim() !== query) return;
  lastSearch = { query, data };
  renderPurchaseSearch(data, query);
}

function renderPurchaseSearch(data, query) {
  const tbody = document.getElementById("searchResultsBody");
  const summary = document.getElementById("searchSummary");
  const rows = data.results || [];

  if (!rows.length) {
    summary.textContent = "";
    tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-slate-400">${escapeHtml(t("search.notFound", { query }))}</td></tr>`;
    return;
  }

  // Purchases and their totals are products; deposits (rows of their own, or folded into a bottle's row)
  // are summed apart, as the Items tab shows them under Tax/CRV rather than Net
  const products = rows.filter(r => !r.is_deposit);
  const totalGross = products.reduce((sum, r) => sum + r.gross_amount, 0);
  const totalNet = products.reduce((sum, r) => sum + r.net_amount, 0);
  const totalCrv = rows.reduce((sum, r) => sum + (r.is_deposit ? r.net_amount : r.crv_amount || 0), 0);
  const stores = [...new Set(rows.map(r => STORE_LABELS[r.store] || r.store))].join(", ");
  summary.textContent = t("search.summary", {
    count: products.length,
    limited: rows.length >= 500 ? t("search.limited") : "",
    stores,
    gross: formatMoney(totalGross),
    net: formatMoney(totalNet)
  }) + (totalCrv ? ` • ${t("search.crvTotal", { amount: formatMoney(totalCrv) })}` : "");

  tbody.innerHTML = rows.map(r => {
    const hasDiscount = r.discount_amount > 0;
    // For sold-by-weight items the price per pound is already in the $/lb column
    const unitNet = r.quantity && r.quantity !== 1 && !r.is_weight ? `<div class="text-[10px] text-slate-400">${escapeHtml(t("search.perUnit", { amount: formatMoney(r.net_amount / r.quantity) }))}</div>` : "";
    return `
      <tr class="hover:bg-slate-50">
        <td class="px-3 py-2 whitespace-nowrap font-medium text-slate-800">${escapeHtml(r.purchase_date)}</td>
        <td class="px-3 py-2">
          <div class="font-semibold text-slate-800">${escapeHtml(STORE_LABELS[r.store] || r.store)}</div>
          <div class="text-[11px] text-slate-400">${escapeHtml(r.warehouse_name)}</div>
        </td>
        <td class="px-3 py-2">
          <div class="flex items-center gap-2">
            ${thumbHtml(r.image_file, "w-10 h-10")}
            <div>
              <div class="font-medium text-slate-900">${escapeHtml(r.clean_name)}</div>
              <div class="text-[11px] font-mono text-slate-400">${escapeHtml(r.raw_description)} • ${escapeHtml(r.item_number)}</div>
            </div>
          </div>
        </td>
        <td class="px-3 py-2 text-right text-slate-600">${r.quantity}</td>
        <td class="px-3 py-2 text-right text-slate-500">${r.is_deposit ? "—" : hasDiscount ? `<s>${formatMoney(r.gross_amount)}</s>` : formatMoney(r.gross_amount)}</td>
        <td class="px-3 py-2 text-right font-bold ${hasDiscount ? "text-emerald-700" : "text-slate-900"}">${r.is_deposit ? `—<div class="text-[10px] font-normal text-slate-400">${escapeHtml(t("receipts.itemCrv", { amount: formatMoney(r.net_amount) }))}</div>` : formatMoney(r.net_amount) + unitNet}${!r.is_deposit && r.crv_amount ? `<div class="text-[10px] font-normal text-slate-400">${escapeHtml(t("receipts.itemCrv", { amount: formatMoney(r.crv_amount) }))}</div>` : ""}</td>
        <td class="px-3 py-2 text-right text-slate-700">${formatUnitPrice(r.price_per_lb)}</td>
        <td class="px-3 py-2 text-right text-slate-700">${formatUnitPrice(r.price_per_oz)}</td>
      </tr>`;
  }).join("");
}

async function loadMembershipAnalytics() {
  // Costco membership analytics only show on the Costco tab
  if (currentStore !== "costco") return;

  const yearSelect = document.getElementById("membershipYearSelect");
  const selectedMode = yearSelect ? yearSelect.value : "auto";
  const excludeAlcohol = document.getElementById("excludeAlcoholCheckbox")?.checked ?? true;
  const goldFee = parseFloat(document.getElementById("goldFeeInput")?.value || "65");
  const execFee = parseFloat(document.getElementById("execFeeInput")?.value || "130");
  const startDate = document.getElementById("startDate").value;
  const endDate = document.getElementById("endDate").value;

  const params = new URLSearchParams();
  params.set("year", selectedMode || "auto");
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  params.set("gold_fee", isNaN(goldFee) ? "65" : String(goldFee));
  params.set("exec_fee", isNaN(execFee) ? "130" : String(execFee));
  params.set("exclude_alcohol", String(excludeAlcohol));

  const res = await fetch(`/api/membership-analytics?${params.toString()}`);
  if (!res.ok) return;
  const data = await res.json();
  lastMembership = { data, ctx: { selectedMode, startDate, endDate, excludeAlcohol } };
  renderMembershipAnalytics(data, lastMembership.ctx);
}

function renderMembershipAnalytics(data, { selectedMode, startDate, endDate, excludeAlcohol }) {
  const yearSelect = document.getElementById("membershipYearSelect");

  // Refresh the list of available years in the select
  if (yearSelect && Array.isArray(data.available_years)) {
    const prevValue = selectedMode === "auto" ? data.selected_year : selectedMode;
    yearSelect.innerHTML = "";
    for (const yr of data.available_years) {
      const opt = document.createElement("option");
      opt.value = yr;
      opt.textContent = t("membership.yearOption", { year: yr });
      yearSelect.appendChild(opt);
    }
    const filterOpt = document.createElement("option");
    filterOpt.value = "filter";
    filterOpt.textContent = t("membership.currentFilter");
    yearSelect.appendChild(filterOpt);

    if ([...yearSelect.options].some(o => o.value === prevValue)) {
      yearSelect.value = prevValue;
    }
  }

  const p = data.parameters || {};
  const sb = data.spend_breakdown || {};
  const cmp = data.comparison || {};
  const proj = data.projection || {};

  document.getElementById("membershipPeriodLabel").textContent = membershipPeriodLabel(data.selected_year, startDate, endDate);
  document.getElementById("goldFeeText").textContent = t("card.perYear", { amount: formatMoney(p.gold_fee) });
  document.getElementById("execFeeText").textContent = t("card.perYear", { amount: formatMoney(p.exec_fee) });
  document.getElementById("execRewardText").textContent = `+${formatMoney(cmp.estimated_reward)}`;
  document.getElementById("goldNetText").textContent = formatMoney(cmp.gold_net_balance);
  document.getElementById("execNetText").textContent = formatMoney(cmp.exec_net_balance);

  document.getElementById("mbTotalSpent").textContent = formatMoney(sb.total_spent_with_tax);
  document.getElementById("mbExcludedGas").textContent = `-${formatMoney(sb.excluded_gas_spent)}`;
  document.getElementById("mbExcludedTax").textContent = `-${formatMoney(sb.excluded_tax)}`;
  document.getElementById("mbExcludedAlcohol").textContent = `-${formatMoney(sb.excluded_alcohol_spent)}`;
  document.getElementById("mbAlcoholRow").className = excludeAlcohol
    ? "flex justify-between"
    : "flex justify-between opacity-40 line-through";
  document.getElementById("mbEligibleSpend").textContent = formatMoney(sb.eligible_spend);

  document.getElementById("mbProjectedSpend").textContent = formatMoney(proj.projected_annual_eligible);
  document.getElementById("mbProjectedReward").textContent = formatMoney(proj.projected_annual_reward);

  const goldBox = document.getElementById("goldCardBox");
  const execBox = document.getElementById("execCardBox");
  const goldWinnerTag = document.getElementById("goldWinnerTag");
  const execWinnerTag = document.getElementById("execWinnerTag");
  const verdictBadge = document.getElementById("cardVerdictBadge");

  const isExecBetter = cmp.recommended_card === "executive";
  if (isExecBetter) {
    execBox.className = "lg:col-span-4 rounded-xl border-2 border-emerald-500 bg-emerald-50/30 p-4 flex flex-col justify-between transition";
    goldBox.className = "lg:col-span-4 rounded-xl border border-slate-200 p-4 flex flex-col justify-between transition opacity-85";
    execWinnerTag.classList.remove("hidden");
    goldWinnerTag.classList.add("hidden");
    verdictBadge.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800";
    verdictBadge.textContent = t("membership.execBetter", { amount: formatMoney(cmp.exec_advantage_vs_gold) });
  } else {
    goldBox.className = "lg:col-span-4 rounded-xl border-2 border-amber-400 bg-amber-50/30 p-4 flex flex-col justify-between transition";
    execBox.className = "lg:col-span-4 rounded-xl border border-slate-200 p-4 flex flex-col justify-between transition";
    goldWinnerTag.classList.remove("hidden");
    execWinnerTag.classList.add("hidden");
    const diff = Math.abs(cmp.exec_advantage_vs_gold || 0);
    verdictBadge.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-800";
    verdictBadge.textContent = t("membership.goldBetter", { amount: formatMoney(diff) });
  }

  const pct = cmp.breakeven_progress_pct || 0;
  const bar = document.getElementById("breakevenProgressBar");
  bar.style.width = `${pct}%`;
  bar.className = isExecBetter
    ? "h-full bg-emerald-500 rounded-full transition-all duration-300"
    : "h-full bg-brand-blue rounded-full transition-all duration-300";

  document.getElementById("breakevenProgressLabel").textContent =
    `${pct}% (${formatMoney(sb.eligible_spend)} / ${formatMoney(p.breakeven_upgrade_spend)})`;

  const summaryText = document.getElementById("breakevenSummaryText");
  if (isExecBetter) {
    summaryText.innerHTML = t("breakeven.reached", {
      cost: formatMoney(p.upgrade_cost),
      advantage: formatMoney(cmp.exec_advantage_vs_gold)
    });
  } else {
    summaryText.innerHTML = t("breakeven.remaining", {
      cost: formatMoney(p.upgrade_cost),
      needed: formatMoney(cmp.spend_needed_for_breakeven),
      threshold: formatMoney(p.breakeven_upgrade_spend)
    });
  }
}

// The API also returns period_label, but only in Russian, so build it here in the UI language
function membershipPeriodLabel(selectedYear, startDate, endDate) {
  if (selectedYear !== "filter") return t("period.year", { year: selectedYear });
  if (startDate && endDate) return t("period.range", { start: startDate, end: endDate });
  if (startDate) return t("period.from", { start: startDate });
  if (endDate) return t("period.to", { end: endDate });
  return t("period.all");
}

async function loadStats() {
  const params = getFilterQueryParams();
  const res = await fetch(`/api/stats?${params.toString()}`);
  lastStats = await res.json();
  renderStats(lastStats);
}

function renderStats(data) {
  const s = data.summary || {};

  document.getElementById("kpiTotalSpent").textContent = formatMoney(s.total_spent);
  document.getElementById("kpiSubtotalTax").textContent =
    t("kpi.subtotalTax", { items: formatMoney(s.items_net_total), tax: formatMoney(s.total_tax) });
  document.getElementById("kpiSavings").textContent = formatMoney(s.total_savings);
  document.getElementById("kpiReceiptCount").textContent = s.receipt_count || 0;
  document.getElementById("kpiAvgReceipt").textContent = formatMoney(s.average_receipt);

  if (s.min_date && s.max_date) {
    document.getElementById("kpiDateRange").textContent = `${s.min_date} — ${s.max_date}`;
  } else {
    document.getElementById("kpiDateRange").textContent = t("kpi.noReceiptsPeriod");
  }

  const typeCounts = currentStore === "costco" ? { warehouse: 0, gas: 0, online: 0 } : {};
  for (const row of data.by_type || []) {
    typeCounts[row.receipt_type] = row.count;
  }
  document.getElementById("kpiReceiptTypes").textContent = Object.entries(typeCounts)
    .map(([type, count]) => `${receiptTypeLabel(type)}: ${count || 0}`)
    .join(" • ") || t("kpi.noReceipts");

  renderCategoryDonutAndTable(data.by_category || []);
  renderMonthlyBarChart(data.by_month || []);
}

function renderCategoryDonutAndTable(categoriesData) {
  const tbody = document.getElementById("categorySummaryTable");
  tbody.innerHTML = "";

  if (!categoriesData.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-slate-400">${escapeHtml(t("charts.noData"))}</td></tr>`;
    if (donutChartInstance) {
      donutChartInstance.destroy();
      donutChartInstance = null;
    }
    return;
  }

  const labels = [];
  const categoryKeys = [];
  const values = [];
  const colors = [];

  categoriesData.forEach((row, idx) => {
    const color = PALETTE[idx % PALETTE.length];
    labels.push(categoryLabel(row.category));
    categoryKeys.push(row.category);
    values.push(row.net_spent);
    colors.push(color);

    const tr = document.createElement("tr");
    tr.className = "hover:bg-slate-50 cursor-pointer transition";
    tr.onclick = () => filterByCategoryFromChart(row.category);
    tr.innerHTML = `
      <td class="py-2 pr-2 flex items-center gap-2 font-medium text-slate-800">
        <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background-color: ${color}"></span>
        <span class="truncate">${escapeHtml(categoryLabel(row.category))}</span>
      </td>
      <td class="py-2 text-right text-slate-500">${row.line_count}</td>
      <td class="py-2 text-right font-semibold text-slate-900">${formatMoney(row.net_spent)}</td>
      <td class="py-2 text-right text-slate-500">${row.percentage}%</td>
    `;
    tbody.appendChild(tr);
  });

  const ctx = document.getElementById("categoryDonutChart").getContext("2d");
  if (donutChartInstance) {
    donutChartInstance.destroy();
  }
  donutChartInstance = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderWidth: 2,
        borderColor: "#ffffff"
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.label}: ${formatMoney(ctx.raw)}`
          }
        }
      },
      onClick: (_, elements) => {
        if (elements.length > 0) {
          const idx = elements[0].index;
          filterByCategoryFromChart(categoryKeys[idx]);
        }
      }
    }
  });
}

function renderMonthlyBarChart(monthlyRows) {
  const ctx = document.getElementById("monthlyBarChart").getContext("2d");
  if (monthlyChartInstance) {
    monthlyChartInstance.destroy();
    monthlyChartInstance = null;
  }

  if (!monthlyRows.length) {
    return;
  }

  const months = [...new Set(monthlyRows.map(r => r.year_month))].sort();
  const categories = [...new Set(monthlyRows.map(r => r.category))];

  const datasets = categories.map((cat, idx) => {
    const color = PALETTE[idx % PALETTE.length];
    return {
      label: categoryLabel(cat),
      backgroundColor: color,
      data: months.map(m => {
        const found = monthlyRows.find(r => r.year_month === m && r.category === cat);
        return found ? found.net_spent : 0;
      })
    };
  });

  monthlyChartInstance = new Chart(ctx, {
    type: "bar",
    data: { labels: months, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { stacked: true },
        y: {
          stacked: true,
          ticks: { callback: (val) => `$${val}` }
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => `${c.dataset.label}: ${formatMoney(c.raw)}`
          }
        }
      }
    }
  });
}

function filterByCategoryFromChart(category) {
  switchTab("items");
  const sel = document.getElementById("itemCategoryFilter");
  sel.value = category;
  loadItemsTable();
}

async function loadItemsTable() {
  const params = getFilterQueryParams();
  const category = document.getElementById("itemCategoryFilter").value;
  const search = document.getElementById("itemSearchInput").value.trim();
  if (category && category !== "all") params.set("category", category);
  if (search) params.set("search", search);

  const res = await fetch(`/api/items?${params.toString()}`);
  lastItems = await res.json();
  renderItemsTable(lastItems);
}

function renderItemsTable(data) {
  availableCategories = data.categories || availableCategories;
  const items = data.items || [];

  const tbody = document.getElementById("itemsTableBody");
  tbody.innerHTML = "";

  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="14" class="py-8 text-center text-slate-400">${escapeHtml(t("items.notFound"))}</td></tr>`;
    return;
  }

  for (const item of items) {
    const tr = document.createElement("tr");
    tr.className = "hover:bg-slate-50 transition";

    let badgeHtml = `<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-100 text-slate-600">${escapeHtml(t("source.dictionary"))}</span>`;
    if (item.is_manual_override) {
      badgeHtml = `<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-purple-100 text-purple-700">${escapeHtml(t("source.manual"))}</span>`;
    } else if (item.classified_by === "gemini") {
      badgeHtml = `<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-100 text-emerald-700">Gemini AI</span>`;
    } else if (item.classified_by === "parser") {
      // The category comes from the receipt itself (fuel, coupon) and Gemini doesn't reclassify it
      badgeHtml = `<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-sky-100 text-sky-700" title="${escapeHtml(t("source.receiptTitle"))}">${escapeHtml(t("source.receipt"))}</span>`;
    }

    const optionsHtml = availableCategories.map(cat => {
      const selected = cat === item.category ? "selected" : "";
      return `<option value="${escapeHtml(cat)}" ${selected}>${escapeHtml(categoryLabel(cat))}</option>`;
    }).join("");

    tr.innerHTML = `
      <td class="px-3 py-1.5">${thumbHtml(item.image_file, "w-10 h-10")}</td>
      <td class="px-3 py-2.5 font-mono font-semibold text-slate-700">${escapeHtml(item.item_number)}</td>
      <td class="px-3 py-2.5 font-mono text-slate-500 max-w-[14rem] break-words">${escapeHtml(item.raw_description)}</td>
      <td class="px-3 py-2.5 min-w-[18rem]">
        <textarea rows="1"
          onkeydown="if (event.key === 'Enter') { event.preventDefault(); this.blur(); }"
          title="${escapeHtml(t("items.editNameTitle"))}"
          style="field-sizing: content"
          class="w-full resize-none overflow-hidden bg-transparent border border-transparent hover:border-slate-300 focus:border-blue-500 focus:bg-white rounded px-1.5 py-0.5 text-slate-900 font-medium leading-snug focus:outline-none">${escapeHtml(item.clean_name)}</textarea>
      </td>
      <td class="px-3 py-2.5 text-right text-slate-600">${item.total_quantity}</td>
      <td class="px-3 py-2.5 text-right text-slate-500">${item.is_deposit ? "—" : item.discount_saved > 0 ? `<s>${formatMoney(item.gross_spent)}</s>` : formatMoney(item.gross_spent)}</td>
      <td class="px-3 py-2.5 text-right text-emerald-600">${!item.is_deposit && item.discount_saved > 0 ? "-" + formatMoney(item.discount_saved) : "—"}</td>
      <td class="px-3 py-2.5 text-right font-bold text-slate-900">${item.is_deposit ? "—" : formatMoney(item.net_spent)}</td>
      ${taxCrvCell(item, "px-3 py-2.5")}
      <td class="px-3 py-2.5 text-right font-bold text-slate-900" title="${escapeHtml(item.tax_unknown ? t("total.taxNotSplit") : item.tax_partial ? t("total.taxPartial") : "")}">${item.tax_unknown ? "—" : formatMoney(item.net_spent + item.tax_crv) + (item.tax_partial ? "*" : "")}</td>
      <td class="px-3 py-2.5 text-right text-slate-700">${formatUnitPrice(item.price_per_lb)}</td>
      <td class="px-3 py-2.5 text-right text-slate-700">${formatUnitPrice(item.price_per_oz)}</td>
      <td class="px-3 py-2.5">
        <select
          class="border border-slate-300 rounded-lg px-2 py-1 text-xs bg-white font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500">
          ${optionsHtml}
        </select>
      </td>
      <td class="px-3 py-2.5 text-center">${badgeHtml}</td>
    `;
    // Handlers are attached here rather than inline: Safeway item numbers are built from names
    // and may contain quotes that would break an onchange="..." attribute
    const nameInput = tr.querySelector("textarea");
    const categorySelect = tr.querySelector("select");
    nameInput.addEventListener("change", () => updateItemCategory(item.item_number, categorySelect.value, nameInput.value));
    categorySelect.addEventListener("change", () => updateItemCategory(item.item_number, categorySelect.value, null));
    tbody.appendChild(tr);
  }
}

async function updateItemCategory(itemNumber, category, newCleanName) {
  const body = { category };
  if (newCleanName !== null && newCleanName !== undefined) {
    body.clean_name = newCleanName;
  }

  const res = await fetch(`/api/items/${encodeURIComponent(itemNumber)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  if (res.ok) {
    showBanner(t("banner.itemSaved", { item: itemNumber }), "success");
    await Promise.all([loadStats(), loadMembershipAnalytics(), loadItemsTable(), loadReceiptsList()]);
  } else {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail;
    const hasCode = detail && typeof detail === "object" && !Array.isArray(detail) && detail.code;
    showBanner(
      hasCode ? apiErrorText(detail, "banner.itemFailed", { item: itemNumber }) : t("banner.itemFailed", { item: itemNumber }),
      "error"
    );
  }
}

async function loadReceiptsList() {
  const params = getFilterQueryParams();
  const res = await fetch(`/api/receipts?${params.toString()}`);
  lastReceipts = await res.json();
  renderReceiptsList(lastReceipts);
}

// The Tax/CRV cell of a receipt line or an item row; padding differs between the two tables
function taxCrvCell(item, padding) {
  const value = item.tax_amount != null || item.crv_amount ? formatMoney(item.tax_crv) : "—";
  // In the Items tab, some purchases' tax couldn't be split: the value leaves it out, as Total does
  const mark = item.tax_partial && !item.tax_unknown && value !== "—" ? "*" : "";
  return `<td class="${padding} text-right text-slate-500" title="${escapeHtml(taxCrvTitle(item))}">${value}${mark}</td>`;
}

function taxCrvTitle(item) {
  const parts = [];
  if (item.tax_amount) parts.push(t("receipts.itemTax", { amount: formatMoney(item.tax_amount) }));
  if (item.crv_amount) parts.push(t("receipts.itemCrv", { amount: formatMoney(item.crv_amount) }));
  const title = parts.join(" + ");
  if (item.tax_unknown || item.tax_unsplit) return [title, t("total.taxNotSplit")].filter(Boolean).join(". ");
  if (item.tax_partial) return [title, t("total.taxPartial")].filter(Boolean).join(". ");
  return title;
}

function renderReceiptsList(data) {
  const receipts = data.receipts || [];
  const container = document.getElementById("receiptsContainer");
  container.innerHTML = "";

  if (!receipts.length) {
    container.innerHTML = `<div class="py-8 text-center text-slate-400 text-xs">${escapeHtml(t("receipts.notFound"))}</div>`;
    return;
  }

  for (const r of receipts) {
    let badge = `<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-blue-50 text-blue-700">${escapeHtml(t("badge.warehouse"))}</span>`;
    if (r.receipt_type === "gas") {
      badge = `<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-amber-50 text-amber-700">${escapeHtml(t("badge.gas"))}</span>`;
    } else if (r.receipt_type === "online") {
      badge = `<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-purple-50 text-purple-700">${escapeHtml(t("badge.online"))}</span>`;
    } else if (r.receipt_type === "safeway") {
      badge = `<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-red-50 text-red-700">🛒 Safeway</span>`;
    }

    const itemsRows = (r.items || []).map(item => `
      <tr class="border-t border-slate-100">
        <td class="py-1.5 px-2 font-mono text-slate-500">${escapeHtml(item.item_number)}</td>
        <td class="py-1.5 px-2 font-mono text-slate-600">${escapeHtml(item.raw_description)}</td>
        <td class="py-1.5 px-2 font-medium text-slate-800">
          <div class="flex items-center gap-2">${thumbHtml(item.image_file, "w-8 h-8")}<span>${escapeHtml(item.clean_name)}</span></div>
        </td>
        <td class="py-1.5 px-2"><span class="px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-[11px]">${escapeHtml(categoryLabel(item.category))}</span></td>
        <td class="py-1.5 px-2 text-right text-slate-500">${item.quantity}</td>
        <td class="py-1.5 px-2 text-right text-slate-500">${item.is_deposit ? "—" : item.discount_amount > 0 ? `<s>${formatMoney(item.gross_amount)}</s>` : formatMoney(item.gross_amount)}</td>
        <td class="py-1.5 px-2 text-right text-emerald-600">${!item.is_deposit && item.discount_amount > 0 ? "-" + formatMoney(item.discount_amount) : "—"}</td>
        <td class="py-1.5 px-2 text-right font-semibold text-slate-900">${item.is_deposit ? "—" : formatMoney(item.net_amount)}</td>
        ${taxCrvCell(item, "py-1.5 px-2")}
        <td class="py-1.5 px-2 text-right font-semibold text-slate-900" title="${item.tax_unsplit ? escapeHtml(t("total.taxNotSplit")) : ""}">${item.tax_unsplit ? "—" : formatMoney((item.is_deposit ? 0 : item.net_amount) + item.tax_crv)}</td>
        <td class="py-1.5 px-2 text-right text-slate-600">${formatUnitPrice(item.price_per_lb)}</td>
        <td class="py-1.5 px-2 text-right text-slate-600">${formatUnitPrice(item.price_per_oz)}</td>
      </tr>
    `).join("");

    const card = document.createElement("details");
    card.className = "border border-slate-200 rounded-xl bg-slate-50/50 overflow-hidden";
    card.innerHTML = `
      <summary class="cursor-pointer px-4 py-3 flex flex-wrap items-center justify-between gap-2 hover:bg-slate-100/70 transition">
        <div class="flex items-center gap-3">
          ${badge}
          <span class="font-bold text-xs text-slate-900">${escapeHtml(r.transaction_date.slice(0, 10))}</span>
          <span class="text-xs text-slate-600">${escapeHtml(r.warehouse_name)}</span>
          <span class="text-[11px] font-mono text-slate-400">#${escapeHtml(r.transaction_barcode)}</span>
        </div>
        <div class="flex items-center gap-4 text-xs">
          ${r.instant_savings > 0 ? `<span class="text-emerald-600 font-medium">${escapeHtml(t("receipts.discount", { amount: formatMoney(r.instant_savings) }))}</span>` : ""}
          <span class="text-slate-500">${escapeHtml(t("receipts.tax", { amount: formatMoney(r.tax) }))}</span>
          <span class="font-extrabold text-slate-900 text-sm">${formatMoney(r.total)}</span>
        </div>
      </summary>
      <div class="bg-white p-4 border-t border-slate-200 overflow-x-auto">
        <table class="w-full text-xs">
          <thead class="text-slate-400">
            <tr>
              <th class="text-left py-1 px-2">Item #</th>
              <th class="text-left py-1 px-2">${escapeHtml(t("col.receiptText"))}</th>
              <th class="text-left py-1 px-2">${escapeHtml(t("col.decodedShort"))}</th>
              <th class="text-left py-1 px-2">${escapeHtml(t("col.category"))}</th>
              <th class="text-right py-1 px-2">${escapeHtml(t("col.qty"))}</th>
              <th class="text-right py-1 px-2">${escapeHtml(t("col.gross"))}</th>
              <th class="text-right py-1 px-2">${escapeHtml(t("col.discount"))}</th>
              <th class="text-right py-1 px-2">${escapeHtml(t("col.netAmount"))}</th>
              <th class="text-right py-1 px-2" title="${escapeHtml(t("col.taxCrvTitle"))}">${escapeHtml(t("col.taxCrv"))}</th>
              <th class="text-right py-1 px-2" title="${escapeHtml(t("col.totalTitle"))}">${escapeHtml(t("col.total"))}</th>
              <th class="text-right py-1 px-2">$/lb</th>
              <th class="text-right py-1 px-2">$/oz</th>
            </tr>
          </thead>
          <tbody>${itemsRows}</tbody>
        </table>
      </div>
    `;
    container.appendChild(card);
  }
}

async function syncWithCostco() {
  await runStoreSync("/api/sync", "btnSync", "Costco.com");
}

async function syncWithSafeway() {
  await runStoreSync("/api/sync/safeway", "btnSyncSafeway", "Safeway.com");
}

async function runStoreSync(url, buttonId, siteName) {
  const startDate = document.getElementById("startDate").value || "2025-01-01";
  const endDate = document.getElementById("endDate").value || new Date().toISOString().slice(0, 10);
  const btn = document.getElementById(buttonId);
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = t("sync.opening", { site: siteName });
  showBanner(t("sync.browserOpened", { site: siteName }), "info");

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_date: startDate, end_date: endDate })
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(apiErrorText(data.detail, "sync.error"));
    }
    showBanner(
      t("sync.success", {
        fetched: data.fetched_receipts,
        inserted: data.inserted_receipts,
        newItems: data.new_items_count,
        provider: data.classification.provider
      }),
      "success"
    );
    await refreshAll();
  } catch (err) {
    showBanner(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

async function importJsonFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  showBanner(t("import.importing", { file: file.name }), "info");

  const res = await fetch("/api/import", { method: "POST", body: formData });
  const data = await res.json();
  event.target.value = "";

  if (res.ok) {
    setPresetAll();
    const storeName = data.store === "safeway" ? "Safeway" : "Costco";
    showBanner(
      t("import.success", {
        store: storeName,
        fetched: data.fetched_receipts,
        inserted: data.inserted_receipts,
        newItems: data.new_items_count,
        images: data.images_downloaded || 0
      }),
      "success"
    );
    await refreshAll();
  } else {
    showBanner(apiErrorText(data.detail, "import.error"), "error");
  }
}

function switchTab(tab) {
  const isItems = tab === "items";
  document.getElementById("tabContentItems").classList.toggle("hidden", !isItems);
  document.getElementById("tabContentReceipts").classList.toggle("hidden", isItems);
  document.getElementById("itemsToolbar").classList.toggle("hidden", !isItems);

  document.getElementById("tabBtnItems").className = isItems
    ? "pb-3 text-sm font-bold border-b-2 border-blue-600 text-blue-600"
    : "pb-3 text-sm font-medium border-b-2 border-transparent text-slate-500 hover:text-slate-800";
  document.getElementById("tabBtnReceipts").className = !isItems
    ? "pb-3 text-sm font-bold border-b-2 border-blue-600 text-blue-600"
    : "pb-3 text-sm font-medium border-b-2 border-transparent text-slate-500 hover:text-slate-800";
}

function openGeminiModal() {
  document.getElementById("geminiModal").classList.remove("hidden");
}

function closeGeminiModal() {
  document.getElementById("geminiModal").classList.add("hidden");
}

async function saveGeminiKey() {
  const key = document.getElementById("geminiKeyInput").value.trim();
  if (!key) return;
  showBanner(t("gemini.saving"), "info");
  const res = await fetch("/api/config/gemini-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: key })
  });
  if (res.ok) {
    closeGeminiModal();
    document.getElementById("geminiKeyInput").value = "";
    showBanner(t("gemini.saved"), "success");
    await refreshAll();
  }
}

async function reclassifyPendingWithGemini() {
  closeGeminiModal();
  showBanner(t("gemini.classifying"), "info");
  const res = await fetch("/api/reclassify-pending", { method: "POST" });
  const data = await res.json();
  if (res.ok) {
    showBanner(
      t("gemini.processed", { count: data.classification.classified_count, provider: data.classification.provider }),
      "success"
    );
    await refreshAll();
  }
}

function openImportHelp() {
  document.getElementById("importHelpStatus").textContent = "";
  document.getElementById("importHelpModal").classList.remove("hidden");
}

function closeImportHelp() {
  document.getElementById("importHelpModal").classList.add("hidden");
}

async function copyExportScript(store) {
  const status = document.getElementById("importHelpStatus");
  const name = store === "safeway" ? "Safeway" : "Costco";
  try {
    const res = await fetch(`/static/export/${store}-export.js`);
    // The script prints its console messages in the dashboard's language
    const script = (await res.text()).replace('const LANG = "ru";', `const LANG = "${currentLang}";`);
    await navigator.clipboard.writeText(script);
    status.className = "text-xs font-medium text-emerald-700";
    status.textContent = t("help.copied", { store: name });
  } catch (err) {
    status.className = "text-xs font-medium text-red-700";
    status.textContent = t("help.copyFailed");
  }
}

function openNewCategoryModal() {
  document.getElementById("newCategoryModal").classList.remove("hidden");
}

function closeNewCategoryModal() {
  document.getElementById("newCategoryModal").classList.add("hidden");
}

async function createNewCategory() {
  const name = document.getElementById("newCategoryInput").value.trim();
  const nameRu = document.getElementById("newCategoryRuInput").value.trim();
  if (!name) return;
  const res = await fetch("/api/categories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, name_ru: nameRu })
  });
  if (res.ok) {
    document.getElementById("newCategoryInput").value = "";
    document.getElementById("newCategoryRuInput").value = "";
    closeNewCategoryModal();
    showBanner(t("newCat.added", { name }), "success");
    await refreshAll();
  } else {
    const body = await res.json().catch(() => ({}));
    showBanner(apiErrorText(body.detail, "newCat.failed"), "error");
  }
}

function thumbHtml(imageFile, sizeClass) {
  if (!imageFile) {
    return `<div class="${sizeClass} shrink-0 rounded bg-slate-100"></div>`;
  }
  return `<img src="/images/${encodeURIComponent(imageFile)}" alt="" loading="lazy"
    class="${sizeClass} shrink-0 rounded object-contain bg-white border border-slate-100" />`;
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

window.addEventListener("DOMContentLoaded", () => {
  try {
    const saved = localStorage.getItem("selectedStore");
    if (STORES.includes(saved)) currentStore = saved;
  } catch (e) {
    // Storage is unavailable, so open the Costco tab
  }
  applyStaticTranslations();
  applyStoreUi();
  setPresetDays(90);
});
