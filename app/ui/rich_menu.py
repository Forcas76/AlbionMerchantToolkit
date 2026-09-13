from __future__ import annotations

import os
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from app.services.market_api import (
    CITIES,
    DB_FILE,
    do_fetch,
    fetch_prices_for_chunk,
    save_prices,
)
from app.core.database import connect_database
from app.domain.game_rules import market_fee_policy


console = Console()
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


@dataclass
class CraftSettings:
    premium: bool = False
    material_source: str = "buy"
    crafting_price: int = 0


def load_craft_settings() -> CraftSettings:
    if not os.path.exists(SETTINGS_FILE):
        return CraftSettings()
    with open(SETTINGS_FILE, encoding="utf-8") as settings_file:
        data = json.load(settings_file)
    source = data.get("material_source", "buy")
    if source not in {"buy", "farm"}:
        source = "buy"
    return CraftSettings(
        premium=bool(data.get("premium", False)),
        material_source=source,
        crafting_price=max(0, int(data.get("crafting_price", 0))),
    )


def save_craft_settings(settings: CraftSettings) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as settings_file:
        json.dump(asdict(settings), settings_file, indent=2)


def craft_settings_menu() -> None:
    settings = load_craft_settings()
    while True:
        source_label = "saját farmolás" if settings.material_source == "farm" else "vásárlás"
        console.print("\n[bold cyan]Craft beállítások[/bold cyan]")
        console.print(f"  Prémium: {'igen' if settings.premium else 'nem'}")
        console.print(f"  Alapanyagforrás: {source_label}")
        console.print(f"  Crafting állomásdíj / craft: {settings.crafting_price:,} silver")
        console.print("  [bold]1[/bold] - Prémium beállítása")
        console.print("  [bold]2[/bold] - Alapanyagforrás beállítása")
        console.print("  [bold]3[/bold] - Crafting állomásdíj beállítása")
        console.print("  [bold]0[/bold] - Vissza")
        choice = Prompt.ask("Válassz", choices=["1", "2", "3", "0"], default="0")
        if choice == "0":
            save_craft_settings(settings)
            return
        if choice == "1":
            settings.premium = Confirm.ask("Van prémiumod?", default=settings.premium)
        elif choice == "2":
            source = Prompt.ask(
                "Alapanyagforrás",
                choices=["buy", "farm"],
                default=settings.material_source,
            )
            settings.material_source = source
        else:
            settings.crafting_price = IntPrompt.ask(
                "Crafting állomásdíj egy craft műveletre",
                default=settings.crafting_price,
            )
        save_craft_settings(settings)


