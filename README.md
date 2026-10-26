# DigitalCheckAI 🛒📊

A local web app that downloads receipts and orders from **costco.com** and **safeway.com**, decodes abbreviated product names (`Item #`) with the **Gemini API**, and visualizes spending by category for any period.

## Features
- **Every Costco.com purchase type**:
  - 🏪 Warehouse purchases (**In-Warehouse Receipts**)
  - ⛽ Fuel (**Costco Gas Station**)
  - 📦 Online orders (**Costco.com Online Orders**)
- **Safeway in-store purchases** with product photos and prices before and after discounts.
- **Search across all stores**, with price per pound and per ounce.
- **JSON import** for Costco and Safeway: ready-made export scripts for the browser console are in the "?" help next to the import button.
- **Correct coupon handling (Instant Savings / TPD)**: discount lines are attached to their `Item #` and subtracted from its amount (`net_amount`).
- **Smart categorization by `Item #`**:
  1. The first time a new `Item #` appears, its abbreviated receipt name (e.g. `KS ORG EVOO`) is sent to the **Gemini API** (`gemini-2.5-flash`), which returns a full readable name and a category.
  2. The result is stored in the local SQLite database (`data/costco.db` -> `item_catalog` table). Later purchases of the same `Item #` read it from the local database without calling Gemini again.
  3. You can **change any item's category or name by hand** at any time, right in the dashboard table, and the statistics for all receipts update immediately.

## Quick start (Windows)

Double-click **`run.bat`** or run in a terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\playwright install chromium
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open **http://localhost:8000** in your browser.

## Gemini API key
1. Click **"Указать ключ Gemini API"** ("Set Gemini API key") in the top right corner of the dashboard, or set `GEMINI_API_KEY=...` in the `.env` file.
2. The key is stored locally in `.env`.

## Running tests
```powershell
.\.venv\Scripts\python -m pytest -v
```
