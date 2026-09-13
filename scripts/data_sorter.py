import json
import sqlite3
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.paths import DB_FILE as DATABASE_PATH
from app.services.item_catalog import sync_item_categories

ITEMS_FILE = os.path.join(BASE_DIR, "items.json")
LOCALIZATION_FILE = os.path.join(BASE_DIR, "localization.json")
DB_FILE = str(DATABASE_PATH)

# ── 1. Lokalizáció betöltése ─────────────────────────────────────────────────
print("Localization betöltése...")
with open(LOCALIZATION_FILE, encoding="utf-8") as f:
    loc_data = json.load(f)

loc_map = {}
for entry in loc_data["tmx"]["body"]["tu"]:
    tuid = entry.get("@tuid", "")
    tuv  = entry.get("tuv", [])
    if isinstance(tuv, dict):
        if tuv.get("@xml:lang") == "EN-US":
            loc_map[tuid] = tuv.get("seg", "")
    elif isinstance(tuv, list):
        for lang_entry in tuv:
            if lang_entry.get("@xml:lang") == "EN-US":
                loc_map[tuid] = lang_entry.get("seg", "")
                break

print(f"  {len(loc_map)} lokalizációs bejegyzés betöltve.")


def get_en_name(uniquename):
    # Enchanted item neve = alap item neve: T4_BOW@2 → @ITEMS_T4_BOW
    base = uniquename.split("@")[0]
    return loc_map.get(f"@ITEMS_{base}") or loc_map.get(f"@ITEMS_{uniquename}") or ""


# ── 2. Items betöltése ───────────────────────────────────────────────────────
print("Items.json betöltése...")
with open(ITEMS_FILE, encoding="utf-8") as f:
    items_data = json.load(f)

raw_items = items_data["items"]

# ── 3. SQLite létrehozása ────────────────────────────────────────────────────
os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

conn = sqlite3.connect(DB_FILE)
cur  = conn.cursor()

cur.executescript("""
    CREATE TABLE items (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        uniquename      TEXT NOT NULL UNIQUE,
        name_en         TEXT,
        item_type       TEXT,
        tier            INTEGER,
        enchantment     INTEGER DEFAULT 0,
        shopcategory    TEXT,
        shopsubcategory TEXT,
        shopsubcategory2 TEXT,
        shopsubcategory3 TEXT,
        slottype        TEXT,
        weight          REAL,
        maxstacksize    INTEGER,
        itemvalue       INTEGER,
        uisprite        TEXT,
        craftable       INTEGER DEFAULT 0
    );
    CREATE INDEX idx_items_uniquename ON items(uniquename);
    CREATE INDEX idx_items_type       ON items(item_type);
    CREATE INDEX idx_items_tier       ON items(tier);
    CREATE INDEX idx_items_category   ON items(shopcategory);
    CREATE INDEX idx_items_category_path
        ON items(shopcategory, shopsubcategory, shopsubcategory2, shopsubcategory3);

    CREATE TABLE item_categories (
        path TEXT PRIMARY KEY,
        category_id TEXT NOT NULL,
        level INTEGER NOT NULL,
        parent_path TEXT REFERENCES item_categories(path),
        sort_order INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_item_categories_parent
        ON item_categories(parent_path, sort_order);

    CREATE TABLE market_prices (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id             INTEGER NOT NULL REFERENCES items(id),
        item_uniquename     TEXT NOT NULL,
        city                TEXT NOT NULL,
        quality             INTEGER NOT NULL,
        enchantment         INTEGER NOT NULL DEFAULT 0,
        sell_price_min      INTEGER,
        sell_price_min_date TEXT,
        sell_price_max      INTEGER,
        sell_price_max_date TEXT,
        buy_price_min       INTEGER,
        buy_price_min_date  TEXT,
        buy_price_max       INTEGER,
        buy_price_max_date  TEXT,
        fetched_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (item_id, city, quality, enchantment)
    );
    CREATE INDEX idx_market_item ON market_prices(item_id);
    CREATE INDEX idx_market_lookup
        ON market_prices(item_uniquename, enchantment, quality, city);
    CREATE INDEX idx_market_sell ON market_prices(sell_price_min);
    CREATE INDEX idx_market_buy ON market_prices(buy_price_max);

    CREATE VIEW best_purchase_prices AS
    SELECT mp.*, i.name_en, i.tier, i.shopcategory
    FROM market_prices mp
    JOIN items i ON i.id = mp.item_id
    WHERE mp.sell_price_min IS NOT NULL AND mp.sell_price_min > 0
      AND NOT EXISTS (
          SELECT 1 FROM market_prices cheaper
          WHERE cheaper.item_id = mp.item_id
            AND cheaper.quality = mp.quality
            AND cheaper.enchantment = mp.enchantment
            AND cheaper.sell_price_min IS NOT NULL
            AND cheaper.sell_price_min > 0
            AND cheaper.sell_price_min < mp.sell_price_min
      );

    CREATE VIEW best_sale_prices AS
    SELECT mp.*, i.name_en, i.tier, i.shopcategory
    FROM market_prices mp
    JOIN items i ON i.id = mp.item_id
    WHERE mp.buy_price_max IS NOT NULL AND mp.buy_price_max > 0
      AND NOT EXISTS (
          SELECT 1 FROM market_prices higher
          WHERE higher.item_id = mp.item_id
            AND higher.quality = mp.quality
            AND higher.enchantment = mp.enchantment
            AND higher.buy_price_max IS NOT NULL
            AND higher.buy_price_max > mp.buy_price_max
      );
""")
print("Adatbázis létrehozva.\n")