def craft_test_menu() -> None:
    settings = load_craft_settings()
    console.print("\n[bold cyan]Craft teszt[/bold cyan]")
    console.print(
        f"Beállítások: prémium={'igen' if settings.premium else 'nem'}, "
        f"alapanyag={'saját farmolás' if settings.material_source == 'farm' else 'vásárlás'}"
    )
    item_name = Prompt.ask("Craftolt tárgy neve vagy részlete").strip()
    if not item_name:
        console.print("[yellow]Az item neve nem lehet üres.[/yellow]")
        return
    tier = IntPrompt.ask("Tier", default=4)
    if tier < 1 or tier > 8:
        console.print("[yellow]A tier értéke 1 és 8 között lehet.[/yellow]")
        return
    with open_db() as conn:
        recipe_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recipes'"
        ).fetchone()
    if recipe_table is None:
        console.print(
            "[yellow]A craft receptadatok még nincsenek az adatbázisban, ezért "
            "a tényleges költség- és profitkalkuláció még nem számítható.[/yellow]"
        )
        console.print(
            "A következő lépés a receptek, alapanyagmennyiségek és farmolási "
            "értékek importálása lesz."
        )
        return
    with open_db() as conn:
        candidates = conn.execute(
            """SELECT r.id, r.item_uniquename, i.name_en, i.tier,
                      r.silver_cost, r.craft_time_seconds,
                      r.variant_index, r.output_amount
               FROM recipes r
               JOIN items i ON i.id = r.item_id
               WHERE (i.name_en LIKE ? COLLATE NOCASE
                   OR i.uniquename LIKE ? COLLATE NOCASE)
                 AND i.tier = ?
               ORDER BY i.name_en, i.uniquename
               LIMIT 100""",
            (f"%{item_name}%", f"%{item_name}%", tier),
        ).fetchall()
        if not candidates:
            console.print("[yellow]Nincs recepttalálat ehhez a névhez és tierhez.[/yellow]")
            return

        choice_table = Table(title=f"Craft recepttalálatok: {item_name} | T{tier}")
        for column in ("Szám", "Név", "Uniquename", "Variáns", "Output", "Silver", "Idő"):
            choice_table.add_column(column)
        choices: dict[str, tuple[int, str, str, int, int]] = {}
        for number, row in enumerate(candidates, 1):
            key = str(number)
            choices[key] = (row[0], row[1], row[2] or row[1], row[4], row[7])
            choice_table.add_row(
                key, row[2] or "-", row[1], f"V{row[6] + 1}", str(row[7]),
                f"{row[4]:,}", f"{row[5]} mp",
            )
        console.print(choice_table)
        selected = Prompt.ask(
            "Válassz receptet számmal", choices=list(choices),
        )
        recipe_id, output_id, output_name, silver_cost, output_amount = choices[selected]
        materials = conn.execute(
            """SELECT rm.material_uniquename, rm.amount, mi.name_en
               FROM recipe_materials rm
               LEFT JOIN items mi ON mi.id = rm.material_item_id
               WHERE rm.recipe_id = ?
               ORDER BY rm.material_uniquename""",
            (recipe_id,),
        ).fetchall()
        if not materials:
            console.print("[yellow]Ehhez a recepthez nincs alapanyagadat.[/yellow]")
            return

        material_rows = []
        buy_material_total: int | None = 0
        missing_prices = []
        for material_id, amount, material_name in materials:
            price = conn.execute(
                """SELECT mp.city, mp.sell_price_min
                   FROM market_prices mp
                   WHERE mp.item_uniquename = ?
                     AND mp.sell_price_min > 0
                   ORDER BY mp.sell_price_min ASC
                   LIMIT 1""",
                (material_id,),
            ).fetchone()
            unit_price = price[1] if price else None
            buy_cost = (unit_price * amount) if unit_price is not None else None
            if unit_price is None:
                missing_prices.append(material_id)
            if buy_cost is None:
                buy_material_total = None
            elif buy_material_total is not None:
                buy_material_total += buy_cost
            material_rows.append(
                (material_id, material_name or material_id, amount,
                 price[0] if price else "-", unit_price, buy_cost)
            )

        sale = conn.execute(
            """SELECT city, buy_price_max
               FROM market_prices
               WHERE item_uniquename = ?
                 AND buy_price_max > 0
               ORDER BY buy_price_max DESC
               LIMIT 1""",
            (output_id,),
        ).fetchone()

    material_table = Table(title=f"Alapanyagköltség: {output_name}")
    for column in ("Alapanyag", "ID", "Db", "Legolcsóbb város",
                   "Egységár", "Összesen"):
        material_table.add_column(column)
    for row in material_rows:
        material_table.add_row(
            row[1], row[0], str(row[2]), row[3],
            f"{row[4]:,}" if row[4] is not None else "-",
            f"{row[5]:,}" if row[5] is not None else "N/A",
        )
    console.print(material_table)
    if missing_prices and settings.material_source == "buy":
        console.print(
            "[yellow]Nincs minden alapanyaghoz piaci adat, ezért a vásárlási "
            "nézethez nem adható hiteles profit. A farmolási nézetet megadom; "
            f"hiányzó: {', '.join(missing_prices)}[/yellow]"
        )
    if settings.material_source == "farm":
        console.print(
            "[cyan]Saját farmolás: pénzkiadásként 0 ezüsttel számolva. "
            "A farmolási idő/opportunity cost nincs még beárazva.[/cyan]"
        )
    station_cost = settings.crafting_price
    farm_total_cost = silver_cost + station_cost
    buy_total_cost = (
        silver_cost + station_cost + buy_material_total
        if buy_material_total is not None
        else None
    )
    unit_sale_price = sale[1] if sale else None
    revenue = unit_sale_price * output_amount if unit_sale_price is not None else None
    market_tax = (
        market_fee_policy(settings.premium).transaction_tax(revenue)
        if revenue is not None
        else None
    )
    net_revenue = revenue - market_tax if revenue is not None and market_tax is not None else None
    farm_profit = net_revenue - farm_total_cost if net_revenue is not None else None
    buy_profit = (
        net_revenue - buy_total_cost
        if net_revenue is not None and buy_total_cost is not None
        else None
    )
    selected_cost = farm_total_cost if settings.material_source == "farm" else buy_total_cost
    selected_profit = farm_profit if settings.material_source == "farm" else buy_profit
    summary = Table(title=f"Craft eredmény: {output_name}")
    summary.add_column("Mutató")
    summary.add_column("Érték")
    summary.add_row("Craft silver", f"{silver_cost:,}")
    summary.add_row("Crafting állomásdíj", f"{station_cost:,}")
    summary.add_row(
        "Vásárolt alapanyag költsége",
        f"{buy_material_total:,}" if buy_material_total is not None else "N/A",
    )
    summary.add_row(
        "Aktív nézet teljes költsége",
        f"{selected_cost:,}" if selected_cost is not None else "N/A",
    )
    summary.add_row(
        "Legjobb eladási hely",
        (
            f"{sale[0]} / {sale[1]:,} × {output_amount} = {revenue:,}"
            if sale else "Nincs piaci eladási adat"
        ),
    )
    summary.add_row(
        "Fix piaci adó",
        f"{market_tax:,}" if market_tax is not None else "N/A",
    )
    summary.add_row(
        "Aktív nézet profitja",
        f"{selected_profit:,}" if selected_profit is not None else "N/A",
    )
    if selected_profit is not None and selected_cost > 0:
        summary.add_row("Aktív nézet profit %", f"{selected_profit * 100 / selected_cost:.2f}%")
    summary.add_row(
        "Farmolási profit",
        f"{farm_profit:,}" if farm_profit is not None else "N/A",
    )
    if missing_prices:
        summary.add_row("Vásárlási profit", f"{buy_profit:,}" if buy_profit is not None else "N/A")
    summary.add_row("Prémium", "igen" if settings.premium else "nem")
    console.print(summary)


