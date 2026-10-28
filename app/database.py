"""Local SQLite storage for Costco receipts, the item catalog, and analytics."""
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from typing import Any

from app.config import (
    ALCOHOL_CATEGORY,
    DB_PATH,
    DEFAULT_CATEGORIES,
    DEFAULT_CATEGORY_LABELS_RU,
    OTHER_CATEGORY,
    default_category_lookup,
    is_english_category_name,
    merge_category_labels_ru,
)
from app.errors import AppError
from app.receipt_tax import SPLIT_RULES_VERSION, is_crv_line, split_receipt
from app.unit_price import add_unit_prices

# PRAGMA user_version after each one-time data migration (see init_db)
REPARSE_SCHEMA_VERSION = 1
ENGLISH_NAMES_SCHEMA_VERSION = 2
CRV_SCHEMA_VERSION = 3

# The catalog row a receipt line counts under: a deposit folded into a bottle counts under the bottle's item
_CATALOG_OF_LINE = """
                LEFT JOIN receipt_items owner ON owner.id = ri.crv_owner_id
                JOIN item_catalog c ON c.item_number = COALESCE(owner.item_number, ri.item_number)"""


def _category_reserved(value: str) -> AppError:
    return AppError("category_reserved", f"'{value}' is already used by another category.", name=value)


def _category_empty() -> AppError:
    return AppError("category_empty", "The category name is empty.")


def _category_not_english(value: str) -> AppError:
    return AppError("category_not_english", f"Category name '{value}' must be in English.", name=value)


