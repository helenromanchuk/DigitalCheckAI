"""Category boundaries and upgrades from the former 15-category catalog."""
import pytest

from app.categorizer.gemini_classifier import fallback_categorize_item
from app.config import DEFAULT_CATEGORIES, DEFAULT_CATEGORY_LABELS_RU, LEGACY_CATEGORY_ALIASES
from app.database import CATEGORY_SCHEMA_VERSION, Database
from app.scraper.parser import parse_any_costco_payload


@pytest.mark.parametrize(("description", "expected"), [
    ("USDA CHOICE RIBEYE", "Meat, poultry & seafood"),
    ("ORG STRAWBERRIES", "Fruits & vegetables"),
    ("KS ORG EGGS 24CT", "Dairy & eggs"),
    ("CROISSANTS 12CT", "Bread & bakery"),
    ("ROTISSERIE CHKN", "Prepared meals & deli"),
    ("FROZEN PIZZA", "Frozen foods"),
    ("KS ORG EVOO", "Pantry & cooking ingredients"),
    ("KS MIXED NUTS 40OZ", "Snacks & sweets"),
    ("WHOLE BEAN COFFEE", "Beverages, coffee & tea"),
    ("KIRKLAND PROSECCO", "Alcohol"),
    ("BOUNTY PAPER TOWEL 12CT", "Household supplies & cleaning"),
    ("SUNSCREEN CREAM SPF50", "Health & personal care"),
    ("PYREX GLASS STORAGE", "Home, kitchen & garden"),
    ("LEVIS MENS JEANS", "Clothing, shoes & accessories"),
    ("BLENDER", "Electronics & appliances"),
    ("UNLEADED GASOLINE 87", "Auto & fuel"),
    ("LEGOSPEED ASSORTMENT P=340", "Toys & games"),
    ("CPN GROCERY COPIENT", "Other"),
    ("FROZEN BLUEBERRIES", "Frozen foods"),
    ("FRZN SALMON", "Frozen foods"),
    ("FROZEN CROISSANTS", "Frozen foods"),
    ("ICE CREAM", "Frozen foods"),
    ("FREEZE-DRIED STRAWBERRIES", "Snacks & sweets"),
    ("MILK CHOCOLATE", "Snacks & sweets"),
    ("CHOCOLATE MILK", "Dairy & eggs"),
    ("TORTILLA CHIPS", "Snacks & sweets"),
    ("KS ORGANIC TORTILLA-CHIPS", "Snacks & sweets"),
    ("TORTILLAS", "Bread & bakery"),
    ("DARK CHOCOLATE", "Snacks & sweets"),
    ("CHOCOLATE CHIPS", "Snacks & sweets"),
    ("CHOCOLATE COVERED COFFEE BEANS", "Snacks & sweets"),
    ("CHOCOLATE-COVERED COFFEE BEANS", "Snacks & sweets"),
    ("DARK CHOCOLATE COATED COFFEE BEANS", "Snacks & sweets"),
    ("CHOCOLATE DIPPED COFFEE BEANS", "Snacks & sweets"),
    ("WINE FILLED CHOCOLATE", "Snacks & sweets"),
    ("WINE-FILLED CHOCOLATES", "Snacks & sweets"),
    ("COFFEE FILLED CHOCOLATES", "Snacks & sweets"),
    ("CHOCOLATE COFFEE TRUFFLES", "Snacks & sweets"),
    ("CHOCOLATE WINE TRUFFLES", "Snacks & sweets"),
    ("CHOCOLATE COFFEE COOKIES", "Snacks & sweets"),
    ("COFFEE FLAVORED CHOCOLATE", "Snacks & sweets"),
    ("CHOCOLATE WINE", "Alcohol"),
    ("CHOCOLATE BEER", "Alcohol"),
    ("DARK CHOCOLATE COFFEE", "Beverages, coffee & tea"),
    ("CHOCOLATE FLAVORED COFFEE", "Beverages, coffee & tea"),
    ("CHOCOLATE FLAVOURED COFFEE", "Beverages, coffee & tea"),
    ("DARK CHOCOLATE TEA", "Beverages, coffee & tea"),
    ("CHOCOLATE COFFEE MAKER", "Electronics & appliances"),
    ("CHOCOLATE WINE VINEGAR", "Pantry & cooking ingredients"),
    ("PEANUT BUTTER", "Pantry & cooking ingredients"),
    ("ALMOND MILK", "Beverages, coffee & tea"),
    ("OAT MILK", "Beverages, coffee & tea"),
    ("KEFIR MILK DRINK", "Dairy & eggs"),
    ("MERLOT WINE", "Alcohol"),
    ("WINE VINEGAR", "Pantry & cooking ingredients"),
    ("ROTISSERIE CHICKEN", "Prepared meals & deli"),
    ("FRESH RAVIOLI", "Prepared meals & deli"),
    ("SALMON AVOCADO SUSHI ROLL", "Prepared meals & deli"),
    ("WASABI AHI TUNA POKE", "Prepared meals & deli"),
    ("PARMESAN MEATBALLS IN SAUCE", "Prepared meals & deli"),
    ("SUSHI RICE", "Pantry & cooking ingredients"),
    ("FROZEN RAVIOLI", "Frozen foods"),
    ("DELI SLICED TURKEY", "Meat, poultry & seafood"),
    ("CHILLED PIZZA", "Prepared meals & deli"),
    ("COFFEE MAKER", "Electronics & appliances"),
    ("ICE CREAM MAKER", "Electronics & appliances"),
    ("REUSABLE WATER BOTTLE", "Home, kitchen & garden"),
    ("ZIPLOC FOOD STORAGE BAGS", "Household supplies & cleaning"),
    ("ALUMINUM FOIL", "Household supplies & cleaning"),
    ("REUSABLE FOOD CONTAINERS", "Home, kitchen & garden"),
    ("SYNTHETIC MOTOR OIL", "Auto & fuel"),
    ("FROZEN LEGO CASTLE", "Toys & games"),
    ("FROZEN DISNEY SHIRT", "Clothing, shoes & accessories"),
])
def test_offline_category_boundaries(description, expected):
    assert fallback_categorize_item("123", description)["category"] == expected


