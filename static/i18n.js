// UI translations (Russian and English) and helpers for switching the interface language
const SUPPORTED_LANGS = ["ru", "en"];

const I18N = {
  ru: {
    "page.title": "DigitalCheckAI — Аналитика покупок Costco и Safeway",
    "header.subtitle": "Аналитика чеков Costco и Safeway • ИИ-расшифровка товаров через Gemini",
    "header.import": "📂 Импорт JSON",
    "header.importHelpTitle": "Как получить JSON с чеками",
    "header.syncCostco": "🔄 Скачать с Costco.com",
    "header.syncSafeway": "🔄 Скачать с Safeway.com",
    "header.langTitle": "Язык интерфейса",

    "tabs.all": "Все магазины",
    "tabs.search": "🔍 Поиск",

    "search.title": "Поиск покупок",
    "search.subtitle": "По всем магазинам и за всё время: название из чека, расшифровка или Item #",
    "search.placeholder": "Например: avocado, молоко, 1268246...",
    "search.minChars": "Введите хотя бы 2 символа.",
    "search.notFound": "Ничего не найдено по запросу «{query}».",
    "search.summary": "Найдено покупок: {count}{limited} • Магазины: {stores} • До скидки: {gross} • Со скидкой: {net}",
    "search.limited": " (показаны первые 500)",
    "search.perUnit": "{amount} за ед.",

    "col.date": "Дата",
    "col.store": "Магазин",
    "col.item": "Товар",
    "col.qty": "Кол-во",
    "col.gross": "До скидки",
    "col.net": "Со скидкой",
    "col.lbTitleSearch": "Цена со скидкой за фунт: по весу из названия или по весу развесного товара",
    "col.ozTitle": "Цена со скидкой за унцию веса (не fl oz)",
    "col.lbTitleItems": "Средняя цена со скидкой за фунт",
    "col.ozTitleItems": "Средняя цена со скидкой за унцию веса (не fl oz)",
    "col.category": "Категория",
    "col.lines": "Позиций",
    "col.amount": "Сумма",
    "col.share": "Доля",
    "col.receiptText": "В чеке",
    "col.decoded": "Расшифровка (Gemini / Вручную)",
    "col.decodedShort": "Расшифровка",
    "col.discount": "Скидка",
    "col.netAmount": "Чистая сумма",
    "col.taxCrvTitle": "Налог чека, разнесённый по облагаемым товарам, плюс залог за тару (CRV)",
    "col.taxCrv": "Налог/CRV",
    "col.total": "Итого",
    "col.totalTitle": "Чистая сумма плюс налог и CRV",
    "col.categoryEditable": "Категория (можно изменить)",
    "col.source": "Источник",

    "filters.period": "Период:",
    "filters.days30": "30 дней",
    "filters.days90": "90 дней",
    "filters.halfYear": "Полгода",
    "filters.thisYear": "Этот год",
    "filters.allTime": "Всё время",
    "filters.type": "Тип покупок:",
    "type.all": "Все покупки (Склад + Заправка + Онлайн)",
    "type.warehouse": "🏪 Склад (In-Warehouse)",
    "type.gas": "⛽ Заправка (Gas Station)",
    "type.online": "📦 Онлайн-заказы (Costco.com)",

    "receiptType.warehouse": "Склад",
    "receiptType.gas": "Заправка",
    "receiptType.online": "Онлайн",
    "badge.warehouse": "🏪 Склад",
    "badge.gas": "⛽ Заправка",
    "badge.online": "📦 Онлайн",

    "kpi.totalSpent": "Всего потрачено (с налогом)",
    "kpi.subtotalTax": "Товары: {items} • Налог: {tax}",
    "kpi.savings": "Сэкономлено (Instant Savings)",
    "kpi.savingsHint": "Купоны TPD вычтены из цены товаров",
    "kpi.receiptCount": "Количество чеков / заказов",
    "kpi.receiptTypesEmpty": "Склад: 0 • Заправка: 0 • Онлайн: 0",
    "kpi.avgReceipt": "Средний чек за период",
    "kpi.noDataPeriod": "Нет данных за период",
    "kpi.noReceiptsPeriod": "Нет чеков за выбранный период",
    "kpi.noReceipts": "Нет чеков",

    "membership.title": "💳 Какая карта Costco выгоднее за год",
    "membership.calculating": "Расчёт...",
    "membership.subtitle": "Сравнение Gold Star и Executive (2% возврат со склада и Costco.com без учёта бензина и налогов)",
    "membership.periodFor": "Расчёт за:",
    "membership.thisYear": "Этот год",
    "membership.dateFilter": "Выбранный фильтр дат",
    "membership.currentFilter": "Текущий фильтр дат",
    "membership.yearOption": "{year} год",
    "membership.excludeAlcohol": "Исключать алкоголь",
    "membership.execBetter": "Выгоднее Executive (+{amount} чистой выгоды)",
    "membership.goldBetter": "Выгоднее Gold Star (экономия {amount} на взносе)",

    "period.year": "{year} год",
    "period.range": "{start} — {end}",
    "period.from": "с {start}",
    "period.to": "по {end}",
    "period.all": "Всё время",

    "card.winnerGold": "Выгоднее сейчас",
    "card.winnerExec": "Окупается!",
    "card.annualFee": "Годовая стоимость:",
    "card.perYear": "{amount} / год",
    "card.goldReward": "Годовой кэшбэк (0%):",
    "card.goldNet": "Чистый итог за год:",
    "card.execReward": "Накопленный возврат 2%:",
    "card.execNet": "Чистый итог с учётом 2%:",

    "mb.title": "База расчёта 2% кэшбэка",
    "mb.total": "Всего по чекам (с налогом):",
    "mb.gas": "Минус заправки (Gas Station):",
    "mb.tax": "Минус налоги (Tax):",
    "mb.alcohol": "Минус алкоголь:",
    "mb.eligible": "Квалифицируемые покупки:",
    "mb.projection": "Прогноз на 12 мес.:",
    "mb.projectionReward": "→ возврат",

    "breakeven.initial": "Точка окупаемости апгрейда до Executive (+$65): <b>$3,250.00</b> квалифицируемых покупок в год",
    "breakeven.reached": "Апгрейд до <b>Executive (+{cost})</b> полностью окупился! Чистая выгода над Gold Star: <b class=\"text-emerald-700\">+{advantage}</b>",
    "breakeven.remaining": "До окупаемости <b>Executive (+{cost})</b> осталось потратить ещё <b>{needed}</b> (порог: {threshold} / год)",

    "charts.categoryTitle": "Расходы по категориям (чистая стоимость товаров)",
    "charts.categoryHint": "Нажмите на категорию, чтобы отфильтровать список товаров ниже",
    "charts.addCategory": "+ Своя категория",
    "charts.monthlyTitle": "Динамика трат по месяцам",
    "charts.monthlyHint": "Помесячное распределение расходов по категориям",
    "charts.noData": "Нет данных за выбранный период. Нажмите «Скачать» в правом верхнем углу или «Импорт JSON».",

    "details.itemsTab": "🏷️ Товары и Категории (Item #)",
    "details.receiptsTab": "🧾 Чеки и Онлайн-заказы",
    "items.allCategories": "Все категории",
    "items.searchPlaceholder": "Поиск по Item# или названию...",
    "items.notFound": "Товары не найдены для заданных фильтров.",
    "items.editNameTitle": "Нажмите, чтобы отредактировать расшифрованное название",
    "source.dictionary": "Словарь",
    "source.manual": "Вручную",
    "source.receipt": "Из чека",
    "source.receiptTitle": "Категория определена по самому чеку (топливо, купон), Gemini её не меняет",

    "receipts.notFound": "Чеки за выбранный период не найдены.",
    "receipts.discount": "Скидка: -{amount}",
    "receipts.tax": "Налог: {amount}",
    "receipts.itemTax": "налог {amount}",
    "receipts.itemCrv": "залог CRV {amount}",

    "banner.itemSaved": "Категория для Item #{item} сохранена в локальной базе и пересчитана во всех чеках.",
    "banner.itemFailed": "Не удалось обновить Item #{item}",

    "sync.opening": "⏳ Открываем браузер {site}...",
    "sync.browserOpened": "Открыто окно браузера Playwright. Войдите в аккаунт {site} (если требуется 2FA) — чеки выгрузятся автоматически.",
    "sync.error": "Ошибка синхронизации",
    "error.sync_failed": "Ошибка синхронизации с {site}: {reason}",
    "error.browser_closed": "Окно браузера закрыто до завершения синхронизации.",
    "error.costco_login_timeout": "Не удалось получить токен авторизации Costco.com. Пожалуйста, войдите в аккаунт в открывшемся окне браузера.",
    "error.safeway_login_timeout": "Не удалось дождаться входа в аккаунт Safeway. Пожалуйста, войдите в аккаунт в открывшемся окне браузера.",
    "error.costco_blocked": "Costco GraphQL вернул HTTP {status} (вероятно, запрос отклонила защита Kasada): {body}",
    "error.costco_http": "Costco GraphQL вернул HTTP {status}: {body}",
    "error.costco_not_json": "Costco GraphQL вернул не JSON: {body}",
    "error.costco_graphql": "Ошибка Costco GraphQL: {messages}",
    "error.safeway_http": "Safeway API вернул HTTP {status}: {body}",
    "error.safeway_not_json": "Safeway API вернул не JSON: {body}",
    "error.safeway_list_only": "Это список покупок Safeway без товаров. Нужны детали чеков — воспользуйтесь скриптом выгрузки из справки к импорту.",
    "error.invalid_json": "Некорректный JSON-файл: {error}",
    "error.no_receipts": "В файле не найдено чеков Costco или Safeway.",
    "error.item_not_found": "Артикул {item} не найден в справочнике.",
    "error.category_reserved": "Название «{name}» уже занято другой категорией. Выберите другое.",
    "error.category_empty": "Введите название категории.",
    "error.category_not_english": "Название «{name}» должно быть на английском: его получает Gemini. Русское название укажите во втором поле.",
    "error.unknown": "{message}",
    "sync.success": "Успешно загружено чеков: {fetched} (новых: {inserted}). Расшифровано новых Item#: {newItems} ({provider}).",

    "import.importing": "Импортируем файл {file}...",
    "import.success": "Импортировано чеков {store}: {fetched} (новых: {inserted}). Новых товаров категоризировано: {newItems}. Скачано фото: {images}.",
    "import.error": "Ошибка импорта JSON",

    "gemini.active": "Gemini AI активен ({model})",
    "gemini.setKey": "Указать ключ Gemini API",
    "gemini.title": "Настройка Gemini API",
    "gemini.description": "При первой встрече нового артикула (<b>Item #</b>) приложение отправляет сокращённое описание из чека в <b>Gemini API</b>, расшифровывает его и сохраняет категорию в локальную базу SQLite.",
    "gemini.reclassify": "Переклассифицировать ожидающие товары",
    "gemini.saving": "Сохраняем ключ Gemini API и расшифровываем ожидающие товары...",
    "gemini.saved": "Ключ Gemini API сохранён! Новые артикулы расшифрованы.",
    "gemini.classifying": "Запускаем классификацию через Gemini API...",
    "gemini.processed": "Обработано товаров: {count} ({provider}).",

    "common.cancel": "Отмена",
    "common.save": "Сохранить",
    "common.add": "Добавить",

    "help.title": "Как получить JSON с чеками",
    "help.intro": "Импорт пригодится, если кнопка «Скачать» не срабатывает (например, сайт не пускает браузер Playwright). Чеки выгружаются из вашего обычного браузера готовым скриптом: он обращается к сайту магазина от вашего имени, собирает все чеки и сохраняет их в файл. Формат файла (Costco или Safeway) приложение определит само, повторный импорт тех же чеков не создаёт дублей.",
    "help.step1": "Откройте сайт магазина в Chrome и войдите в аккаунт: <b>costco.com</b> → Account → <b>Orders &amp; Purchases</b>, или <b>safeway.com</b> → Account → <b>Purchases</b>.",
    "help.step2": "Нажмите кнопку ниже — скрипт скопируется в буфер обмена.",
    "help.step3": "На странице магазина нажмите <b>F12</b>, перейдите на вкладку <b>Console</b>, вставьте скрипт (<b>Ctrl+V</b>) и нажмите <b>Enter</b>. Если Chrome не даёт вставить и просит ввести <code class=\"bg-slate-100 px-1 rounded\">allow pasting</code> — введите эту фразу, нажмите Enter и вставьте скрипт ещё раз.",
    "help.step4": "Дождитесь сообщения «DigitalCheckAI: готово» — браузер сохранит файл <code class=\"bg-slate-100 px-1 rounded\">costco-receipts.json</code> или <code class=\"bg-slate-100 px-1 rounded\">safeway-receipts.json</code> в «Загрузки».",
    "help.step5": "Здесь нажмите <b>«📂 Импорт JSON»</b> и выберите этот файл.",
    "help.costcoDesc": "Склад, заправки и онлайн-заказы за последние 3 года (около минуты на 100 чеков).",
    "help.copyCostco": "Скопировать скрипт Costco",
    "help.safewayDesc": "Все покупки в магазинах, которые показывает сайт Safeway.",
    "help.copySafeway": "Скопировать скрипт Safeway",
    "help.viewCode": "посмотреть код",
    "help.privacy": "Скрипт только читает ваши чеки и никуда их не отправляет — файл сохраняется на вашем компьютере. В нём есть личные данные (номер клубной карты, последние цифры банковской карты), не пересылайте его другим. Вставляйте в консоль только код, которому доверяете: его можно посмотреть по ссылке «посмотреть код».",
    "help.copied": "Скрипт {store} скопирован. Вставьте его в консоль на сайте {store} (шаг 3).",
    "help.copyFailed": "Не удалось скопировать автоматически. Откройте «посмотреть код» и скопируйте текст вручную.",

    "newCat.title": "Добавить свою категорию",
    "newCat.label": "Название на английском",
    "newCat.placeholder": "Например: Kids или Pets",
    "newCat.ruLabel": "Название на русском (необязательно)",
    "newCat.ruPlaceholder": "Например: Детские товары или Питомцы",
    "newCat.ruHint": "Gemini получает только английское название, русское показывается в интерфейсе. Чтобы перевести уже созданную категорию, введите её английское название ещё раз вместе с русским.",
    "newCat.added": "Категория «{name}» добавлена! Теперь её можно выбрать для любого товара.",
    "newCat.failed": "Не удалось добавить категорию.",
    "config.loadFailed": "Не удалось загрузить настройки приложения. Обновите страницу."
  },

  en: {
    "page.title": "DigitalCheckAI — Costco and Safeway Purchase Analytics",
    "header.subtitle": "Costco and Safeway receipt analytics • AI item decoding with Gemini",
    "header.import": "📂 Import JSON",
    "header.importHelpTitle": "How to get a JSON file with receipts",
    "header.syncCostco": "🔄 Download from Costco.com",
    "header.syncSafeway": "🔄 Download from Safeway.com",
    "header.langTitle": "Interface language",

    "tabs.all": "All stores",
    "tabs.search": "🔍 Search",

    "search.title": "Purchase search",
    "search.subtitle": "Across all stores and all time: receipt name, decoded name, or Item #",
    "search.placeholder": "e.g. avocado, milk, 1268246...",
    "search.minChars": "Type at least 2 characters.",
    "search.notFound": "Nothing found for “{query}”.",
    "search.summary": "Purchases found: {count}{limited} • Stores: {stores} • Before discounts: {gross} • After discounts: {net}",
    "search.limited": " (showing the first 500)",
    "search.perUnit": "{amount} each",

    "col.date": "Date",
    "col.store": "Store",
    "col.item": "Item",
    "col.qty": "Qty",
    "col.gross": "Before discount",
    "col.net": "After discount",
    "col.lbTitleSearch": "Discounted price per pound: from the weight in the name or the weight of a sold-by-weight item",
    "col.ozTitle": "Discounted price per ounce of weight (not fl oz)",
    "col.lbTitleItems": "Average discounted price per pound",
    "col.ozTitleItems": "Average discounted price per ounce of weight (not fl oz)",
    "col.category": "Category",
    "col.lines": "Lines",
    "col.amount": "Amount",
    "col.share": "Share",
    "col.receiptText": "On receipt",
    "col.decoded": "Decoded name (Gemini / Manual)",
    "col.decodedShort": "Decoded name",
    "col.discount": "Discount",
    "col.netAmount": "Net amount",
    "col.taxCrvTitle": "Receipt tax split across taxable items, plus the container deposit (CRV)",
    "col.taxCrv": "Tax/CRV",
    "col.total": "Total",
    "col.totalTitle": "Net amount plus tax and CRV",
    "col.categoryEditable": "Category (editable)",
    "col.source": "Source",

    "filters.period": "Period:",
    "filters.days30": "30 days",
    "filters.days90": "90 days",
    "filters.halfYear": "6 months",
    "filters.thisYear": "This year",
    "filters.allTime": "All time",
    "filters.type": "Purchase type:",
    "type.all": "All purchases (Warehouse + Gas + Online)",
    "type.warehouse": "🏪 Warehouse (In-Warehouse)",
    "type.gas": "⛽ Gas Station",
    "type.online": "📦 Online orders (Costco.com)",

    "receiptType.warehouse": "Warehouse",
    "receiptType.gas": "Gas",
    "receiptType.online": "Online",
    "badge.warehouse": "🏪 Warehouse",
    "badge.gas": "⛽ Gas",
    "badge.online": "📦 Online",

    "kpi.totalSpent": "Total spent (incl. tax)",
    "kpi.subtotalTax": "Items: {items} • Tax: {tax}",
    "kpi.savings": "Saved (Instant Savings)",
    "kpi.savingsHint": "TPD coupons are subtracted from item prices",
    "kpi.receiptCount": "Receipts / orders",
    "kpi.receiptTypesEmpty": "Warehouse: 0 • Gas: 0 • Online: 0",
    "kpi.avgReceipt": "Average receipt for the period",
    "kpi.noDataPeriod": "No data for the period",
    "kpi.noReceiptsPeriod": "No receipts for the selected period",
    "kpi.noReceipts": "No receipts",

    "membership.title": "💳 Which Costco membership pays off this year",
    "membership.calculating": "Calculating...",
    "membership.subtitle": "Gold Star vs Executive (2% back on warehouse and Costco.com purchases, excluding gas and tax)",
    "membership.periodFor": "Period:",
    "membership.thisYear": "This year",
    "membership.dateFilter": "Selected date filter",
    "membership.currentFilter": "Current date filter",
    "membership.yearOption": "{year}",
    "membership.excludeAlcohol": "Exclude alcohol",
    "membership.execBetter": "Executive pays off (+{amount} net benefit)",
    "membership.goldBetter": "Gold Star is better ({amount} saved on the fee)",

    "period.year": "{year}",
    "period.range": "{start} — {end}",
    "period.from": "from {start}",
    "period.to": "until {end}",
    "period.all": "All time",

    "card.winnerGold": "Better right now",
    "card.winnerExec": "Pays off!",
    "card.annualFee": "Annual fee:",
    "card.perYear": "{amount} / year",
    "card.goldReward": "Annual reward (0%):",
    "card.goldNet": "Net for the year:",
    "card.execReward": "2% reward earned:",
    "card.execNet": "Net including 2%:",

    "mb.title": "2% reward base",
    "mb.total": "Total on receipts (incl. tax):",
    "mb.gas": "Minus gas (Gas Station):",
    "mb.tax": "Minus tax:",
    "mb.alcohol": "Minus alcohol:",
    "mb.eligible": "Eligible purchases:",
    "mb.projection": "12-month projection:",
    "mb.projectionReward": "→ reward",

    "breakeven.initial": "Executive upgrade break-even (+$65): <b>$3,250.00</b> in eligible purchases per year",
    "breakeven.reached": "The <b>Executive (+{cost})</b> upgrade has paid off! Net benefit over Gold Star: <b class=\"text-emerald-700\">+{advantage}</b>",
    "breakeven.remaining": "Spend <b>{needed}</b> more for <b>Executive (+{cost})</b> to pay off (threshold: {threshold} / year)",

    "charts.categoryTitle": "Spending by category (net item cost)",
    "charts.categoryHint": "Click a category to filter the item list below",
    "charts.addCategory": "+ Custom category",
    "charts.monthlyTitle": "Monthly spending",
    "charts.monthlyHint": "Spending by category for each month",
    "charts.noData": "No data for the selected period. Click “Download” in the top right corner or “Import JSON”.",

    "details.itemsTab": "🏷️ Items and Categories (Item #)",
    "details.receiptsTab": "🧾 Receipts and Online Orders",
    "items.allCategories": "All categories",
    "items.searchPlaceholder": "Search by Item # or name...",
    "items.notFound": "No items match the selected filters.",
    "items.editNameTitle": "Click to edit the decoded name",
    "source.dictionary": "Dictionary",
    "source.manual": "Manual",
    "source.receipt": "From receipt",
    "source.receiptTitle": "Category comes from the receipt itself (fuel, coupon); Gemini doesn't change it",

    "receipts.notFound": "No receipts for the selected period.",
    "receipts.discount": "Discount: -{amount}",
    "receipts.tax": "Tax: {amount}",
    "receipts.itemTax": "tax {amount}",
    "receipts.itemCrv": "CRV {amount}",

    "banner.itemSaved": "Category for Item #{item} saved to the local database and updated on all receipts.",
    "banner.itemFailed": "Couldn't update Item #{item}",

    "sync.opening": "⏳ Opening the {site} browser...",
    "sync.browserOpened": "A Playwright browser window is open. Sign in to {site} (complete 2FA if asked) and your receipts will download automatically.",
    "sync.error": "Sync failed",
    "error.sync_failed": "Sync with {site} failed: {reason}",
    "error.browser_closed": "The browser window was closed before the sync finished.",
    "error.costco_login_timeout": "Could not get a Costco.com auth token. Please sign in in the browser window that opened.",
    "error.safeway_login_timeout": "Timed out waiting for the Safeway sign-in. Please sign in in the browser window that opened.",
    "error.costco_blocked": "Costco GraphQL returned HTTP {status} (the Kasada protection probably rejected the request): {body}",
    "error.costco_http": "Costco GraphQL returned HTTP {status}: {body}",
    "error.costco_not_json": "Costco GraphQL returned non-JSON: {body}",
    "error.costco_graphql": "Costco GraphQL error: {messages}",
    "error.safeway_http": "Safeway API returned HTTP {status}: {body}",
    "error.safeway_not_json": "Safeway API returned non-JSON: {body}",
    "error.safeway_list_only": "This is a Safeway purchase list without items. Receipt details are needed: use the export script from the import help.",
    "error.invalid_json": "Invalid JSON file: {error}",
    "error.no_receipts": "No Costco or Safeway receipts found in the file.",
    "error.item_not_found": "Item {item} not found in the catalog.",
    "error.category_reserved": "“{name}” is already used by another category. Please choose another name.",
    "error.category_empty": "Please enter a category name.",
    "error.category_not_english": "“{name}” must be in English: Gemini receives this name. Put the Russian name in the second field.",
    "error.unknown": "{message}",
    "sync.success": "Receipts downloaded: {fetched} (new: {inserted}). New Item #s decoded: {newItems} ({provider}).",

    "import.importing": "Importing {file}...",
    "import.success": "{store} receipts imported: {fetched} (new: {inserted}). New items categorized: {newItems}. Photos downloaded: {images}.",
    "import.error": "JSON import failed",

    "gemini.active": "Gemini AI active ({model})",
    "gemini.setKey": "Set Gemini API key",
    "gemini.title": "Gemini API settings",
    "gemini.description": "The first time a new <b>Item #</b> appears, the app sends its abbreviated receipt description to the <b>Gemini API</b>, decodes it, and saves the category to the local SQLite database.",
    "gemini.reclassify": "Reclassify pending items",
    "gemini.saving": "Saving the Gemini API key and decoding pending items...",
    "gemini.saved": "Gemini API key saved! New items have been decoded.",
    "gemini.classifying": "Starting classification with the Gemini API...",
    "gemini.processed": "Items processed: {count} ({provider}).",

    "common.cancel": "Cancel",
    "common.save": "Save",
    "common.add": "Add",

    "help.title": "How to get a JSON file with receipts",
    "help.intro": "Import is useful when the “Download” button doesn't work (for example, the site blocks the Playwright browser). A ready-made script exports receipts from your regular browser: it calls the store's site on your behalf, collects all receipts, and saves them to a file. The app detects the file format (Costco or Safeway) itself, and importing the same receipts again doesn't create duplicates.",
    "help.step1": "Open the store's site in Chrome and sign in: <b>costco.com</b> → Account → <b>Orders &amp; Purchases</b>, or <b>safeway.com</b> → Account → <b>Purchases</b>.",
    "help.step2": "Click a button below to copy the script to the clipboard.",
    "help.step3": "On the store's page press <b>F12</b>, open the <b>Console</b> tab, paste the script (<b>Ctrl+V</b>) and press <b>Enter</b>. If Chrome blocks pasting and asks you to type <code class=\"bg-slate-100 px-1 rounded\">allow pasting</code>, type that phrase, press Enter, and paste the script again.",
    "help.step4": "Wait for the “DigitalCheckAI: done” message. The browser saves <code class=\"bg-slate-100 px-1 rounded\">costco-receipts.json</code> or <code class=\"bg-slate-100 px-1 rounded\">safeway-receipts.json</code> to Downloads.",
    "help.step5": "Back here, click <b>“📂 Import JSON”</b> and choose that file.",
    "help.costcoDesc": "Warehouse, gas, and online orders for the last 3 years (about a minute per 100 receipts).",
    "help.copyCostco": "Copy the Costco script",
    "help.safewayDesc": "All in-store purchases that the Safeway site shows.",
    "help.copySafeway": "Copy the Safeway script",
    "help.viewCode": "view code",
    "help.privacy": "The script only reads your receipts and doesn't send them anywhere: the file is saved on your computer. It contains personal data (club card number, last digits of your bank card), so don't share it. Only paste code you trust into the console; you can review it via the “view code” link.",
    "help.copied": "{store} script copied. Paste it into the console on the {store} site (step 3).",
    "help.copyFailed": "Couldn't copy automatically. Open “view code” and copy the text by hand.",

    "newCat.title": "Add a custom category",
    "newCat.label": "Category name (English)",
    "newCat.placeholder": "e.g. Kids or Pets",
    "newCat.ruLabel": "Russian name (optional)",
    "newCat.ruPlaceholder": "e.g. Детские товары",
    "newCat.ruHint": "Gemini only gets the English name; the Russian one is shown when the interface is in Russian. To translate an existing category, enter its English name again together with the Russian one.",
    "newCat.added": "Category “{name}” added! You can now assign it to any item.",
    "newCat.failed": "Could not add the category.",
    "config.loadFailed": "Could not load the app settings. Please reload the page."
  }
};