# ── 4. Segédfüggvények ───────────────────────────────────────────────────────
def safe_int(v, default=None):
    try:    return int(v)
    except: return default

def safe_float(v, default=None):
    try:    return float(v)
    except: return default


def insert_row(uniquename, item_type, enchantment, merged):
    """Egy sort ír az adatbázisba a merged attribútumokból."""
    cur.execute("""
        INSERT OR IGNORE INTO items (
            uniquename, name_en, item_type, tier, enchantment,
            shopcategory, shopsubcategory, shopsubcategory2, shopsubcategory3,
            slottype, weight, maxstacksize, itemvalue, uisprite, craftable
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        uniquename,
        get_en_name(uniquename) or None,
        item_type,
        safe_int(merged.get("@tier")),
        enchantment,
        merged.get("@shopcategory"),
        merged.get("@shopsubcategory1"),
        merged.get("@shopsubcategory2"),
        merged.get("@shopsubcategory3"),
        merged.get("@slottype"),
        safe_float(merged.get("@weight")),
        safe_int(merged.get("@maxstacksize")),
        safe_int(merged.get("@itemvalue")),
        merged.get("@uisprite"),
        1 if merged.get("@unlockedtocraft") == "true" else 0,
    ))
    return cur.rowcount  # 1 ha sikeres, 0 ha IGNORE


def process_item(item, item_type):
    """
    Egy alap itemet (és enchanted verzióit) feldolgozza.
    Az enchanted verziók uniquename-je: {parent}@{enchantmentlevel}
    """
    uniquename = item.get("@uniquename", "")
    if not uniquename:
        return 0

    count = insert_row(uniquename, item_type, enchantment=0, merged=item)

    # Enchantment szintek keresése
    ench_block = item.get("enchantments", {})
    if not ench_block:
        return count

    raw = ench_block.get("enchantment", [])
    if isinstance(raw, dict):
        raw = [raw]

    for ench in raw:
        if not isinstance(ench, dict):
            continue
        level = safe_int(ench.get("@enchantmentlevel"))
        if level is None:
            continue

        # Enchanted uniquename generálása: T4_2H_BOW + @1 = T4_2H_BOW@1
        ench_uniquename = f"{uniquename}@{level}"

        # Az enchanted item örökli a szülő attribútumait, saját értékei felülírják
        merged = {**item, **ench}

        count += insert_row(ench_uniquename, item_type, enchantment=level, merged=merged)

    return count


def walk_and_process(node, item_type):
    """
    Dinamikusan bejárja a node-ot.
    Ha dict-ben van @uniquename → process_item.
    Ha nincs → mélyebbre megy (köztes struktúra).
    Lista esetén minden elemre rekurzív.
    """
    if isinstance(node, list):
        return sum(walk_and_process(el, item_type) for el in node)

    if isinstance(node, dict):
        if "@uniquename" in node:
            return process_item(node, item_type)
        # Köztes node — sub-key-ek bejárása (attribútumok kihagyásával)
        total = 0
        for key, val in node.items():
            if not key.startswith("@") and isinstance(val, (dict, list)):
                total += walk_and_process(val, item_type)
        return total

    return 0


# ── 5. Bejárás ───────────────────────────────────────────────────────────────
SKIP_KEYS = {"@xmlns:xsi", "@xsi:noNamespaceSchemaLocation", "shopcategories"}
category_stats = {}

for top_key, top_val in raw_items.items():
    if top_key in SKIP_KEYS or top_key.startswith("@"):
        continue
    count = walk_and_process(top_val, item_type=top_key)
    category_stats[top_key] = count
    print(f"  {top_key:<35} {count:>5} db")

conn.commit()
category_count, categorized_items = sync_item_categories(conn, items_data)
print(f"Kategoriafa: {category_count} csomopont, {categorized_items} item frissitve.")

# ── 6. Összesítő ────────────────────────────────────────────────────────────
total = sum(category_stats.values())
print(f"\nKesz! Osszesen: {total} item\n")

cur.execute("SELECT COUNT(*) FROM items WHERE name_en IS NOT NULL AND name_en != ''")
named = cur.fetchone()[0]
print(f"Angol nevvel: {named} / {total}\n")

print("Tier bontas:")
cur.execute("SELECT tier, COUNT(*) FROM items WHERE tier IS NOT NULL GROUP BY tier ORDER BY tier")
for row in cur.fetchall():
    print(f"   T{row[0]}: {row[1]} db")

print("\nEnchantment bontas:")
cur.execute("SELECT enchantment, COUNT(*) FROM items GROUP BY enchantment ORDER BY enchantment")
for row in cur.fetchall():
    label = f"+{row[0]}" if row[0] else "alap"
    print(f"   {label}: {row[1]} db")

conn.close()
print(f"\nMentve: {DB_FILE}")