def craft_menu() -> None:
    while True:
        console.print("\n[bold cyan]Craft teszt és beállítások[/bold cyan]")
        console.print("  [bold]1[/bold] - Craft költség és profit teszt")
        console.print("  [bold]2[/bold] - Craft beállítások")
        console.print("  [bold]0[/bold] - Vissza")
        choice = Prompt.ask("Válassz", choices=["1", "2", "0"], default="0")
        if choice == "0":
            return
        if choice == "1":
            craft_test_menu()
        else:
            craft_settings_menu()


def open_db() -> sqlite3.Connection:
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError(
            f"Nem található az adatbázis: {DB_FILE}. Futtasd előbb a scripts/data_sorter.py-t."
        )
    return connect_database(DB_FILE)


def show_database_status() -> None:
    with open_db() as conn:
        items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        named = conn.execute(
            "SELECT COUNT(*) FROM items WHERE name_en IS NOT NULL AND name_en != ''"
        ).fetchone()[0]
        prices = conn.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0]
        cities = conn.execute(
            "SELECT COUNT(DISTINCT city) FROM market_prices"
        ).fetchone()[0]
        recipes = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='recipes'"
        ).fetchone()[0]
        recipe_count = (
            conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
            if recipes
            else 0
        )
    table = Table(title="Adatbázis állapot")
    table.add_column("Mutató")
    table.add_column("Érték", justify="right")
    table.add_row("Itemek", f"{items:,}")
    table.add_row("Angol névvel", f"{named:,}")
    table.add_row("Piaci rekordok", f"{prices:,}")
    table.add_row("Városok piaci adatokkal", str(cities))
    table.add_row("Craft receptek", f"{recipe_count:,}")
    console.print(table)