def test_eighteen_defaults_have_unique_translations():
    assert DEFAULT_CATEGORIES == [
        "Meat, poultry & seafood", "Fruits & vegetables", "Dairy & eggs", "Bread & bakery",
        "Prepared meals & deli", "Frozen foods", "Pantry & cooking ingredients", "Snacks & sweets",
        "Beverages, coffee & tea", "Alcohol", "Household supplies & cleaning", "Health & personal care",
        "Home, kitchen & garden", "Clothing, shoes & accessories", "Electronics & appliances", "Auto & fuel",
        "Toys & games", "Other",
    ]
    assert list(DEFAULT_CATEGORY_LABELS_RU) == DEFAULT_CATEGORIES
    assert len(set(DEFAULT_CATEGORY_LABELS_RU.values())) == 18


def test_upgrade_preserves_fallback_snacks_and_chocolate_beverages(tmp_path):
    path = tmp_path / "fallback_categories.db"
    db = Database(path)
    cases = [
        ("TORTILLA CHIPS", "Snacks, sweets, nuts & drinks", "Snacks & sweets"),
        ("CHOCOLATE WINE", "Alcohol", "Alcohol"),
        ("DARK CHOCOLATE COFFEE", "Snacks, sweets, nuts & drinks", "Beverages, coffee & tea"),
        ("CHOCOLATE COVERED COFFEE BEANS", "Snacks & sweets", "Snacks & sweets"),
        ("WINE FILLED CHOCOLATE", "Snacks, sweets, nuts & drinks", "Snacks & sweets"),
        ("WINE-FILLED CHOCOLATES", "Snacks & sweets", "Snacks & sweets"),
    ]
    db.save_categorized_items([
        {"item_number": str(i), "raw_description": desc, "clean_name": desc.title(), "category": old}
        for i, (desc, old, _) in enumerate(cases)
    ], classified_by="fallback")
    with db.connect() as conn:
        conn.execute("PRAGMA user_version = 3")

    for _ in range(2):
        db = Database(path)
        catalog = db.get_existing_catalog_map([str(i) for i in range(len(cases))])
        for i, (desc, _, expected) in enumerate(cases):
            row = catalog[str(i)]
            assert row["category"] == expected
            assert row["clean_name"] == desc.title()
            assert row["classified_by"] == "fallback"


