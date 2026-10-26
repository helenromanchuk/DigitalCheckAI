// Клиентская логика дашборда DigitalCheckAI
let availableCategories = [];
let donutChartInstance = null;
let monthlyChartInstance = null;
let currentStore = "costco";

const STORES = ["costco", "safeway", "all", "search"];
const STORE_LABELS = { costco: "Costco", safeway: "Safeway" };
let purchaseSearchTimer = null;
const RECEIPT_TYPE_LABELS = {
  warehouse: "Склад",
  gas: "Заправка",
  online: "Онлайн",
  safeway: "Safeway"
};

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

// Цена за фунт/унцию: копейки за унцию видны только с тремя знаками
function formatUnitPrice(val) {
  if (val === null || val === undefined) return `<span class="text-slate-300">—</span>`;
  const digits = val < 1 ? 3 : 2;
  return `$${Number(val).toFixed(digits)}`;
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
  // Типы покупок (склад / заправка / онлайн) есть только у Costco
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
    // Хранилище недоступно — вкладка просто не запомнится
  }
  applyStoreUi();
  refreshAll();
  if (currentStore === "search") document.getElementById("purchaseSearchInput").focus();
}

async function loadConfig() {
  const res = await fetch("/api/config");
  const data = await res.json();
  availableCategories = data.categories || [];

  const dot = document.getElementById("geminiStatusDot");
  const txt = document.getElementById("geminiStatusText");
  if (data.has_gemini_key) {
    dot.className = "w-2 h-2 rounded-full bg-emerald-500";
    txt.textContent = `Gemini AI активен (${data.gemini_model})`;
  } else {
    dot.className = "w-2 h-2 rounded-full bg-amber-400";
    txt.textContent = "Указать ключ Gemini API";
  }
  populateCategoryFilterSelect();
}

function populateCategoryFilterSelect() {
  const sel = document.getElementById("itemCategoryFilter");
  const current = sel.value || "all";
  sel.innerHTML = `<option value="all">Все категории</option>`;
  for (const cat of availableCategories) {
    const opt = document.createElement("option");
    opt.value = cat;
    opt.textContent = cat;
    sel.appendChild(opt);
  }
  sel.value = availableCategories.includes(current) ? current : "all";
}