def search_items() -> None:
    term = Prompt.ask("Keresési szöveg").strip()
    if not term:
        console.print("[yellow]A keresés nem lehet üres.[/yellow]")
        return
    with open_db() as conn:
        rows = conn.execute(
            """SELECT uniquename, name_en, tier, enchantment, shopcategory
               FROM items
               WHERE uniquename LIKE ? OR name_en LIKE ?
               ORDER BY name_en, uniquename
               LIMIT 50""",
            (f"%{term}%", f"%{term}%"),
        ).fetchall()
    table = Table(title=f"Találatok: {term}")
    for column in ("Uniquename", "Név", "Tier", "Enchant", "Kategória"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row[0], row[1] or "-", str(row[2] or "-"),
            f"+{row[3]}" if row[3] else "alap", row[4] or "-",
        )
    console.print(table)
    if not rows:
        console.print("[yellow]Nincs találat.[/yellow]")


def select_item() -> tuple[list[str], str] | None:
    term = Prompt.ask("Keresett tárgy neve vagy részlete").strip()
    if not term:
        console.print("[yellow]A keresés nem lehet üres.[/yellow]")
        return None
    tier = IntPrompt.ask("Tier", default=4)
    if tier < 1 or tier > 8:
        console.print("[yellow]A tier értéke 1 és 8 között lehet.[/yellow]")
        return None
    with open_db() as conn:
        candidates = conn.execute(
            """SELECT uniquename, name_en, tier, shopcategory
               FROM items
               WHERE (name_en LIKE ? COLLATE NOCASE
                  OR uniquename LIKE ? COLLATE NOCASE)
                 AND tier = ?
               ORDER BY name_en, tier, uniquename
               LIMIT 100""",
            (f"%{term}%", f"%{term}%", tier),
        ).fetchall()
    grouped: dict[str, tuple[str, int | None, str | None]] = {}
    for unique_name, name, tier, category in candidates:
        base_name = unique_name.rsplit("@", 1)[0]
        grouped.setdefault(base_name, (name, tier, category))
    if not grouped:
        console.print("[yellow]Nincs találat.[/yellow]")
        return None
    choice_table = Table(title=f"Legközelebbi találatok: {term} | T{tier}")
    for column in ("Szám", "Név", "Tier", "Uniquename", "Kategória"):
        choice_table.add_column(column)
    choices: dict[str, str] = {}
    for number, (base_name, (name, tier, category)) in enumerate(grouped.items(), 1):
        key = str(number)
        choices[key] = base_name
        choice_table.add_row(key, name or "-", str(tier or "-"), base_name, category or "-")
    console.print(choice_table)
    selected = Prompt.ask("Válassz a találatok közül számmal", choices=list(choices))
    selected_base = choices[selected]
    with open_db() as conn:
        item_ids = [
            row[0] for row in conn.execute(
                """SELECT uniquename FROM items
                   WHERE uniquename = ? OR substr(uniquename, 1, ?) = ?""",
                (selected_base, len(selected_base) + 1, f"{selected_base}@"),
            )
        ]
    return item_ids, selected_base