class Database:
    """Manages the DigitalCheckAI SQLite database."""

    def __init__(self, db_path: Path | str = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Context manager for a SQLite connection that returns rows as dict-like Row objects."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # SQLite's built-in lower() only handles ASCII; searching Russian names needs Unicode
        conn.create_function("casefold", 1, lambda s: s.casefold() if isinstance(s, str) else s, deterministic=True)
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Creates tables and indexes if they don't exist yet."""
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS receipts (
                    transaction_barcode TEXT PRIMARY KEY,
                    transaction_date TEXT NOT NULL,
                    receipt_type TEXT NOT NULL DEFAULT 'warehouse',
                    warehouse_name TEXT NOT NULL DEFAULT '',
                    subtotal REAL NOT NULL DEFAULT 0.0,
                    tax REAL NOT NULL DEFAULT 0.0,
                    total REAL NOT NULL DEFAULT 0.0,
                    instant_savings REAL NOT NULL DEFAULT 0.0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(transaction_date);
                CREATE INDEX IF NOT EXISTS idx_receipts_type ON receipts(receipt_type);

                CREATE TABLE IF NOT EXISTS item_catalog (
                    item_number TEXT PRIMARY KEY,
                    raw_description TEXT NOT NULL,
                    clean_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    is_manual_override INTEGER NOT NULL DEFAULT 0,
                    classified_by TEXT NOT NULL DEFAULT 'pending',
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_catalog_category ON item_catalog(category);

                CREATE TABLE IF NOT EXISTS receipt_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_barcode TEXT NOT NULL,
                    item_number TEXT NOT NULL,
                    raw_description TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 1.0,
                    unit_price REAL NOT NULL DEFAULT 0.0,
                    gross_amount REAL NOT NULL DEFAULT 0.0,
                    discount_amount REAL NOT NULL DEFAULT 0.0,
                    net_amount REAL NOT NULL DEFAULT 0.0,
                    tax_flag TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (transaction_barcode) REFERENCES receipts(transaction_barcode) ON DELETE CASCADE,
                    FOREIGN KEY (item_number) REFERENCES item_catalog(item_number)
                );

                CREATE INDEX IF NOT EXISTS idx_receipt_items_barcode ON receipt_items(transaction_barcode);
                CREATE INDEX IF NOT EXISTS idx_receipt_items_item ON receipt_items(item_number);

                CREATE TABLE IF NOT EXISTS custom_categories (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                """
            )
            # Migration: a custom category is named in English (that name goes to Gemini) with an optional
            # Russian UI label. Older databases kept the Russian name with an optional English one
            category_columns = {row["name"] for row in conn.execute("PRAGMA table_info(custom_categories)")}
            if "name_ru" not in category_columns:
                if "name_en" in category_columns:
                    self._swap_custom_category_names(conn)
                    conn.execute("ALTER TABLE custom_categories RENAME COLUMN name_en TO name_ru")
                else:
                    conn.execute("ALTER TABLE custom_categories ADD COLUMN name_ru TEXT NOT NULL DEFAULT ''")
            # Migration: receipt store (costco / safeway) for databases created before Safeway support
            receipt_columns = {row["name"] for row in conn.execute("PRAGMA table_info(receipts)")}
            if "store" not in receipt_columns:
                conn.execute("ALTER TABLE receipts ADD COLUMN store TEXT NOT NULL DEFAULT 'costco'")
            # Migration: the version of the tax split rules a receipt's lines were split with (NULL: not split)
            if "tax_split_version" not in receipt_columns:
                conn.execute("ALTER TABLE receipts ADD COLUMN tax_split_version INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_receipts_store ON receipts(store)")
            # Migration: item image (source URL and local file in data/images)
            catalog_columns = {row["name"] for row in conn.execute("PRAGMA table_info(item_catalog)")}
            if "image_url" not in catalog_columns:
                conn.execute("ALTER TABLE item_catalog ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
            if "image_file" not in catalog_columns:
                conn.execute("ALTER TABLE item_catalog ADD COLUMN image_file TEXT NOT NULL DEFAULT ''")
            # Migration: sold-by-weight items (quantity is the weight in pounds)
            item_columns = {row["name"] for row in conn.execute("PRAGMA table_info(receipt_items)")}
            if "is_weight" not in item_columns:
                conn.execute("ALTER TABLE receipt_items ADD COLUMN is_weight INTEGER NOT NULL DEFAULT 0")
            # Migration: each line's tax share and CRV (see app.receipt_tax), stored when its receipt is saved.
            # crv_owner_id is the receipt_items id a CRV line ("CA REDEMP VAL T EI/919140") is folded into;
            # the receipt's tax_split_version says which rules produced them (see _split_outdated_receipts)
            if "crv_owner_id" not in item_columns:
                conn.execute("ALTER TABLE receipt_items ADD COLUMN crv_owner_id INTEGER")
            if "tax_unsplit" not in item_columns:
                conn.execute("ALTER TABLE receipt_items ADD COLUMN tax_amount REAL")
                conn.execute("ALTER TABLE receipt_items ADD COLUMN crv_amount REAL NOT NULL DEFAULT 0.0")
                conn.execute("ALTER TABLE receipt_items ADD COLUMN tax_unsplit INTEGER")
            if "is_deposit" not in item_columns:
                # A CRV line, folded into its bottle or not: never listed as a product
                conn.execute("ALTER TABLE receipt_items ADD COLUMN is_deposit INTEGER NOT NULL DEFAULT 0")
            if "crv_for_item" in item_columns:
                # An earlier version also stored the owner's item number, which crv_owner_id already gives
                conn.execute("ALTER TABLE receipt_items DROP COLUMN crv_for_item")
            self._merge_categories_clashing_with_defaults(conn)
            schema_version = conn.execute("PRAGMA user_version").fetchone()[0]

        if schema_version < CRV_SCHEMA_VERSION:
            # Migration: online orders with several items no longer mark every line taxable. The old parser's
            # all-"Y" flags are reset before any re-parse below, which gives back the flags an order really has
            # (from its raw_json); an order it can't redo keeps no flags, so its tax isn't split over groceries.
            # An order with any other flag got it from an imported file and keeps it. An all-"Y" order whose
            # raw_json can't be re-parsed can't be told apart from the old parser's, so it shows its tax as not
            # split rather than risk a wrong split. Their stored split came from the old flags, so they are
            # split again at the end
            all_taxable_online = """
                SELECT ri.transaction_barcode
                FROM receipt_items ri
                JOIN receipts r ON r.transaction_barcode = ri.transaction_barcode
                WHERE r.receipt_type = 'online'
                GROUP BY ri.transaction_barcode
                HAVING COUNT(*) > 1 AND MIN(ri.tax_flag) = 'Y' AND MAX(ri.tax_flag) = 'Y'
            """
            with self.connect() as conn:
                conn.execute(
                    f"UPDATE receipts SET tax_split_version = NULL WHERE transaction_barcode IN ({all_taxable_online})"
                )
                conn.execute(f"UPDATE receipt_items SET tax_flag = '' WHERE transaction_barcode IN ({all_taxable_online})")

        if schema_version < REPARSE_SCHEMA_VERSION:
            # Migration: receipts stored by older parsers have wrong receipt types, coupon lines and
            # parser categories (e.g. "REGULAR COFFEE" taken for fuel). Re-parse them from raw_json once
            self.reparse_stored_receipts()
            with self.connect() as conn:
                conn.execute(f"PRAGMA user_version = {REPARSE_SCHEMA_VERSION}")

        if schema_version < ENGLISH_NAMES_SCHEMA_VERSION:
            # Migration: names the app generated itself used to be Russian; product names are English now
            self._translate_generated_names()
            with self.connect() as conn:
                conn.execute(f"PRAGMA user_version = {ENGLISH_NAMES_SCHEMA_VERSION}")

        if schema_version < CRV_SCHEMA_VERSION:
            # Migration: CRV catalog rows get a parser category instead of going to Gemini, and online orders
            # get their new flags. Re-parse once, unless the re-parse above already ran with the current
            # parsers. Catalog rows of receipts the re-parse can't redo (no usable raw_json) are fixed directly
            if schema_version >= REPARSE_SCHEMA_VERSION:
                self.reparse_stored_receipts()
            with self.connect() as conn:
                self._mark_crv_catalog_rows(conn)
                conn.execute(f"PRAGMA user_version = {CRV_SCHEMA_VERSION}")

        # Receipts not yet split, or split by older rules (an earlier build, or before SPLIT_RULES_VERSION was
        # bumped), are split with the current ones. Re-parsed receipts were split on saving, so each is done once
        with self.connect() as conn:
            self._split_outdated_receipts(conn)

    @staticmethod
    def _mark_crv_catalog_rows(conn: sqlite3.Connection) -> None:
        """Gives CRV catalog rows the parser's category, as the parser does for new ones (see parser._mark_crv)."""
        # Imported here to keep the storage layer from loading the scrapers on every import
        from app.scraper.parser import CRV_NAME

        rows = conn.execute(
            "SELECT item_number, raw_description FROM item_catalog "
            "WHERE is_manual_override = 0 AND classified_by != 'parser' AND LTRIM(raw_description) LIKE 'CA REDEMP%'"
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        conn.executemany(
            """
            UPDATE item_catalog
            SET clean_name = CASE WHEN classified_by = 'gemini' THEN clean_name ELSE ? END,
                category = ?, classified_by = 'parser', updated_at = ?
            WHERE item_number = ?
            """,
            [(CRV_NAME, OTHER_CATEGORY, now, row["item_number"]) for row in rows if is_crv_line(row["raw_description"])],
        )

    @classmethod
    def _split_outdated_receipts(cls, conn: sqlite3.Connection) -> None:
        """Stores the tax split of every receipt whose tax_split_version isn't SPLIT_RULES_VERSION."""
        outdated = "SELECT transaction_barcode FROM receipts WHERE tax_split_version IS NOT ?"
        rows = conn.execute(
            f"""
            SELECT ri.id, ri.transaction_barcode, ri.item_number, ri.raw_description, ri.net_amount,
                   ri.tax_flag, r.tax
            FROM receipt_items ri
            JOIN receipts r ON r.transaction_barcode = ri.transaction_barcode
            WHERE ri.transaction_barcode IN ({outdated})
            ORDER BY ri.transaction_barcode, ri.id
            """,
            (SPLIT_RULES_VERSION,),
        ).fetchall()
        for _, group in groupby(rows, key=lambda row: row["transaction_barcode"]):
            lines = [dict(row) for row in group]
            cls._save_split(conn, lines, lines[0]["tax"])
        # Receipts without lines have nothing to split
        conn.execute(
            "UPDATE receipts SET tax_split_version = ? WHERE tax_split_version IS NOT ?",
            (SPLIT_RULES_VERSION, SPLIT_RULES_VERSION),
        )

    @staticmethod
    def _save_split(conn: sqlite3.Connection, lines: list[dict[str, Any]], receipt_tax: float) -> None:
        """Stores one receipt's CRV owners and tax split; lines are in receipt order, each with its id.

        The caller marks the receipt with SPLIT_RULES_VERSION.
        """
        conn.executemany(
            "UPDATE receipt_items SET crv_owner_id = ?, tax_amount = ?, crv_amount = ?, is_deposit = ?, tax_unsplit = ?"
            " WHERE id = ?",
            [
                (
                    lines[split["crv_owner"]]["id"] if split["crv_owner"] is not None else None,
                    split["tax_amount"],
                    split["crv_amount"],
                    1 if split["is_deposit"] else 0,
                    1 if split["tax_unsplit"] else 0,
                    line["id"],
                )
                for line, split in zip(lines, split_receipt(lines, receipt_tax), strict=True)
            ],
        )

    @staticmethod
    def _swap_custom_category_names(conn: sqlite3.Connection) -> None:
        """
        One-time migration for custom_categories(name, name_en): a row with an English name is renamed
        to it, and the old (Russian) name moves into name_en, which then becomes name_ru. When a category
        with that English name already exists (in any letter case), the row is merged into it and its
        Russian name becomes that category's label if it has none.
        Rows without an English name keep their name; add_custom_category can translate them later.
        """
        rows = conn.execute("SELECT name, name_en FROM custom_categories WHERE TRIM(name_en) != ''").fetchall()
        for row in rows:
            old, new = row["name"], row["name_en"].strip()
            if new == old:
                continue
            existing = conn.execute(
                "SELECT name FROM custom_categories WHERE casefold(name) = ? AND name != ?", (new.casefold(), old)
            ).fetchone()
            if existing:
                new = existing["name"]
                conn.execute("DELETE FROM custom_categories WHERE name = ?", (old,))
                conn.execute(
                    "UPDATE custom_categories SET name_en = ? WHERE name = ? AND TRIM(name_en) = ''", (old, new)
                )
            else:
                conn.execute("UPDATE custom_categories SET name = ?, name_en = ? WHERE name = ?", (new, old, old))
            conn.execute("UPDATE item_catalog SET category = ? WHERE category = ?", (new, old))

    @staticmethod
    def _merge_categories_clashing_with_defaults(conn: sqlite3.Connection) -> None:
        """
        Migration (idempotent, runs on every start). Item categories still stored under an old Russian
        default name ("Прочее") or saved as a custom name that matches a default ("other") are moved to
        that default, blank ones to "Other", and a Russian label that repeats a default's is cleared.
        """
        defaults = default_category_lookup()
        custom = [row["name"] for row in conn.execute("SELECT name FROM custom_categories")]
        used = [row["category"] for row in conn.execute("SELECT DISTINCT category FROM item_catalog WHERE category != ''")]
        for name in {*custom, *used}:
            if name in DEFAULT_CATEGORY_LABELS_RU:
                # A default saved as a custom row adds nothing but a label the UI ignores
                conn.execute("DELETE FROM custom_categories WHERE name = ?", (name,))
                continue
            target = defaults.get(name.strip().casefold()) or (OTHER_CATEGORY if not name.strip() else None)
            if target:
                conn.execute("UPDATE item_catalog SET category = ? WHERE category = ?", (target, name))
                conn.execute("DELETE FROM custom_categories WHERE name = ?", (name,))
        for row in conn.execute("SELECT name, name_ru FROM custom_categories WHERE name_ru != ''").fetchall():
            if row["name_ru"].strip().casefold() in defaults:
                conn.execute("UPDATE custom_categories SET name_ru = '' WHERE name = ?", (row["name"],))

    def _translate_generated_names(self) -> None:
        """Renames parser and fallback catalog rows from the old Russian names to the English ones."""
        from app.categorizer.gemini_classifier import fallback_categorize_item
        from app.scraper.parser import COUPON_NAME_PREFIX, FUEL_NAME_PREFIX

        with self.connect() as conn:
            for old_prefix, new_prefix in (("Топливо Costco (", FUEL_NAME_PREFIX), ("Скидка / купон (", COUPON_NAME_PREFIX)):
                conn.execute(
                    """
                    UPDATE item_catalog
                    SET clean_name = ? || substr(clean_name, ?)
                    WHERE is_manual_override = 0 AND classified_by = 'parser' AND clean_name LIKE ? || '%'
                    """,
                    (new_prefix, len(old_prefix) + 1, old_prefix),
                )
            rows = conn.execute(
                "SELECT item_number, raw_description FROM item_catalog "
                "WHERE is_manual_override = 0 AND classified_by = 'fallback'"
            ).fetchall()
            for row in rows:
                result = fallback_categorize_item(row["item_number"], row["raw_description"])
                conn.execute(
                    "UPDATE item_catalog SET clean_name = ? WHERE item_number = ?",
                    (result["clean_name"], row["item_number"]),
                )

    def reparse_stored_receipts(self) -> int:
        """
        Re-parses every stored receipt from its raw_json with the current parsers and saves it again,
        which also fixes parser categories in item_catalog. Returns the number of re-parsed receipts.
        """
        # Imported here to keep the storage layer from loading the scrapers on every import
        from app.scraper.parser import parse_online_order, parse_warehouse_or_gas_receipt
        from app.scraper.safeway_client import parse_safeway_receipt

        with self.connect() as conn:
            rows = conn.execute("SELECT transaction_barcode, store, receipt_type, raw_json FROM receipts").fetchall()

        reparsed: list[dict[str, Any]] = []
        for row in rows:
            try:
                raw = json.loads(row["raw_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict) or not raw:
                continue
            if row["store"] == "safeway":
                parsed = parse_safeway_receipt(raw)
            elif row["receipt_type"] == "online":
                parsed = parse_online_order(raw)
            else:
                parsed = parse_warehouse_or_gas_receipt(raw)
            # Keep the stored receipt if raw_json can't be parsed into the same receipt with items
            if parsed and parsed["transaction_barcode"] == row["transaction_barcode"] and parsed["items"]:
                reparsed.append(parsed)

        self.upsert_receipts(reparsed)
        return len(reparsed)

    def get_all_categories(self) -> list[str]:
        """Returns the combined list of default and user-defined categories."""
        with self.connect() as conn:
            return self._read_categories(conn)[0]

    @staticmethod
    def _read_categories(conn: sqlite3.Connection) -> tuple[list[str], dict[str, str]]:
        """
        Returns all categories (defaults, custom ones, then ones only used in item_catalog) and a lookup
        from every casefolded category name and Russian label to the stored category name.
        """
        categories = list(DEFAULT_CATEGORIES)
        lookup = default_category_lookup()
        for row in conn.execute("SELECT name, name_ru FROM custom_categories ORDER BY name"):
            if row["name"] not in categories:
                categories.append(row["name"])
            lookup.setdefault(row["name"].casefold(), row["name"])
            if row["name_ru"]:
                lookup.setdefault(row["name_ru"].casefold(), row["name"])
        for row in conn.execute("SELECT DISTINCT category FROM item_catalog WHERE category != ''"):
            if row["category"] not in categories:
                categories.append(row["category"])
            lookup.setdefault(row["category"].casefold(), row["category"])
        return categories, lookup

    def add_custom_category(self, name: str, name_ru: str = "") -> list[str]:
        """
        Adds a new user-defined category. The name is English (it is sent to Gemini); name_ru is an optional
        Russian UI label. For an existing category, a non-empty name_ru sets its label, so a category added
        earlier can get a translation too (the name may differ in letter case). When name_ru names a category
        an old database kept under a Russian name ("Питомцы"), that category is renamed to the English name.
        Raises AppError: "category_empty", "category_not_english", or "category_reserved" when the name or
        label belongs to another category, including the defaults (a custom "other" would compete with "Other").
        """
        clean = name.strip()
        clean_ru = name_ru.strip()
        if not clean:
            raise _category_empty()
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            categories, lookup = self._read_categories(conn)
            owner = lookup.get(clean.casefold())
            if owner in DEFAULT_CATEGORIES or (owner and owner.casefold() != clean.casefold()):
                raise _category_reserved(clean)
            clean = owner or clean
            if not is_english_category_name(clean):
                raise _category_not_english(clean)
            legacy = None
            if clean_ru and lookup.get(clean_ru.casefold(), clean) != clean:
                legacy = lookup[clean_ru.casefold()]
                if legacy in DEFAULT_CATEGORIES or is_english_category_name(legacy):
                    raise _category_reserved(clean_ru)
            conn.execute(
                """
                INSERT INTO custom_categories (name, name_ru, created_at) VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET name_ru = excluded.name_ru WHERE excluded.name_ru != ''
                """,
                (clean, clean_ru, now),
            )
            if legacy:
                conn.execute("UPDATE item_catalog SET category = ? WHERE category = ?", (clean, legacy))
                conn.execute("DELETE FROM custom_categories WHERE name = ?", (legacy,))
                categories.remove(legacy)
        if clean not in categories:
            categories.append(clean)
        return categories

    def get_category_labels_ru(self) -> dict[str, str]:
        """Russian UI labels of every category that has one: the defaults and labelled custom categories."""
        with self.connect() as conn:
            rows = conn.execute("SELECT name, name_ru FROM custom_categories WHERE name_ru != ''").fetchall()
        return merge_category_labels_ru({row["name"]: row["name_ru"] for row in rows})

    def get_existing_catalog_map(self, item_numbers: list[str]) -> dict[str, dict[str, Any]]:
        """Returns existing item_catalog rows for the given item numbers."""
        if not item_numbers:
            return {}
        placeholders = ",".join("?" for _ in item_numbers)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM item_catalog WHERE item_number IN ({placeholders})",
                item_numbers,
            ).fetchall()
        return {row["item_number"]: dict(row) for row in rows}

    def get_pending_items_for_gemini(self) -> list[dict[str, Any]]:
        """Returns item_catalog items that haven't been decoded by Gemini or by hand yet."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    c.item_number,
                    c.raw_description,
                    c.clean_name,
                    c.category,
                    MAX(ri.unit_price) AS sample_unit_price
                FROM item_catalog c
                JOIN receipt_items ri ON ri.item_number = c.item_number
                WHERE c.is_manual_override = 0 AND c.classified_by NOT IN ('gemini', 'parser')
                GROUP BY c.item_number
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def save_categorized_items(self, items: list[dict[str, Any]], classified_by: str = "gemini") -> int:
        """
        Saves decoded names and categories to item_catalog.
        Never overwrites rows with is_manual_override = 1.
        """
        if not items:
            return 0
        now = datetime.now(UTC).isoformat()
        updated = 0
        with self.connect() as conn:
            for item in items:
                item_number = str(item["item_number"]).strip()
                raw_desc = str(item.get("raw_description") or item.get("clean_name") or item_number).strip()
                clean_name = str(item.get("clean_name") or raw_desc).strip()
                category = str(item.get("category") or OTHER_CATEGORY).strip()

                existing = conn.execute(
                    "SELECT is_manual_override FROM item_catalog WHERE item_number = ?",
                    (item_number,),
                ).fetchone()

                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO item_catalog
                            (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
                        VALUES (?, ?, ?, ?, 0, ?, ?)
                        """,
                        (item_number, raw_desc, clean_name, category, classified_by, now),
                    )
                    updated += 1
                elif existing["is_manual_override"] == 0:
                    conn.execute(
                        """
                        UPDATE item_catalog
                        SET clean_name = ?,
                            category = ?,
                            classified_by = ?,
                            updated_at = ?
                        WHERE item_number = ? AND is_manual_override = 0
                        """,
                        (clean_name, category, classified_by, now, item_number),
                    )
                    updated += 1
        return updated

    def get_items_missing_images(self) -> list[dict[str, str]]:
        """Items whose image URL is known but whose image hasn't been downloaded yet."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT item_number, image_url FROM item_catalog WHERE image_url != '' AND image_file = ''"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_item_image_file(self, item_number: str, image_file: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE item_catalog SET image_file = ? WHERE item_number = ?",
                (image_file, item_number),
            )

    def update_item_category_manual(
        self,
        item_number: str,
        category: str,
        clean_name: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Updates the category (and optionally the decoded name) as a manual user edit.
        Sets is_manual_override = 1.
        """
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM item_catalog WHERE item_number = ?",
                (item_number,),
            ).fetchone()
            if not existing:
                return None

            new_clean_name = clean_name.strip() if clean_name and clean_name.strip() else existing["clean_name"]
            # A typed "прочее" or "pets" means the existing "Other" or "Pets", not a new category
            new_category = category.strip()
            if not new_category:
                raise _category_empty()
            # An existing category is accepted as is, even one an old database kept under a Russian name;
            # a new one must be English
            known = self._read_categories(conn)[1].get(new_category.casefold())
            if known is None and not is_english_category_name(new_category):
                raise _category_not_english(new_category)
            new_category = known or new_category

            conn.execute(
                """
                UPDATE item_catalog
                SET category = ?,
                    clean_name = ?,
                    is_manual_override = 1,
                    classified_by = 'manual',
                    updated_at = ?
                WHERE item_number = ?
                """,
                (new_category, new_clean_name, now, item_number),
            )
            if new_category not in DEFAULT_CATEGORIES:
                conn.execute(
                    "INSERT OR IGNORE INTO custom_categories (name, created_at) VALUES (?, ?)",
                    (new_category, now),
                )
            updated = conn.execute(
                "SELECT * FROM item_catalog WHERE item_number = ?",
                (item_number,),
            ).fetchone()
            return dict(updated) if updated else None

    def upsert_receipts(self, parsed_receipts: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Saves normalized receipts and their line items to the database.
        Returns receipt counts and the list of brand-new items (not previously in item_catalog).
        """
        now = datetime.now(UTC).isoformat()
        inserted_receipts = 0
        updated_receipts = 0
        skipped_receipts = 0
        new_items_for_catalog: dict[str, dict[str, Any]] = {}

        with self.connect() as conn:
            for receipt in parsed_receipts:
                barcode = str(receipt["transaction_barcode"]).strip()
                if not barcode:
                    continue

                exists = conn.execute(
                    "SELECT 1 FROM receipts WHERE transaction_barcode = ?",
                    (barcode,),
                ).fetchone()

                if exists and receipt.get("is_partial"):
                    # Details didn't load: keep the previously stored receipt and its items as they are
                    skipped_receipts += 1
                    continue

                if exists:
                    updated_receipts += 1
                    conn.execute(
                        "DELETE FROM receipt_items WHERE transaction_barcode = ?",
                        (barcode,),
                    )
                    conn.execute(
                        """
                        UPDATE receipts
                        SET transaction_date = ?,
                            receipt_type = ?,
                            store = ?,
                            warehouse_name = ?,
                            subtotal = ?,
                            tax = ?,
                            total = ?,
                            instant_savings = ?,
                            raw_json = ?
                        WHERE transaction_barcode = ?
                        """,
                        (
                            receipt["transaction_date"],
                            receipt.get("receipt_type", "warehouse"),
                            receipt.get("store", "costco"),
                            receipt.get("warehouse_name", ""),
                            float(receipt.get("subtotal", 0.0)),
                            float(receipt.get("tax", 0.0)),
                            float(receipt.get("total", 0.0)),
                            float(receipt.get("instant_savings", 0.0)),
                            json.dumps(receipt.get("raw_json", {}), ensure_ascii=False),
                            barcode,
                        ),
                    )
                else:
                    inserted_receipts += 1
                    conn.execute(
                        """
                        INSERT INTO receipts (
                            transaction_barcode, transaction_date, receipt_type, store, warehouse_name,
                            subtotal, tax, total, instant_savings, raw_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            barcode,
                            receipt["transaction_date"],
                            receipt.get("receipt_type", "warehouse"),
                            receipt.get("store", "costco"),
                            receipt.get("warehouse_name", ""),
                            float(receipt.get("subtotal", 0.0)),
                            float(receipt.get("tax", 0.0)),
                            float(receipt.get("total", 0.0)),
                            float(receipt.get("instant_savings", 0.0)),
                            json.dumps(receipt.get("raw_json", {}), ensure_ascii=False),
                            now,
                        ),
                    )

                inserted_lines: list[dict[str, Any]] = []
                for line in receipt.get("items", []):
                    item_num = str(line["item_number"]).strip()
                    raw_desc = str(line.get("raw_description", item_num)).strip()

                    cat_row = conn.execute(
                        "SELECT item_number, classified_by, is_manual_override FROM item_catalog WHERE item_number = ?",
                        (item_num,),
                    ).fetchone()

                    if cat_row is None:
                        # The parser already knows the category (fuel): keep it and don't send the item
                        # to Gemini or the fallback, which could move fuel out of the gas category
                        from_parser = bool(line.get("default_category"))
                        default_cat = line.get("default_category") or OTHER_CATEGORY
                        default_clean = line.get("default_clean_name") or raw_desc
                        conn.execute(
                            """
                            INSERT INTO item_catalog
                                (item_number, raw_description, clean_name, category, is_manual_override, classified_by, updated_at)
                            VALUES (?, ?, ?, ?, 0, ?, ?)
                            """,
                            (item_num, raw_desc, default_clean, default_cat, "parser" if from_parser else "pending", now),
                        )
                        if not from_parser:
                            new_items_for_catalog[item_num] = {
                                "item_number": item_num,
                                "raw_description": raw_desc,
                                "sample_unit_price": float(line.get("unit_price", 0.0)),
                                "receipt_type": receipt.get("receipt_type", "warehouse"),
                            }
                    elif (
                        line.get("default_category")
                        and not cat_row["is_manual_override"]
                        and cat_row["classified_by"] != "parser"
                    ):
                        # The row was created before the parser knew its category (e.g. fuel saved as pending):
                        # take the parser's category so the classifier can't move it again. A name decoded
                        # by Gemini is kept: it reads better than the parser's template
                        conn.execute(
                            """
                            UPDATE item_catalog
                            SET clean_name = CASE WHEN classified_by = 'gemini' THEN clean_name ELSE ? END,
                                category = ?, classified_by = 'parser', updated_at = ?
                            WHERE item_number = ?
                            """,
                            (line.get("default_clean_name") or raw_desc, line["default_category"], now, item_num),
                        )
                    elif (
                        not line.get("default_category")
                        and not cat_row["is_manual_override"]
                        and cat_row["classified_by"] == "parser"
                    ):
                        # An older parser gave this item a category (e.g. "REGULAR COFFEE" as fuel) that the
                        # current one doesn't: unlock the row and send it to the classifier again
                        conn.execute(
                            """
                            UPDATE item_catalog
                            SET clean_name = ?, category = ?, classified_by = 'pending', updated_at = ?
                            WHERE item_number = ?
                            """,
                            (raw_desc, OTHER_CATEGORY, now, item_num),
                        )
                        new_items_for_catalog[item_num] = {
                            "item_number": item_num,
                            "raw_description": raw_desc,
                            "sample_unit_price": float(line.get("unit_price", 0.0)),
                            "receipt_type": receipt.get("receipt_type", "warehouse"),
                        }

                    if line.get("image_url"):
                        conn.execute(
                            "UPDATE item_catalog SET image_url = ? WHERE item_number = ? AND image_url = ''",
                            (str(line["image_url"]), item_num),
                        )

                    cursor = conn.execute(
                        """
                        INSERT INTO receipt_items (
                            transaction_barcode, item_number, raw_description,
                            quantity, unit_price, gross_amount, discount_amount, net_amount, tax_flag, is_weight
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            barcode,
                            item_num,
                            raw_desc,
                            float(line.get("quantity", 1.0)),
                            float(line.get("unit_price", 0.0)),
                            float(line.get("gross_amount", 0.0)),
                            float(line.get("discount_amount", 0.0)),
                            float(line.get("net_amount", 0.0)),
                            str(line.get("tax_flag", "")),
                            1 if line.get("is_weight") else 0,
                        ),
                    )
                    inserted_lines.append(
                        {
                            "id": cursor.lastrowid,
                            "item_number": item_num,
                            "raw_description": raw_desc,
                            "net_amount": float(line.get("net_amount", 0.0)),
                            "tax_flag": str(line.get("tax_flag", "")),
                        }
                    )
                self._save_split(conn, inserted_lines, float(receipt.get("tax", 0.0)))
                conn.execute(
                    "UPDATE receipts SET tax_split_version = ? WHERE transaction_barcode = ?",
                    (SPLIT_RULES_VERSION, barcode),
                )

        return {
            "inserted_receipts": inserted_receipts,
            "updated_receipts": updated_receipts,
            "skipped_receipts": skipped_receipts,
            "new_items": list(new_items_for_catalog.values()),
        }

    def _build_receipt_filter(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        receipt_type: str | None = None,
        store: str | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        if store and store != "all":
            clauses.append(" r.store = ? ")
            params.append(store)
        if start_date:
            clauses.append(" substr(r.transaction_date, 1, 10) >= ? ")
            params.append(start_date[:10])
        if end_date:
            clauses.append(" substr(r.transaction_date, 1, 10) <= ? ")
            params.append(end_date[:10])
        if receipt_type and receipt_type != "all":
            clauses.append(" r.receipt_type = ? ")
            params.append(receipt_type)
        return " AND ".join(clauses), params

    def get_statistics(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        receipt_type: str | None = None,
        store: str | None = None,
    ) -> dict[str, Any]:
        """Computes full spending statistics by category and month for the selected period."""
        where_sql, params = self._build_receipt_filter(start_date, end_date, receipt_type, store)

        with self.connect() as conn:
            summary_row = conn.execute(
                f"""
                SELECT
                    COUNT(DISTINCT r.transaction_barcode) AS receipt_count,
                    COALESCE(SUM(r.subtotal), 0.0) AS total_subtotal,
                    COALESCE(SUM(r.tax), 0.0) AS total_tax,
                    COALESCE(SUM(r.total), 0.0) AS total_spent,
                    COALESCE(SUM(r.instant_savings), 0.0) AS total_savings,
                    MIN(substr(r.transaction_date, 1, 10)) AS min_date,
                    MAX(substr(r.transaction_date, 1, 10)) AS max_date
                FROM receipts r
                WHERE {where_sql}
                """,
                params,
            ).fetchone()

            # A CRV line counts towards the category of the item it was charged for: the deposit is part of
            # what was spent on it. The Items tab shows the same deposit under Tax/CRV and Total instead of
            # Net, so a category's net here is its items' net plus their deposits
            cat_rows = conn.execute(
                f"""
                SELECT
                    c.category AS category,
                    COUNT(CASE WHEN ri.is_deposit = 0 THEN ri.id END) AS line_count,
                    COUNT(DISTINCT CASE WHEN ri.is_deposit = 0 THEN ri.item_number END) AS unique_items,
                    ROUND(COALESCE(SUM(ri.gross_amount), 0.0), 2) AS gross_spent,
                    ROUND(COALESCE(SUM(ri.discount_amount), 0.0), 2) AS discount_saved,
                    ROUND(COALESCE(SUM(ri.net_amount), 0.0), 2) AS net_spent
                FROM receipt_items ri
                JOIN receipts r ON r.transaction_barcode = ri.transaction_barcode
                {_CATALOG_OF_LINE}
                WHERE {where_sql}
                GROUP BY c.category
                ORDER BY net_spent DESC
                """,
                params,
            ).fetchall()

            monthly_rows = conn.execute(
                f"""
                SELECT
                    substr(r.transaction_date, 1, 7) AS year_month,
                    c.category AS category,
                    ROUND(COALESCE(SUM(ri.net_amount), 0.0), 2) AS net_spent
                FROM receipt_items ri
                JOIN receipts r ON r.transaction_barcode = ri.transaction_barcode
                {_CATALOG_OF_LINE}
                WHERE {where_sql}
                GROUP BY year_month, c.category
                ORDER BY year_month ASC, net_spent DESC
                """,
                params,
            ).fetchall()

            type_rows = conn.execute(
                f"""
                SELECT
                    r.receipt_type,
                    COUNT(*) AS count,
                    ROUND(COALESCE(SUM(r.total), 0.0), 2) AS total_spent
                FROM receipts r
                WHERE {where_sql}
                GROUP BY r.receipt_type
                """,
                params,
            ).fetchall()

        total_items_net = sum(float(r["net_spent"]) for r in cat_rows)
        categories_breakdown = []
        for r in cat_rows:
            net_val = float(r["net_spent"])
            pct = round((net_val / total_items_net) * 100, 1) if total_items_net > 0 else 0.0
            categories_breakdown.append(
                {
                    "category": r["category"],
                    "line_count": int(r["line_count"]),
                    "unique_items": int(r["unique_items"]),
                    "gross_spent": float(r["gross_spent"]),
                    "discount_saved": float(r["discount_saved"]),
                    "net_spent": net_val,
                    "percentage": pct,
                }
            )

        receipt_count = int(summary_row["receipt_count"] or 0)
        total_spent = round(float(summary_row["total_spent"] or 0.0), 2)
        avg_receipt = round(total_spent / receipt_count, 2) if receipt_count > 0 else 0.0

        return {
            "summary": {
                "receipt_count": receipt_count,
                "items_net_total": round(total_items_net, 2),
                "total_subtotal": round(float(summary_row["total_subtotal"] or 0.0), 2),
                "total_tax": round(float(summary_row["total_tax"] or 0.0), 2),
                "total_spent": total_spent,
                "total_savings": round(float(summary_row["total_savings"] or 0.0), 2),
                "average_receipt": avg_receipt,
                "min_date": summary_row["min_date"],
                "max_date": summary_row["max_date"],
            },
            "by_category": categories_breakdown,
            "by_month": [dict(r) for r in monthly_rows],
            "by_type": [dict(r) for r in type_rows],
        }

    def get_items_summary(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        receipt_type: str | None = None,
        category: str | None = None,
        search: str | None = None,
        store: str | None = None,
    ) -> list[dict[str, Any]]:
        """Returns an aggregated list of items for the period with their categories and totals."""
        where_sql, params = self._build_receipt_filter(start_date, end_date, receipt_type, store)
        # A CRV line is a deposit, not a product: one folded into its bottle shows in the bottle's Tax/CRV and
        # Total (crv_amount); one whose bottle isn't on the receipt stays a row of its own with its amount under
        # Tax/CRV, not Net, as in the receipt view. The category stats count deposits in their item's category
        where_sql += " AND ri.crv_owner_id IS NULL "
        if category and category != "all":
            where_sql += " AND c.category = ? "
            params.append(category)
        if search and search.strip():
            # LIKE ignores case only for ASCII, so compare casefolded text (as search_purchases does)
            q = f"%{search.strip().casefold()}%"
            where_sql += (
                " AND (casefold(c.item_number) LIKE ? OR casefold(c.raw_description) LIKE ?"
                " OR casefold(c.clean_name) LIKE ?) "
            )
            params.extend([q, q, q])

        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    c.item_number,
                    c.raw_description,
                    c.clean_name,
                    c.category,
                    c.is_manual_override,
                    c.classified_by,
                    c.image_file,
                    MAX(ri.is_weight) AS is_weight,
                    COUNT(ri.id) AS purchase_count,
                    ROUND(COALESCE(SUM(ri.quantity), 0.0), 2) AS total_quantity,
                    ROUND(COALESCE(SUM(CASE WHEN ri.is_deposit = 0 THEN ri.gross_amount END), 0.0), 2) AS gross_spent,
                    ROUND(COALESCE(SUM(CASE WHEN ri.is_deposit = 0 THEN ri.discount_amount END), 0.0), 2)
                        AS discount_saved,
                    ROUND(COALESCE(SUM(CASE WHEN ri.is_deposit = 0 THEN ri.net_amount END), 0.0), 2) AS net_spent,
                    ROUND(SUM(ri.tax_amount), 2) AS tax_amount,
                    ROUND(COALESCE(SUM(ri.crv_amount), 0.0), 2) AS crv_amount,
                    ROUND(COALESCE(SUM(ri.tax_amount), 0.0) + COALESCE(SUM(ri.crv_amount), 0.0), 2) AS tax_crv,
                    MAX(COALESCE(ri.tax_unsplit, 0)) AS tax_partial,
                    MIN(COALESCE(ri.tax_unsplit, 0)) AS tax_unknown,
                    MAX(substr(r.transaction_date, 1, 10)) AS last_purchased
                FROM receipt_items ri
                JOIN receipts r ON r.transaction_barcode = ri.transaction_barcode
                JOIN item_catalog c ON c.item_number = ri.item_number
                WHERE {where_sql}
                GROUP BY c.item_number
                ORDER BY net_spent DESC
                """,
                params,
            ).fetchall()
        result = []
        for r in rows:
            item = add_unit_prices(dict(r), "net_spent", "total_quantity")
            # Some purchase's tax couldn't be split, so the item's total with tax is incomplete; when none of
            # them could be split there is no total with tax at all
            item["tax_partial"] = bool(item["tax_partial"])
            item["tax_unknown"] = bool(item["tax_unknown"])
            result.append(item)
        return result

    def search_purchases(self, query: str, limit: int = 500) -> list[dict[str, Any]]:
        """
        Searches item purchases across all stores and all time by item number, receipt
        description, and decoded name. One row per receipt line, newest first.
        """
        q = f"%{query.strip().casefold()}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    substr(r.transaction_date, 1, 10) AS purchase_date,
                    r.store,
                    r.receipt_type,
                    r.warehouse_name,
                    r.transaction_barcode,
                    ri.item_number,
                    ri.raw_description,
                    c.clean_name,
                    c.category,
                    c.image_file,
                    ri.quantity,
                    ri.is_weight,
                    ri.gross_amount,
                    ri.discount_amount,
                    ri.net_amount
                FROM receipt_items ri
                JOIN receipts r ON r.transaction_barcode = ri.transaction_barcode
                JOIN item_catalog c ON c.item_number = ri.item_number
                WHERE ri.is_deposit = 0
                  AND (casefold(ri.item_number) LIKE ?
                   OR casefold(ri.raw_description) LIKE ?
                   OR casefold(c.clean_name) LIKE ?)
                ORDER BY r.transaction_date DESC, ri.id ASC
                LIMIT ?
                """,
                (q, q, q, limit),
            ).fetchall()
        return [add_unit_prices(dict(r), "net_amount", "quantity") for r in rows]

    def get_receipts_list(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        receipt_type: str | None = None,
        store: str | None = None,
    ) -> list[dict[str, Any]]:
        """Returns receipts for the period with their line items and categories."""
        where_sql, params = self._build_receipt_filter(start_date, end_date, receipt_type, store)
        with self.connect() as conn:
            receipts = conn.execute(
                f"""
                SELECT
                    r.transaction_barcode,
                    r.transaction_date,
                    r.receipt_type,
                    r.store,
                    r.warehouse_name,
                    r.subtotal,
                    r.tax,
                    r.total,
                    r.instant_savings
                FROM receipts r
                WHERE {where_sql}
                ORDER BY r.transaction_date DESC
                """,
                params,
            ).fetchall()

            result = []
            for rec in receipts:
                rec_dict = dict(rec)
                items = conn.execute(
                    """
                    SELECT
                        ri.id,
                        ri.item_number,
                        ri.raw_description,
                        c.clean_name,
                        c.category,
                        c.is_manual_override,
                        c.image_file,
                        ri.quantity,
                        ri.is_weight,
                        ri.unit_price,
                        ri.gross_amount,
                        ri.discount_amount,
                        ri.net_amount,
                        ri.tax_amount,
                        ri.crv_amount,
                        ri.is_deposit,
                        ROUND(COALESCE(ri.tax_amount, 0.0) + ri.crv_amount, 2) AS tax_crv,
                        COALESCE(ri.tax_unsplit, 0) AS tax_unsplit
                    FROM receipt_items ri
                    JOIN item_catalog c ON c.item_number = ri.item_number
                    WHERE ri.transaction_barcode = ? AND ri.crv_owner_id IS NULL
                    ORDER BY ri.id ASC
                    """,
                    (rec["transaction_barcode"],),
                ).fetchall()
                rec_dict["items"] = []
                for row in items:
                    item = add_unit_prices(dict(row), "net_amount", "quantity")
                    item["tax_unsplit"] = bool(item["tax_unsplit"])
                    item["is_deposit"] = bool(item["is_deposit"])
                    rec_dict["items"].append(item)
                result.append(rec_dict)
            return result

    def get_membership_analytics(
        self,
        year: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        gold_fee: float = 65.0,
        exec_fee: float = 130.0,
        reward_rate: float = 2.0,
        reward_cap: float = 1250.0,
        exclude_alcohol: bool = True,
    ) -> dict[str, Any]:
        """
        Compares Costco memberships (Gold Star vs Executive 2% Reward) for a year or the selected period.
        The 2% reward base is net purchase amounts (net_amount, excluding tax and after coupons)
        in the warehouse and online, excluding fuel (gas) and, optionally, the "Alcohol" category.
        """
        current_year = str(datetime.now().year)
        with self.connect() as conn:
            year_rows = conn.execute(
                """
                SELECT DISTINCT substr(transaction_date, 1, 4) AS yr
                FROM receipts
                WHERE length(transaction_date) >= 4 AND store = 'costco'
                ORDER BY yr DESC
                """
            ).fetchall()
            db_years = [r["yr"] for r in year_rows if r["yr"] and r["yr"].isdigit()]

            available_years = list(db_years)
            if current_year not in available_years:
                available_years.insert(0, current_year)
            available_years.sort(reverse=True)

            resolved_mode = year or "auto"
            if resolved_mode == "auto":
                if current_year in db_years:
                    selected_year = current_year
                elif db_years:
                    selected_year = db_years[0]
                else:
                    selected_year = current_year
                where_sql = "substr(r.transaction_date, 1, 4) = ?"
                params: list[Any] = [selected_year]
                period_label = f"{selected_year} год"
            elif resolved_mode == "filter":
                selected_year = "filter"
                where_sql, params = self._build_receipt_filter(start_date, end_date, "all", "costco")
                if start_date and end_date:
                    period_label = f"{start_date[:10]} — {end_date[:10]}"
                elif start_date:
                    period_label = f"с {start_date[:10]}"
                elif end_date:
                    period_label = f"по {end_date[:10]}"
                else:
                    period_label = "Всё время"
            else:
                selected_year = resolved_mode[:4]
                where_sql = "substr(r.transaction_date, 1, 4) = ?"
                params = [selected_year]
                period_label = f"{selected_year} год"

            if resolved_mode != "filter":
                # Costco memberships only apply to Costco purchases
                where_sql += " AND r.store = 'costco'"

            receipt_totals = conn.execute(
                f"""
                SELECT
                    COUNT(DISTINCT r.transaction_barcode) AS receipt_count,
                    COUNT(DISTINCT substr(r.transaction_date, 1, 7)) AS active_months,
                    COALESCE(SUM(r.total), 0.0) AS total_spent_with_tax,
                    COALESCE(SUM(r.subtotal), 0.0) AS total_subtotal,
                    COALESCE(SUM(r.tax), 0.0) AS total_tax,
                    COALESCE(SUM(CASE WHEN r.receipt_type = 'gas' THEN r.total ELSE 0.0 END), 0.0) AS gas_receipts_total,
                    MIN(substr(r.transaction_date, 1, 10)) AS min_date,
                    MAX(substr(r.transaction_date, 1, 10)) AS max_date
                FROM receipts r
                WHERE {where_sql}
                """,
                params,
            ).fetchone()

            items_breakdown = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN r.receipt_type != 'gas' THEN ri.net_amount ELSE 0.0 END), 0.0) AS non_gas_items_net,
                    COALESCE(SUM(CASE WHEN r.receipt_type = 'gas' THEN ri.net_amount ELSE 0.0 END), 0.0) AS gas_items_net,
                    -- A deposit on a bottle stays in the reward base, as before deposits were linked to items
                    COALESCE(SUM(CASE WHEN r.receipt_type != 'gas' AND c.category = ? AND ri.is_deposit = 0
                                      THEN ri.net_amount ELSE 0.0 END), 0.0) AS alcohol_items_net
                FROM receipt_items ri
                JOIN receipts r ON r.transaction_barcode = ri.transaction_barcode
                {_CATALOG_OF_LINE}
                WHERE {where_sql}
                """,
                [ALCOHOL_CATEGORY, *params],
            ).fetchone()

        receipt_count = int(receipt_totals["receipt_count"] or 0)
        active_months = int(receipt_totals["active_months"] or 0)
        total_spent_with_tax = round(float(receipt_totals["total_spent_with_tax"] or 0.0), 2)
        excluded_tax = round(float(receipt_totals["total_tax"] or 0.0), 2)

        non_gas_items_net = round(float(items_breakdown["non_gas_items_net"] or 0.0), 2)
        gas_items_net = round(float(items_breakdown["gas_items_net"] or 0.0), 2)
        excluded_gas_spent = round(max(float(receipt_totals["gas_receipts_total"] or 0.0), gas_items_net), 2)
        alcohol_items_net = round(float(items_breakdown["alcohol_items_net"] or 0.0), 2)

        excluded_alcohol_spent = alcohol_items_net if exclude_alcohol else 0.0
        eligible_spend = round(max(non_gas_items_net - excluded_alcohol_spent, 0.0), 2)

        rate_fraction = max(reward_rate, 0.0) / 100.0
        upgrade_cost = round(max(exec_fee - gold_fee, 0.0), 2)
        breakeven_upgrade_spend = round(upgrade_cost / rate_fraction, 2) if rate_fraction > 0 else 0.0
        breakeven_free_exec_spend = round(exec_fee / rate_fraction, 2) if rate_fraction > 0 else 0.0

        estimated_reward = round(min(eligible_spend * rate_fraction, reward_cap), 2)
        gold_net_balance = round(-gold_fee, 2)
        exec_net_balance = round(-exec_fee + estimated_reward, 2)
        exec_advantage_vs_gold = round(estimated_reward - upgrade_cost, 2)

        spend_needed_for_breakeven = round(max(breakeven_upgrade_spend - eligible_spend, 0.0), 2)
        breakeven_progress_pct = (
            round(min((eligible_spend / breakeven_upgrade_spend) * 100.0, 100.0), 1)
            if breakeven_upgrade_spend > 0
            else 100.0
        )
        recommended_card = "executive" if exec_advantage_vs_gold >= 0 else "gold_star"

        months_divisor = max(active_months, 1)
        monthly_avg_eligible = round(eligible_spend / months_divisor, 2) if eligible_spend > 0 else 0.0
        projected_annual_eligible = round(monthly_avg_eligible * 12.0, 2)
        projected_annual_reward = round(min(projected_annual_eligible * rate_fraction, reward_cap), 2)
        projected_exec_advantage = round(projected_annual_reward - upgrade_cost, 2)
        projected_recommended_card = "executive" if projected_exec_advantage >= 0 else "gold_star"

        return {
            "selected_year": selected_year,
            "period_label": period_label,
            "available_years": available_years,
            "parameters": {
                "gold_fee": round(gold_fee, 2),
                "exec_fee": round(exec_fee, 2),
                "upgrade_cost": upgrade_cost,
                "reward_rate": round(reward_rate, 2),
                "reward_cap": round(reward_cap, 2),
                "exclude_alcohol": exclude_alcohol,
                "breakeven_upgrade_spend": breakeven_upgrade_spend,
                "breakeven_free_exec_spend": breakeven_free_exec_spend,
            },
            "spend_breakdown": {
                "receipt_count": receipt_count,
                "active_months": active_months,
                "min_date": receipt_totals["min_date"],
                "max_date": receipt_totals["max_date"],
                "total_spent_with_tax": total_spent_with_tax,
                "excluded_gas_spent": excluded_gas_spent,
                "excluded_tax": excluded_tax,
                "alcohol_items_net": alcohol_items_net,
                "excluded_alcohol_spent": excluded_alcohol_spent,
                "eligible_spend": eligible_spend,
            },
            "comparison": {
                "estimated_reward": estimated_reward,
                "gold_net_balance": gold_net_balance,
                "exec_net_balance": exec_net_balance,
                "exec_advantage_vs_gold": exec_advantage_vs_gold,
                "spend_needed_for_breakeven": spend_needed_for_breakeven,
                "breakeven_progress_pct": breakeven_progress_pct,
                "recommended_card": recommended_card,
            },
            "projection": {
                "monthly_avg_eligible": monthly_avg_eligible,
                "projected_annual_eligible": projected_annual_eligible,
                "projected_annual_reward": projected_annual_reward,
                "projected_exec_advantage": projected_exec_advantage,
                "projected_recommended_card": projected_recommended_card,
            },
        }