def test_taxonomy_upgrade_preserves_custom_edits_and_receipt_totals(tmp_path):
    path = tmp_path / "old_categories.db"
    db = Database(path)
    # Old categories are deliberately retained here to exercise real upgrade inputs.
    cases = [
        ("CROISSANTS", "Bakery, bread & prepared food (Deli)", "Bread & bakery", False),
        ("ROTISSERIE CHKN", "Bakery, bread & prepared food (Deli)", "Prepared meals & deli", False),
        ("COFFEE K-CUP PODS", "Snacks, sweets, nuts & drinks", "Beverages, coffee & tea", False),
        ("MIXED NUTS", "Снеки, сладости, орехи и напитки", "Snacks & sweets", False),
        ("FROZEN SALMON", "Frozen & ready meals", "Frozen foods", False),
        ("MILK", "Молочные продукты, сыр и яйца", "Dairy & eggs", True),
        ("LEGO", "Other", "Toys & games", False),
        ("LEGO", "Other", "Other", True),
        ("LEGO", "Kids", "Kids", True),
        ("PET FOOD", "Pets", "Pets", False),
        ("COFFEE", "Pantry, oils, spices & canned goods", "Beverages, coffee & tea", False),
        ("COFFEE", "Pantry, oils, spices & canned goods", "Pantry & cooking ingredients", True),
        ("BLENDER", "Home, kitchen & garden", "Electronics & appliances", False),
        ("MYSTERY PRODUCT", "Bakery, bread & prepared food (Deli)", "Bread & bakery", True),
        ("GASOLINE", "Auto & fuel (Gas Station)", "Auto & fuel", False),
    ]
    db.add_custom_category("Kids", "Детские товары")
    db.add_custom_category("Pets", "Питомцы")
    db.upsert_receipts(parse_any_costco_payload({
        "transactionBarcode": "CATEGORY-UPGRADE", "transactionDate": "2026-10-01",
        "total": 150, "itemArray": [
            {"itemNumber": str(i), "itemDescription01": desc, "unit": 1, "amount": 10}
            for i, (desc, _, _, _) in enumerate(cases)
        ],
    }))
    with db.connect() as conn:
        for i, (desc, old, _, manual) in enumerate(cases):
            conn.execute(
                "UPDATE item_catalog SET category = ?, clean_name = ?, is_manual_override = ?, "
                "classified_by = ?, updated_at = 'original' WHERE item_number = ?",
                (old, desc, manual, "manual" if manual else "gemini", str(i)),
            )
        conn.execute("INSERT INTO custom_categories VALUES (?, 'original', ?)", (
            "Snacks, sweets, nuts & drinks", "Снеки, сладости, орехи и напитки",
        ))
        conn.execute("PRAGMA user_version = 3")
        receipts_before = [tuple(row) for row in conn.execute("SELECT * FROM receipts")]
        lines_before = [tuple(row) for row in conn.execute("SELECT * FROM receipt_items")]

    for _ in range(2):
        db = Database(path)
        catalog = db.get_existing_catalog_map([str(i) for i in range(len(cases))])
        for i, (desc, _, expected, manual) in enumerate(cases):
            row = catalog[str(i)]
            assert (row["category"], row["clean_name"], row["is_manual_override"], row["updated_at"]) == (
                expected, desc, manual, "original",
            )
            assert row["classified_by"] == ("manual" if manual else "gemini")
        assert db.get_all_categories() == [*DEFAULT_CATEGORIES, "Kids", "Pets"]
        assert db.get_category_labels_ru()["Pets"] == "Питомцы"
        assert db.get_statistics()["summary"]["items_net_total"] == 150
        with db.connect() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CATEGORY_SCHEMA_VERSION
            assert [tuple(row) for row in conn.execute("SELECT * FROM receipts")] == receipts_before
            assert [tuple(row) for row in conn.execute("SELECT * FROM receipt_items")] == lines_before


@pytest.mark.parametrize(("old", "expected"), LEGACY_CATEGORY_ALIASES.items())
def test_all_old_names_are_migrated(tmp_path, old, expected):
    path = tmp_path / "aliases.db"
    db = Database(path)
    db.save_categorized_items([{"item_number": "1", "clean_name": "Unknown product", "category": old}])
    with db.connect() as conn:
        conn.execute("PRAGMA user_version = 3")
    assert Database(path).get_existing_catalog_map(["1"])["1"]["category"] == expected


def test_upgrade_keeps_legacy_custom_spelling(tmp_path):
    path = tmp_path / "custom_spelling.db"
    db = Database(path)
    with db.connect() as conn:
        conn.execute("INSERT INTO custom_categories VALUES ('Handpicked ', 'original', 'Избранное')")
        conn.execute(
            "INSERT INTO item_catalog (item_number, raw_description, clean_name, category, updated_at) "
            "VALUES ('1', 'LEGO', 'LEGO', 'Handpicked ', 'original')"
        )
        conn.execute("PRAGMA user_version = 3")
    db = Database(path)
    assert db.get_existing_catalog_map(["1"])["1"]["category"] == "Handpicked "
    assert db.get_category_labels_ru()["Handpicked "] == "Избранное"
    assert "Handpicked" not in db.get_all_categories()