def show_selected_prices(item_ids: list[str], selected_name: str) -> None:
    with open_db() as conn:
        rows = _market_rows(
            conn,
            "i.uniquename IN (" + ",".join("?" for _ in item_ids) + ")",
            tuple(item_ids),
            list(range(5)),
        )
    table = Table(title=f"Piaci árak: {selected_name}")
    for column in ("Név", "ID", "Város", "Q", "Ench", "Legolcsóbb vétel",
                   "Legdrágább eladás", "Lekérve"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row[0] or "-", row[1], row[2], str(row[3]), f"+{row[4]}",
            f"{row[5]:,}" if row[5] else "-",
            f"{row[6]:,}" if row[6] else "-",
            row[7][:19],
        )
    console.print(table)
    if not rows:
        console.print("[yellow]Ehhez az itemhez még nincs piaci adat.[/yellow]")


def show_selected_flips(item_ids: list[str], selected_name: str) -> None:
    with open_db() as conn:
        placeholders = ",".join("?" for _ in item_ids)
        rows = conn.execute(
            """SELECT i.name_en, buy.item_uniquename, buy.quality,
                      buy.enchantment, buy.city, sell.city,
                      buy.sell_price_min, sell.buy_price_max,
                      sell.buy_price_max - buy.sell_price_min AS margin,
                      ROUND((sell.buy_price_max - buy.sell_price_min) * 100.0
                            / buy.sell_price_min, 2)
               FROM market_prices buy
               JOIN market_prices sell
                 ON sell.item_id = buy.item_id
                AND sell.quality = buy.quality
                AND sell.enchantment = buy.enchantment
                AND sell.city <> buy.city
               JOIN items i ON i.id = buy.item_id
               WHERE i.uniquename IN (""" + placeholders + """)
                 AND buy.sell_price_min > 0
                 AND sell.buy_price_max > buy.sell_price_min
               ORDER BY margin DESC
               LIMIT 5""",
            (*item_ids,),
        ).fetchall()
    table = Table(title=f"Top 5 flip: {selected_name}")
    for column in ("Item", "ID", "Q", "Ench", "Vétel innen", "Eladás ide",
                   "Vételár", "Eladási ár", "Árrés", "Árrés %"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row[0] or "-", row[1], str(row[2]), f"+{row[3]}", row[4], row[5],
            f"{row[6]:,}", f"{row[7]:,}", f"{row[8]:,}", f"{row[9]:.2f}%",
        )
    console.print(table)
    if not rows:
        console.print("[yellow]Ehhez az itemhez nincs pozitív flip lehetőség.[/yellow]")


def item_query_menu() -> None:
    selected = select_item()
    if selected is None:
        return
    item_ids, selected_name = selected
    while True:
        console.print(f"\n[bold cyan]Kiválasztott item: {selected_name}[/bold cyan]")
        console.print("  [bold]1[/bold] - Sima piaci árak")
        console.print("  [bold]2[/bold] - Item flipjei")
        console.print("  [bold]0[/bold] - Vissza")
        choice = Prompt.ask("Válassz", choices=["1", "2", "0"], default="0")
        if choice == "0":
            return
        if choice == "1":
            show_selected_prices(item_ids, selected_name)
        else:
            show_selected_flips(item_ids, selected_name)


def _market_rows(
    conn: sqlite3.Connection,
    item_filter: str,
    item_params: tuple[object, ...],
    enchantments: list[int],
) -> list[tuple]:
    placeholders = ",".join("?" for _ in enchantments)
    return conn.execute(
        """SELECT i.name_en, mp.item_uniquename, mp.city, mp.quality,
                  mp.enchantment, mp.sell_price_min, mp.buy_price_max,
                  mp.fetched_at
           FROM market_prices mp JOIN items i ON i.id = mp.item_id
           WHERE """
        + item_filter
        + " AND mp.enchantment IN (" + placeholders + """)
             AND (mp.sell_price_min > 0 OR mp.buy_price_max > 0)
           ORDER BY mp.sell_price_min ASC, mp.buy_price_max DESC
           LIMIT 100""",
        (*item_params, *enchantments),
    ).fetchall()