async function refreshAll() {
  await loadConfig();
  if (currentStore === "search") {
    await runPurchaseSearch();
    return;
  }
  await Promise.all([loadStats(), loadMembershipAnalytics(), loadItemsTable(), loadReceiptsList()]);
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
    tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-slate-400">Введите хотя бы 2 символа.</td></tr>`;
    return;
  }

  const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
  const data = await res.json();
  // Пока шёл запрос, пользователь мог изменить строку поиска
  if (document.getElementById("purchaseSearchInput").value.trim() !== query) return;
  const rows = data.results || [];

  if (!rows.length) {
    summary.textContent = "";
    tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-slate-400">Ничего не найдено по запросу «${escapeHtml(query)}».</td></tr>`;
    return;
  }

  const totalGross = rows.reduce((sum, r) => sum + r.gross_amount, 0);
  const totalNet = rows.reduce((sum, r) => sum + r.net_amount, 0);
  const stores = [...new Set(rows.map(r => STORE_LABELS[r.store] || r.store))].join(", ");
  summary.textContent =
    `Найдено покупок: ${rows.length}${rows.length >= 500 ? " (показаны первые 500)" : ""} • ` +
    `Магазины: ${stores} • До скидки: ${formatMoney(totalGross)} • Со скидкой: ${formatMoney(totalNet)}`;

  tbody.innerHTML = rows.map(r => {
    const hasDiscount = r.discount_amount > 0;
    // У развесного товара цена за фунт уже есть в колонке $/lb
    const unitNet = r.quantity && r.quantity !== 1 && !r.is_weight ?`<div class="text-[10px] text-slate-400">${formatMoney(r.net_amount / r.quantity)} за ед.</div>` : "";
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
        <td class="px-3 py-2 text-right text-slate-500">${hasDiscount ? `<s>${formatMoney(r.gross_amount)}</s>` : formatMoney(r.gross_amount)}</td>
        <td class="px-3 py-2 text-right font-bold ${hasDiscount ? "text-emerald-700" : "text-slate-900"}">${formatMoney(r.net_amount)}${unitNet}</td>
        <td class="px-3 py-2 text-right text-slate-700">${formatUnitPrice(r.price_per_lb)}</td>
        <td class="px-3 py-2 text-right text-slate-700">${formatUnitPrice(r.price_per_oz)}</td>
      </tr>`;
  }).join("");
}

async function loadMembershipAnalytics() {
  // Аналитика карт Costco показывается только на вкладке Costco
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

  // Обновляем список доступных лет в селекте
  if (yearSelect && Array.isArray(data.available_years)) {
    const prevValue = selectedMode === "auto" ? data.selected_year : selectedMode;
    yearSelect.innerHTML = "";
    for (const yr of data.available_years) {
      const opt = document.createElement("option");
      opt.value = yr;
      opt.textContent = `${yr} год`;
      yearSelect.appendChild(opt);
    }
    const filterOpt = document.createElement("option");
    filterOpt.value = "filter";
    filterOpt.textContent = "Текущий фильтр дат";
    yearSelect.appendChild(filterOpt);

    if ([...yearSelect.options].some(o => o.value === prevValue)) {
      yearSelect.value = prevValue;
    }
  }

  const p = data.parameters || {};
  const sb = data.spend_breakdown || {};
  const cmp = data.comparison || {};
  const proj = data.projection || {};

  document.getElementById("membershipPeriodLabel").textContent = data.period_label || "";
  document.getElementById("goldFeeText").textContent = `${formatMoney(p.gold_fee)} / год`;
  document.getElementById("execFeeText").textContent = `${formatMoney(p.exec_fee)} / год`;
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
    verdictBadge.textContent = `Выгоднее Executive (+${formatMoney(cmp.exec_advantage_vs_gold)} чистой выгоды)`;
  } else {
    goldBox.className = "lg:col-span-4 rounded-xl border-2 border-amber-400 bg-amber-50/30 p-4 flex flex-col justify-between transition";
    execBox.className = "lg:col-span-4 rounded-xl border border-slate-200 p-4 flex flex-col justify-between transition";
    goldWinnerTag.classList.remove("hidden");
    execWinnerTag.classList.add("hidden");
    const diff = Math.abs(cmp.exec_advantage_vs_gold || 0);
    verdictBadge.className = "px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-800";
    verdictBadge.textContent = `Выгоднее Gold Star (экономия ${formatMoney(diff)} на взносе)`;
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
    summaryText.innerHTML =
      `Апгрейд до <b>Executive (+${formatMoney(p.upgrade_cost)})</b> полностью окупился! Чистая выгода над Gold Star: <b class="text-emerald-700">+${formatMoney(cmp.exec_advantage_vs_gold)}</b>`;
  } else {
    summaryText.innerHTML =
      `До окупаемости <b>Executive (+${formatMoney(p.upgrade_cost)})</b> осталось потратить ещё <b>${formatMoney(cmp.spend_needed_for_breakeven)}</b> (порог: ${formatMoney(p.breakeven_upgrade_spend)} / год)`;
  }
}

async function loadStats() {
  const params = getFilterQueryParams();
  const res = await fetch(`/api/stats?${params.toString()}`);
  const data = await res.json();
  const s = data.summary || {};

  document.getElementById("kpiTotalSpent").textContent = formatMoney(s.total_spent);
  document.getElementById("kpiSubtotalTax").textContent =
    `Товары: ${formatMoney(s.items_net_total)} • Налог: ${formatMoney(s.total_tax)}`;
  document.getElementById("kpiSavings").textContent = formatMoney(s.total_savings);
  document.getElementById("kpiReceiptCount").textContent = s.receipt_count || 0;
  document.getElementById("kpiAvgReceipt").textContent = formatMoney(s.average_receipt);

  if (s.min_date && s.max_date) {
    document.getElementById("kpiDateRange").textContent = `${s.min_date} — ${s.max_date}`;
  } else {
    document.getElementById("kpiDateRange").textContent = "Нет чеков за выбранный период";
  }

  const typeCounts = currentStore === "costco" ? { warehouse: 0, gas: 0, online: 0 } : {};
  for (const t of data.by_type || []) {
    typeCounts[t.receipt_type] = t.count;
  }
  document.getElementById("kpiReceiptTypes").textContent = Object.entries(typeCounts)
    .map(([type, count]) => `${RECEIPT_TYPE_LABELS[type] || type}: ${count || 0}`)
    .join(" • ") || "Нет чеков";

  renderCategoryDonutAndTable(data.by_category || []);
  renderMonthlyBarChart(data.by_month || []);
}

function renderCategoryDonutAndTable(categoriesData) {
  const tbody = document.getElementById("categorySummaryTable");
  tbody.innerHTML = "";

  if (!categoriesData.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-slate-400">Нет данных за выбранный период. Нажмите «Скачать» в правом верхнем углу или «Импорт JSON».</td></tr>`;
    if (donutChartInstance) {
      donutChartInstance.destroy();
      donutChartInstance = null;
    }
    return;
  }

  const labels = [];
  const values = [];
  const colors = [];

  categoriesData.forEach((row, idx) => {
    const color = PALETTE[idx % PALETTE.length];
    labels.push(row.category);
    values.push(row.net_spent);
    colors.push(color);

    const tr = document.createElement("tr");
    tr.className = "hover:bg-slate-50 cursor-pointer transition";
    tr.onclick = () => filterByCategoryFromChart(row.category);
    tr.innerHTML = `
      <td class="py-2 pr-2 flex items-center gap-2 font-medium text-slate-800">
        <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background-color: ${color}"></span>
        <span class="truncate">${escapeHtml(row.category)}</span>
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
          filterByCategoryFromChart(labels[idx]);
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
      label: cat,
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
  const data = await res.json();
  availableCategories = data.categories || availableCategories;
  const items = data.items || [];

  const tbody = document.getElementById("itemsTableBody");
  tbody.innerHTML = "";

  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="12" class="py-8 text-center text-slate-400">Товары не найдены для заданных фильтров.</td></tr>`;
    return;
  }

  for (const item of items) {
    const tr = document.createElement("tr");
    tr.className = "hover:bg-slate-50 transition";

    let badgeHtml = `<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-100 text-slate-600">Словарь</span>`;
    if (item.is_manual_override) {
      badgeHtml = `<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-purple-100 text-purple-700">Вручную</span>`;
    } else if (item.classified_by === "gemini") {
      badgeHtml = `<span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-100 text-emerald-700">Gemini AI</span>`;
    }

    const optionsHtml = availableCategories.map(cat => {
      const selected = cat === item.category ? "selected" : "";
      return `<option value="${escapeHtml(cat)}" ${selected}>${escapeHtml(cat)}</option>`;
    }).join("");

    tr.innerHTML = `
      <td class="px-3 py-1.5">${thumbHtml(item.image_file, "w-10 h-10")}</td>
      <td class="px-3 py-2.5 font-mono font-semibold text-slate-700">${escapeHtml(item.item_number)}</td>
      <td class="px-3 py-2.5 font-mono text-slate-500 max-w-[14rem] break-words">${escapeHtml(item.raw_description)}</td>
      <td class="px-3 py-2.5 min-w-[18rem]">
        <textarea rows="1"
          onchange="updateItemCategory('${escapeJs(item.item_number)}', null, this.value)"
          onkeydown="if (event.key === 'Enter') { event.preventDefault(); this.blur(); }"
          title="Нажмите, чтобы отредактировать расшифрованное название"
          style="field-sizing: content"
          class="w-full resize-none overflow-hidden bg-transparent border border-transparent hover:border-slate-300 focus:border-blue-500 focus:bg-white rounded px-1.5 py-0.5 text-slate-900 font-medium leading-snug focus:outline-none">${escapeHtml(item.clean_name)}</textarea>
      </td>
      <td class="px-3 py-2.5 text-right text-slate-600">${item.total_quantity}</td>
      <td class="px-3 py-2.5 text-right text-slate-500">${item.discount_saved > 0 ? `<s>${formatMoney(item.gross_spent)}</s>` : formatMoney(item.gross_spent)}</td>
      <td class="px-3 py-2.5 text-right text-emerald-600">${item.discount_saved > 0 ? "-" + formatMoney(item.discount_saved) : "—"}</td>
      <td class="px-3 py-2.5 text-right font-bold text-slate-900">${formatMoney(item.net_spent)}</td>
      <td class="px-3 py-2.5 text-right text-slate-700">${formatUnitPrice(item.price_per_lb)}</td>
      <td class="px-3 py-2.5 text-right text-slate-700">${formatUnitPrice(item.price_per_oz)}</td>
      <td class="px-3 py-2.5">
        <select onchange="updateItemCategory('${escapeJs(item.item_number)}', this.value, null)"
          class="border border-slate-300 rounded-lg px-2 py-1 text-xs bg-white font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500">
          ${optionsHtml}
        </select>
      </td>
      <td class="px-3 py-2.5 text-center">${badgeHtml}</td>
    `;
    tbody.appendChild(tr);
  }
}

async function updateItemCategory(itemNumber, newCategory, newCleanName) {
  let categoryToUse = newCategory;
  if (!categoryToUse) {
    // Если изменилось только название, берём текущую категорию из DOM или запрашиваем
    const rowSelect = document.querySelector(`select[onchange*="'${itemNumber}'"]`);
    categoryToUse = rowSelect ? rowSelect.value : "Прочее";
  }

  const body = { category: categoryToUse };
  if (newCleanName !== null && newCleanName !== undefined) {
    body.clean_name = newCleanName;
  }

  const res = await fetch(`/api/items/${encodeURIComponent(itemNumber)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });

  if (res.ok) {
    showBanner(`Категория для Item #${itemNumber} сохранена в локальной базе и пересчитана во всех чеках.`, "success");
    await Promise.all([loadStats(), loadMembershipAnalytics(), loadItemsTable(), loadReceiptsList()]);
  } else {
    showBanner(`Не удалось обновить Item #${itemNumber}`, "error");
  }
}

async function loadReceiptsList() {
  const params = getFilterQueryParams();
  const res = await fetch(`/api/receipts?${params.toString()}`);
  const data = await res.json();
  const receipts = data.receipts || [];
  const container = document.getElementById("receiptsContainer");
  container.innerHTML = "";

  if (!receipts.length) {
    container.innerHTML = `<div class="py-8 text-center text-slate-400 text-xs">Чеки за выбранный период не найдены.</div>`;
    return;
  }

  for (const r of receipts) {
    let badge = `<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-blue-50 text-blue-700">🏪 Склад</span>`;
    if (r.receipt_type === "gas") {
      badge = `<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-amber-50 text-amber-700">⛽ Заправка</span>`;
    } else if (r.receipt_type === "online") {
      badge = `<span class="px-2 py-0.5 rounded text-[11px] font-semibold bg-purple-50 text-purple-700">📦 Онлайн</span>`;
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
        <td class="py-1.5 px-2"><span class="px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-[11px]">${escapeHtml(item.category)}</span></td>
        <td class="py-1.5 px-2 text-right text-slate-500">${item.quantity}</td>
        <td class="py-1.5 px-2 text-right text-slate-500">${item.discount_amount > 0 ? `<s>${formatMoney(item.gross_amount)}</s>` : formatMoney(item.gross_amount)}</td>
        <td class="py-1.5 px-2 text-right text-emerald-600">${item.discount_amount > 0 ? "-" + formatMoney(item.discount_amount) : "—"}</td>
        <td class="py-1.5 px-2 text-right font-semibold text-slate-900">${formatMoney(item.net_amount)}</td>
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
          ${r.instant_savings > 0 ? `<span class="text-emerald-600 font-medium">Скидка: -${formatMoney(r.instant_savings)}</span>` : ""}
          <span class="text-slate-500">Налог: ${formatMoney(r.tax)}</span>
          <span class="font-extrabold text-slate-900 text-sm">${formatMoney(r.total)}</span>
        </div>
      </summary>
      <div class="bg-white p-4 border-t border-slate-200 overflow-x-auto">
        <table class="w-full text-xs">
          <thead class="text-slate-400">
            <tr>
              <th class="text-left py-1 px-2">Item #</th>
              <th class="text-left py-1 px-2">В чеке</th>
              <th class="text-left py-1 px-2">Расшифровка</th>
              <th class="text-left py-1 px-2">Категория</th>
              <th class="text-right py-1 px-2">Кол-во</th>
              <th class="text-right py-1 px-2">До скидки</th>
              <th class="text-right py-1 px-2">Скидка</th>
              <th class="text-right py-1 px-2">Чистая сумма</th>
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
  btn.textContent = `⏳ Открываем браузер ${siteName}...`;
  showBanner(`Открыто окно браузера Playwright. Войдите в аккаунт ${siteName} (если требуется 2FA) — чеки выгрузятся автоматически.`, "info");

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_date: startDate, end_date: endDate })
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Ошибка синхронизации");
    }
    showBanner(
      `Успешно загружено чеков: ${data.fetched_receipts} (новых: ${data.inserted_receipts}). Расшифровано новых Item#: ${data.new_items_count} (${data.classification.provider}).`,
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
  showBanner(`Импортируем файл ${file.name}...`, "info");

  const res = await fetch("/api/import", { method: "POST", body: formData });
  const data = await res.json();
  event.target.value = "";

  if (res.ok) {
    setPresetAll();
    const storeName = data.store === "safeway" ? "Safeway" : "Costco";
    showBanner(
      `Импортировано чеков ${storeName}: ${data.fetched_receipts} (новых: ${data.inserted_receipts}). ` +
      `Новых товаров категоризировано: ${data.new_items_count}. Скачано фото: ${data.images_downloaded || 0}.`,
      "success"
    );
    await refreshAll();
  } else {
    showBanner(data.detail || "Ошибка импорта JSON", "error");
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
  showBanner("Сохраняем ключ Gemini API и расшифровываем ожидающие товары...", "info");
  const res = await fetch("/api/config/gemini-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: key })
  });
  if (res.ok) {
    closeGeminiModal();
    document.getElementById("geminiKeyInput").value = "";
    showBanner("Ключ Gemini API сохранён! Новые артикулы расшифрованы.", "success");
    await refreshAll();
  }
}

async function reclassifyPendingWithGemini() {
  closeGeminiModal();
  showBanner("Запускаем классификацию через Gemini API...", "info");
  const res = await fetch("/api/reclassify-pending", { method: "POST" });
  const data = await res.json();
  if (res.ok) {
    showBanner(`Обработано товаров: ${data.classification.classified_count} (${data.classification.provider}).`, "success");
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
    await navigator.clipboard.writeText(await res.text());
    status.className = "text-xs font-medium text-emerald-700";
    status.textContent = `Скрипт ${name} скопирован. Вставьте его в консоль на сайте ${name} (шаг 3).`;
  } catch (err) {
    status.className = "text-xs font-medium text-red-700";
    status.textContent = `Не удалось скопировать автоматически. Откройте «посмотреть код» и скопируйте текст вручную.`;
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
  if (!name) return;
  const res = await fetch("/api/categories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name })
  });
  if (res.ok) {
    document.getElementById("newCategoryInput").value = "";
    closeNewCategoryModal();
    showBanner(`Категория «${name}» добавлена! Теперь её можно выбрать для любого товара.`, "success");
    await refreshAll();
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

function escapeJs(str) {
  return String(str ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

window.addEventListener("DOMContentLoaded", () => {
  try {
    const saved = localStorage.getItem("selectedStore");
    if (STORES.includes(saved)) currentStore = saved;
  } catch (e) {
    // Хранилище недоступно — открываем вкладку Costco
  }
  applyStoreUi();
  setPresetDays(90);
});