function detectLang() {
  try {
    const saved = localStorage.getItem("uiLang");
    if (SUPPORTED_LANGS.includes(saved)) return saved;
  } catch (e) {
    // Storage is unavailable, so fall back to the browser language
  }
  return (navigator.language || "").toLowerCase().startsWith("ru") ? "ru" : "en";
}

let currentLang = detectLang();

function t(key, params = {}) {
  const template = I18N[currentLang][key] ?? I18N.ru[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match));
}

// Categories are stored under their English names; Russian labels (defaults and custom) come from /api/config
let categoryLabelsRu = {};

function categoryLabel(category) {
  if (currentLang !== "ru") return category;
  return categoryLabelsRu[category] || category;
}

function applyStaticTranslations() {
  document.documentElement.lang = currentLang;
  document.title = t("page.title");
  document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  // Only our own dictionary strings go into innerHTML
  document.querySelectorAll("[data-i18n-html]").forEach(el => { el.innerHTML = t(el.dataset.i18nHtml); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  document.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = t(el.dataset.i18nTitle); });
  document.querySelectorAll("[data-lang]").forEach(btn => {
    btn.className = btn.dataset.lang === currentLang
      ? "px-2 py-1 font-semibold bg-slate-800 text-white"
      : "px-2 py-1 font-medium bg-white text-slate-600 hover:bg-slate-50";
  });
}

function setLanguage(lang) {
  if (!SUPPORTED_LANGS.includes(lang) || lang === currentLang) return;
  currentLang = lang;
  try {
    localStorage.setItem("uiLang", lang);
  } catch (e) {
    // Storage is unavailable, so the choice only lasts until the page reloads
  }
  applyStaticTranslations();
  // Dynamic content (tables, charts, banners from JS) is rendered with t(), so re-render it
  // from the last loaded data: the data itself doesn't depend on the language
  rerenderAll();
}