def fetch_one_item() -> None:
    item_id = Prompt.ask("API item ID (például T4_BAG vagy T4_BAG@1)").strip()
    city = Prompt.ask("Városok vesszővel", default=",".join(CITIES)).strip()
    locations = tuple(value.strip() for value in city.split(",") if value.strip())
    if not item_id or not locations:
        console.print("[yellow]Item ID és legalább egy város szükséges.[/yellow]")
        return
    with open_db() as conn:
        from app.services.market_api import _request_url

        url_length = len(_request_url([item_id], locations))
        if url_length > 4000:
            console.print("[red]Ez a kérés túllépi a 4000 karakteres URL-limitet.[/red]")
            return
        prices = fetch_prices_for_chunk([item_id], locations=locations)
        saved = save_prices(conn, prices)
    console.print(f"[green]API rekordok: {len(prices)}, mentve: {saved}[/green]")
    active_prices = [
        record for record in prices
        if any(
            record.get(field, 0) not in (None, 0)
            for field in ("sell_price_min", "sell_price_max",
                          "buy_price_min", "buy_price_max")
        )
    ]
    if not active_prices and prices:
        console.print(
            "[yellow]Az API megtalálta az itemet, de a kiválasztott városokban "
            "jelenleg nincs aktív vételi vagy eladási ajánlat.[/yellow]"
        )


def fetch_all_items() -> None:
    console.print(
        "[yellow]Ez az összes, névvel rendelkező itemet lekéri; sok API-hívás lehet.[/yellow]"
    )
    if not Confirm.ask("Biztosan elindítod?", default=False):
        return
    with open_db() as conn:
        saved = do_fetch(conn)
    console.print(f"[green]Teljes frissítés kész. Mentett rekordok: {saved}[/green]")


def import_crafting_data_menu() -> None:
    from app.services.crafting import import_crafting_data

    if not Confirm.ask(
        "Az items.json crafting adatait importáljam az adatbázisba?",
        default=True,
    ):
        return
    recipe_count, material_count, unresolved_count = import_crafting_data()
    console.print(
        f"[green]Craft import kész: {recipe_count} recept, "
        f"{material_count} alapanyagkapcsolat.[/green]"
    )
    if unresolved_count:
        console.print(
            f"[yellow]{unresolved_count} item nem volt megtalálható az items táblában, "
            "ezeket név szerint őriztem meg.[/yellow]"
        )


def reload_database_from_json() -> None:
    """Rebuild the catalogue from the current JSON files and reimport recipes."""
    console.print(
        "[yellow]Az adatbázis újraépítése törli a jelenlegi piaci árakat és craft "
        "adatokat, majd az aktuális items.json alapján hozza létre őket.[/yellow]"
    )
    if not Confirm.ask("Biztosan újratöltöd az adatbázist?", default=False):
        return
    project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    console.print("[cyan]Itemkatalógus újraépítése...[/cyan]")
    subprocess.run(
        [sys.executable, os.path.join(project_dir, "scripts", "data_sorter.py")],
        cwd=project_dir,
        check=True,
    )
    console.print("[cyan]Craft adatok újraimportálása...[/cyan]")
    from app.services.crafting import import_crafting_data

    recipe_count, material_count, unresolved_count = import_crafting_data()
    console.print(
        f"[green]Újratöltés kész: {recipe_count} recept, "
        f"{material_count} alapanyagkapcsolat.[/green]"
    )
    if unresolved_count:
        console.print(
            f"[yellow]{unresolved_count} craft item nem található az items táblában.[/yellow]"
        )


def show_item_flips() -> None:
    """List the five most profitable city-to-city item flip opportunities."""
    with open_db() as conn:
        rows = conn.execute(
            """SELECT
                   i.name_en,
                   buy.item_uniquename,
                   buy.quality,
                   buy.enchantment,
                   buy.city,
                   sell.city,
                   buy.sell_price_min,
                   sell.buy_price_max,
                   sell.buy_price_max - buy.sell_price_min AS margin,
                   ROUND(
                       (sell.buy_price_max - buy.sell_price_min) * 100.0
                       / buy.sell_price_min, 2
                   ) AS margin_percent,
                   buy.fetched_at,
                   sell.fetched_at
               FROM market_prices buy
               JOIN market_prices sell
                 ON sell.item_id = buy.item_id
                AND sell.quality = buy.quality
                AND sell.enchantment = buy.enchantment
                AND sell.city <> buy.city
               JOIN items i ON i.id = buy.item_id
               WHERE buy.sell_price_min > 0
                 AND sell.buy_price_max > buy.sell_price_min
                 AND sell.buy_price_max > 0
               ORDER BY margin DESC, margin_percent DESC
               LIMIT 5"""
        ).fetchall()

    table = Table(title="Legjobb 5 item flip lehetőség")
    for column in (
        "Item", "ID", "Q", "Ench", "Vétel innen", "Eladás ide",
        "Vételár", "Eladási ár", "Bruttó árrés", "Árrés %", "Adat",
    ):
        table.add_column(column)
    for row in rows:
        fetched_at = min(row[10], row[11])[:19]
        table.add_row(
            row[0] or "-",
            row[1],
            str(row[2]),
            f"+{row[3]}",
            row[4],
            row[5],
            f"{row[6]:,}",
            f"{row[7]:,}",
            f"{row[8]:,}",
            f"{row[9]:.2f}%",
            fetched_at,
        )
    console.print(table)
    if not rows:
        console.print(
            "[yellow]Nincs jelenleg pozitív, két város közötti flip lehetőség "
            "az adatbázisban.[/yellow]"
        )


def database_menu() -> None:
    while True:
        console.print("\n[bold cyan]Adatbázis[/bold cyan]")
        console.print("  [bold]1[/bold] - Állapot")
        console.print("  [bold]2[/bold] - Item frissítés")
        console.print("  [bold]3[/bold] - Teljes piaci adatbázis frissítése")
        console.print("  [bold]4[/bold] - Craft adatok importálása")
        console.print("  [bold]5[/bold] - Adatbázis újratöltése az items.json-ból")
        console.print("  [bold]0[/bold] - Vissza")
        choice = Prompt.ask(
            "Válassz", choices=["1", "2", "3", "4", "5", "0"], default="0"
        )
        if choice == "0":
            return
        try:
            if choice == "1":
                show_database_status()
            elif choice == "2":
                fetch_one_item()
            elif choice == "3":
                fetch_all_items()
            elif choice == "4":
                import_crafting_data_menu()
            else:
                reload_database_from_json()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as error:
            console.print(f"[red]Hiba: {error}[/red]")


def launch_gui() -> None:
    from app.ui.qt_app import run_gui

    run_gui()


def main() -> None:
    actions = {
        "1": ("Adatbázis", database_menu),
        "2": ("Item lekérdezés", item_query_menu),
        "3": ("Összes item legjobb flipjei", show_item_flips),
        "4": ("Craft teszt és beállítások", craft_menu),
        "5": ("PyQt6 grafikus felület indítása", launch_gui),
    }
    while True:
        console.print("\n[bold cyan]Albion Prize Shower - tesztmenü[/bold cyan]")
        for key, (label, _) in actions.items():
            console.print(f"  [bold]{key}[/bold] - {label}")
        console.print("  [bold]0[/bold] - Kilépés")
        choice = Prompt.ask("Válassz", choices=[*actions, "0"], default="0")
        if choice == "0":
            console.print("Kilépés.")
            return
        try:
            actions[choice][1]()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as error:
            console.print(f"[red]Hiba: {error}[/red]")


if __name__ == "__main__":
    main()
