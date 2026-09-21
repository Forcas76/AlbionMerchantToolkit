"""Refining recipe import and deterministic refining/ROI calculations.

The service deliberately prices the direct ``craftresource`` entries from
``items.json``.  It does not expand a material into another recipe, which
keeps the result explainable and avoids silently turning refining into a
recursive crafting planner.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_FLOOR
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.domain.game_rules import market_fee_policy
from app.paths import CATALOG_DB_FILE, ITEMS_FILE

DB_FILE = str(CATALOG_DB_FILE)
DEFAULT_ITEMS_FILE = str(ITEMS_FILE)
LOGGER = logging.getLogger(__name__)


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    return []


@dataclass(frozen=True, slots=True)
class RefiningCityConfig:
    city: str
    resource_type: str
    refined_item: str
    local_production_bonus: Decimal = Decimal("0")
    base_return_rate: Decimal = Decimal("0.15")
    focus_return_rate: Decimal = Decimal("0")
    station_fee: int = 0
    refined_item_id: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "local_production_bonus",
            "base_return_rate",
            "focus_return_rate",
        ):
            value = _decimal(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.station_fee < 0:
            raise ValueError("station_fee cannot be negative")

    @property
    def production_bonus(self) -> Decimal:
        """Compatibility name used by the planning/schema terminology."""

        return self.local_production_bonus


@dataclass(frozen=True, slots=True)
class RefiningMaterial:
    item_uniquename: str
    amount: int
    returnable: bool = True
    max_return_amount: int | None = None
    item_id: int | None = None


@dataclass(frozen=True, slots=True)
class RefiningRecipe:
    output_item_uniquename: str
    materials: tuple[RefiningMaterial, ...]
    output_amount: int = 1
    silver_cost: int = 0
    craft_time_seconds: Decimal = Decimal("0")
    crafting_focus: int = 0
    variant_index: int = 0
    output_item_id: int | None = None

    @property
    def input_item_uniquename(self) -> str | None:
        """Compatibility convenience for the common one-input recipe."""

        return self.materials[0].item_uniquename if self.materials else None

    @property
    def input_amount(self) -> int:
        return self.materials[0].amount if self.materials else 0


@dataclass(frozen=True, slots=True)
class MaterialCalculation:
    item_uniquename: str
    required_amount: int
    returned_amount: Decimal
    net_amount: Decimal
    unit_price: int
    gross_cost: int
    returned_value: int
    effective_cost: int


@dataclass(frozen=True, slots=True)
class RefiningResult:
    recipe: RefiningRecipe
    refining_city: str
    buy_city: str
    sell_city: str
    batches: int
    use_focus: bool
    return_rate: Decimal
    output_amount: int
    materials: tuple[MaterialCalculation, ...]
    gross_input_cost: int
    returned_material_value: int
    input_cost: int
    station_fee: int
    transport_fee: int
    silver_cost: int
    total_cost: int
    gross_revenue: int
    market_tax: int
    market_listing_fee: int
    net_revenue: int
    profit: int
    roi_percent: Decimal
    focus_used: int
    silver_per_focus: Decimal | None

    @property
    def investment(self) -> int:
        return self.total_cost

    @property
    def net_profit(self) -> int:
        return self.profit

    @property
    def returned_resources(self) -> int:
        return sum(int(material.returned_amount) for material in self.materials)


def _validate_rate(value: Decimal, name: str) -> Decimal:
    value = _decimal(value)
    if value < 0 or value > 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def return_rate_from_bonus(production_bonus: int | float | Decimal) -> Decimal:
    """Convert a percentage production bonus to its diminishing-return rate.

    For example, a 35% production bonus becomes ``1 - 1 / 1.35``.  Callers
    may pass ``0.35`` as a convenience; values up to one are treated as
    already-normalised fractions.
    """

    bonus = _decimal(production_bonus)
    if bonus < 0:
        raise ValueError("production_bonus cannot be negative")
    if bonus <= 1:
        return (bonus / (Decimal("1") + bonus)).quantize(Decimal("0.000001"))
    return (Decimal("1") - Decimal("1") / (Decimal("1") + bonus / 100)).quantize(
        Decimal("0.000001")
    )


def calculate_return_rate(
    city: RefiningCityConfig,
    *,
    use_focus: bool = False,
    specialization_bonus: Decimal = Decimal("0"),
) -> Decimal:
    """Return the configured effective RRR for one refining operation.

    Rates in the database are fractions (``0.15`` means 15%).  The local
    production bonus is converted using the game's diminishing-return formula
    and then added to the configured base/focus/specialisation rates.  The
    result is capped at 100% so malformed user settings cannot create items.
    """

    base = _validate_rate(city.base_return_rate, "base_return_rate")
    focus = _validate_rate(city.focus_return_rate, "focus_return_rate")
    specialization = _validate_rate(specialization_bonus, "specialization_bonus")
    result = base + return_rate_from_bonus(city.local_production_bonus) + specialization
    if use_focus:
        result += focus
    return min(Decimal("1"), result).quantize(Decimal("0.000001"))


def _round_expected(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_FLOOR)


def _recipe_from_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row | tuple[Any, ...],
) -> RefiningRecipe:
    values = dict(zip(
        ("id", "input_item_uniquename", "output_item_uniquename",
         "variant_index", "output_amount", "silver_cost",
         "craft_time_seconds", "crafting_focus", "output_item_id"),
        row,
    ))
    materials = tuple(
        RefiningMaterial(
            item_uniquename=material[0],
            amount=material[1],
            returnable=bool(material[2]),
            max_return_amount=material[3],
            item_id=material[4],
        )
        for material in conn.execute(
            """SELECT material_uniquename, amount, returnable,
                      max_return_amount, material_item_id
               FROM refining_recipe_materials WHERE recipe_id=?
               ORDER BY CASE WHEN material_uniquename=? THEN 0 ELSE 1 END,
                        material_uniquename""",
            (values["id"], values["input_item_uniquename"]),
        )
    )
    return RefiningRecipe(
        output_item_uniquename=values["output_item_uniquename"],
        materials=materials,
        output_amount=values["output_amount"],
        silver_cost=values["silver_cost"],
        craft_time_seconds=_decimal(values["craft_time_seconds"]),
        crafting_focus=values["crafting_focus"],
        variant_index=values["variant_index"],
        output_item_id=values["output_item_id"],
    )


def list_refining_recipes(
    conn: sqlite3.Connection, output_item: str | None = None
) -> list[RefiningRecipe]:
    """Load imported direct recipes, optionally filtered by output id."""

    where = "WHERE output_item_uniquename=?" if output_item else ""
    args: tuple[Any, ...] = (output_item,) if output_item else ()
    rows = conn.execute(
        f"""SELECT id, input_item_uniquename, output_item_uniquename,
                   variant_index, output_amount, silver_cost,
                   craft_time_seconds, crafting_focus, output_item_id
            FROM refining_recipes {where}
            ORDER BY output_item_uniquename, variant_index""",
        args,
    )
    return [_recipe_from_row(conn, row) for row in rows]


def load_refining_cities(conn: sqlite3.Connection) -> list[RefiningCityConfig]:
    rows = conn.execute(
        """SELECT city, resource_type, refined_item, refined_item_id,
                  local_production_bonus, base_return_rate, focus_return_rate,
                  station_fee
           FROM refining_cities ORDER BY city"""
    )
    return [
        RefiningCityConfig(
            city=row[0],
            resource_type=row[1],
            refined_item=row[2],
            refined_item_id=row[3],
            local_production_bonus=_decimal(row[4]),
            base_return_rate=_decimal(row[5]),
            focus_return_rate=_decimal(row[6]),
            station_fee=row[7],
        )
        for row in rows
    ]


def save_refining_city(conn: sqlite3.Connection, city: RefiningCityConfig) -> None:
    """Insert or update one city profile without embedding game constants."""

    conn.execute(
        """INSERT INTO refining_cities(
               city, resource_type, refined_item, refined_item_id,
               local_production_bonus, production_bonus, base_return_rate,
               focus_return_rate, station_fee
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(city) DO UPDATE SET
               resource_type=excluded.resource_type,
               refined_item=excluded.refined_item,
               refined_item_id=excluded.refined_item_id,
               local_production_bonus=excluded.local_production_bonus,
               production_bonus=excluded.production_bonus,
               base_return_rate=excluded.base_return_rate,
               focus_return_rate=excluded.focus_return_rate,
               station_fee=excluded.station_fee,
               updated_at=CURRENT_TIMESTAMP""",
        (
            city.city,
            city.resource_type,
            city.refined_item,
            city.refined_item_id,
            str(city.local_production_bonus),
            str(city.local_production_bonus),
            str(city.base_return_rate),
            str(city.focus_return_rate),
            city.station_fee,
        ),
    )
    conn.commit()


def _create_refining_tables(conn: sqlite3.Connection) -> None:
    # Useful for callers importing into a small, pre-existing test catalogue.
    from app.core.database import apply_catalog_migrations

    apply_catalog_migrations(conn)


def ensure_refining_schema(conn: sqlite3.Connection) -> None:
    """Apply the catalog migrations needed by the refining service."""

    _create_refining_tables(conn)


def _walk_items(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        if "@uniquename" in node:
            yield node
        for value in node.values():
            yield from _walk_items(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_items(value)


def _refining_entries(item: dict[str, Any]) -> list[tuple[str, int, dict[str, Any]]]:
    from app.services.crafting import _recipe_entries

    return _recipe_entries(item)


def import_refining_recipes(
    db_file: str | Path = DB_FILE,
    items_file: str | Path = DEFAULT_ITEMS_FILE,
) -> tuple[int, int, int]:
    """Import direct refined-resource recipes from ``items.json``.

    The return value is ``(recipes, materials, unresolved_item_references)``.
    Existing rows are replaced idempotently, while unresolved material names
    remain useful to the calculator and have a NULL internal item id.
    """

    LOGGER.info("Refining import indul")
    with Path(items_file).open(encoding="utf-8") as source:
        raw_items = json.load(source)
    with closing(sqlite3.connect(str(db_file))) as conn:
        _create_refining_tables(conn)
        item_ids = {
            row[1]: row[0]
            for row in conn.execute("SELECT id, uniquename FROM items")
        }
        recipes = materials = unresolved = 0
        seen: set[str] = set()
        for item in _walk_items(raw_items):
            base_name = item.get("@uniquename")
            if not base_name or base_name in seen:
                continue
            seen.add(base_name)
            if item.get("@shopsubcategory1") != "refinedresources":
                continue
            for output_name, variant, requirement in _refining_entries(item):
                output_id = item_ids.get(output_name)
                if output_id is None:
                    unresolved += 1
                    continue
                raw_materials = _as_list(requirement.get("craftresource"))
                if not raw_materials:
                    continue
                conn.execute(
                    """INSERT INTO refining_recipes(
                           input_item_id, input_item_uniquename,
                           output_item_id, output_item_uniquename, variant_index,
                           input_amount, output_amount, silver_cost,
                           craft_time_seconds, crafting_focus
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(output_item_uniquename, variant_index) DO UPDATE SET
                           input_item_id=excluded.input_item_id,
                           input_item_uniquename=excluded.input_item_uniquename,
                           output_item_id=excluded.output_item_id,
                           input_amount=excluded.input_amount,
                           output_amount=excluded.output_amount,
                           silver_cost=excluded.silver_cost,
                           craft_time_seconds=excluded.craft_time_seconds,
                           crafting_focus=excluded.crafting_focus,
                           imported_at=CURRENT_TIMESTAMP""",
                    (
                        item_ids.get(raw_materials[0].get("@uniquename")),
                        raw_materials[0].get("@uniquename"),
                        output_id,
                        output_name,
                        variant,
                        _int(raw_materials[0].get("@count")),
                        max(1, _int(requirement.get("@amountcrafted"), 1)),
                        _int(requirement.get("@silver")),
                        str(_decimal(requirement.get("@time"))),
                        _int(requirement.get("@craftingfocus")),
                    ),
                )
                recipe_id = conn.execute(
                    """SELECT id FROM refining_recipes
                       WHERE output_item_uniquename=? AND variant_index=?""",
                    (output_name, variant),
                ).fetchone()[0]
                conn.execute(
                    "DELETE FROM refining_recipe_materials WHERE recipe_id=?",
                    (recipe_id,),
                )
                for raw_material in raw_materials:
                    material_name = raw_material.get("@uniquename")
                    if not material_name:
                        continue
                    max_return = raw_material.get("@maxreturnamount")
                    if material_name not in item_ids:
                        unresolved += 1
                    conn.execute(
                        """INSERT INTO refining_recipe_materials(
                               recipe_id, material_item_id, material_uniquename,
                               amount, returnable, max_return_amount
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            recipe_id,
                            item_ids.get(material_name),
                            material_name,
                            max(0, _int(raw_material.get("@count"))),
                            max_return not in ("0", 0),
                            _int(max_return) if max_return is not None else None,
                        ),
                    )
                    materials += 1
                recipes += 1
        conn.commit()
    LOGGER.info(
        "Refining import kész | receptek=%s | anyagok=%s | feloldatlan=%s",
        recipes,
        materials,
        unresolved,
    )
    return recipes, materials, unresolved


def _price(
    prices: Mapping[tuple[str, str], int] | Mapping[str, int],
    item: str,
    city: str,
) -> int:
    value: Any = prices.get((item, city), prices.get(item, 0))
    return max(0, _int(value))


def calculate_refining(
    recipe: RefiningRecipe,
    city: RefiningCityConfig,
    *,
    material_prices: Mapping[tuple[str, str], int] | Mapping[str, int] | None = None,
    input_price: int | None = None,
    output_price: int,
    batches: int = 1,
    buy_city: str = "",
    sell_city: str = "",
    transport_fee: int = 0,
    station_fee: int | None = None,
    premium: bool = True,
    use_focus: bool = False,
) -> RefiningResult:
    """Calculate one direct refining route from explicit market prices."""

    if batches < 1:
        raise ValueError("batches must be positive")
    if transport_fee < 0 or (station_fee is not None and station_fee < 0):
        raise ValueError("fees cannot be negative")
    if output_price < 0:
        raise ValueError("output_price cannot be negative")
    if material_prices is None:
        if input_price is None:
            raise ValueError("material_prices or input_price is required")
        material_prices = {material.item_uniquename: input_price for material in recipe.materials}
    elif input_price is not None:
        raise ValueError("pass either material_prices or input_price, not both")
    rrr = calculate_return_rate(city, use_focus=use_focus)
    material_results: list[MaterialCalculation] = []
    gross_input = returned_value = effective_input = 0
    for material in recipe.materials:
        required = material.amount * batches
        returned = (
            _round_expected(Decimal(required) * rrr)
            if material.returnable
            else Decimal("0")
        )
        if material.max_return_amount is not None:
            returned = min(returned, Decimal(material.max_return_amount * batches))
        net = max(Decimal("0"), Decimal(required) - returned)
        price = _price(material_prices, material.item_uniquename, buy_city)
        gross = required * price
        recovered = int((returned * price).quantize(Decimal("1"), rounding=ROUND_FLOOR))
        effective = max(0, gross - recovered)
        material_results.append(MaterialCalculation(
            item_uniquename=material.item_uniquename,
            required_amount=required,
            returned_amount=returned,
            net_amount=net,
            unit_price=price,
            gross_cost=gross,
            returned_value=recovered,
            effective_cost=effective,
        ))
        gross_input += gross
        returned_value += recovered
        effective_input += effective
    station = (city.station_fee if station_fee is None else station_fee) * batches
    silver = recipe.silver_cost * batches
    transport = transport_fee
    total_cost = effective_input + station + silver + transport
    output_amount = recipe.output_amount * batches
    gross_revenue = output_amount * max(0, output_price)
    fees = market_fee_policy(premium)
    tax = fees.transaction_tax(gross_revenue)
    listing = fees.setup_fee(gross_revenue)
    net_revenue = gross_revenue - tax - listing
    profit = net_revenue - total_cost
    roi = (
        (Decimal(profit) * Decimal(100) / Decimal(total_cost)).quantize(Decimal("0.01"))
        if total_cost
        else Decimal("0")
    )
    focus_used = recipe.crafting_focus * batches if use_focus else 0
    focus_profit = None
    if focus_used:
        # Calculate the marginal value against the same route without focus.
        # This does not recurse because the comparison explicitly disables
        # focus.
        without_focus = calculate_refining(
            recipe,
            city,
            material_prices=material_prices,
            output_price=output_price,
            batches=batches,
            buy_city=buy_city,
            sell_city=sell_city,
            transport_fee=transport_fee,
            station_fee=station_fee,
            premium=premium,
            use_focus=False,
        )
        focus_profit = (
            Decimal(profit - without_focus.profit) / Decimal(focus_used)
        ).quantize(Decimal("0.01"))
    return RefiningResult(
        recipe=recipe,
        refining_city=city.city,
        buy_city=buy_city,
        sell_city=sell_city,
        batches=batches,
        use_focus=use_focus,
        return_rate=rrr,
        output_amount=output_amount,
        materials=tuple(material_results),
        gross_input_cost=gross_input,
        returned_material_value=returned_value,
        input_cost=effective_input,
        station_fee=station,
        transport_fee=transport,
        silver_cost=silver,
        total_cost=total_cost,
        gross_revenue=gross_revenue,
        market_tax=tax,
        market_listing_fee=listing,
        net_revenue=net_revenue,
        profit=profit,
        roi_percent=roi,
        focus_used=focus_used,
        silver_per_focus=focus_profit,
    )


def calculate_focus_value(
    recipe: RefiningRecipe,
    city: RefiningCityConfig,
    *,
    material_prices: Mapping[tuple[str, str], int] | Mapping[str, int] | None = None,
    input_price: int | None = None,
    output_price: int,
    batches: int = 1,
    buy_city: str = "",
    sell_city: str = "",
    transport_fee: int = 0,
    station_fee: int | None = None,
    premium: bool = True,
) -> tuple[RefiningResult, RefiningResult]:
    """Return no-focus/focus results with marginal silver per focus."""

    without = calculate_refining(
        recipe, city, material_prices=material_prices, output_price=output_price,
        input_price=input_price,
        batches=batches, buy_city=buy_city, sell_city=sell_city,
        transport_fee=transport_fee, station_fee=station_fee, premium=premium,
    )
    with_focus = calculate_refining(
        recipe, city, material_prices=material_prices, output_price=output_price,
        input_price=input_price,
        batches=batches, buy_city=buy_city, sell_city=sell_city,
        transport_fee=transport_fee, station_fee=station_fee, premium=premium,
        use_focus=True,
    )
    marginal = with_focus.profit - without.profit
    if with_focus.focus_used:
        per_focus = (Decimal(marginal) / Decimal(with_focus.focus_used)).quantize(
            Decimal("0.01")
        )
        with_focus = replace(with_focus, silver_per_focus=per_focus)
    return without, with_focus


def calculate_refining_roi(*args: Any, **kwargs: Any) -> RefiningResult:
    """Explicitly named alias used by callers building an ROI screen."""

    return calculate_refining(*args, **kwargs)


def rank_refining_routes(
    recipe: RefiningRecipe,
    cities: Sequence[RefiningCityConfig],
    *,
    material_prices: Mapping[tuple[str, str], int] | Mapping[str, int],
    output_prices: Mapping[str, int],
    buy_cities: Sequence[str] | None = None,
    sell_cities: Sequence[str] | None = None,
    batches: int = 1,
    transport_fees: Mapping[tuple[str, str], int] | None = None,
    premium: bool = True,
    use_focus: bool = False,
) -> list[RefiningResult]:
    """Evaluate buy/refine/sell combinations and rank by profit then ROI."""

    buys = tuple(buy_cities or [city.city for city in cities])
    sells = tuple(sell_cities or output_prices.keys())
    transport_fees = transport_fees or {}
    results: list[RefiningResult] = []
    for city in cities:
        for buy in buys:
            for sell in sells:
                output_price = _int(output_prices.get(sell, 0))
                if output_price <= 0:
                    continue
                results.append(
                    calculate_refining(
                        recipe,
                        city,
                        material_prices=material_prices,
                        output_price=output_price,
                        batches=batches,
                        buy_city=buy,
                        sell_city=sell,
                        transport_fee=max(0, _int(transport_fees.get((buy, city.city), 0))),
                        premium=premium,
                        use_focus=use_focus,
                    )
                )
    return sorted(results, key=lambda result: (result.profit, result.roi_percent), reverse=True)


# Short aliases are kept for callers that use the terminology from the
# planning document.
calculate_rrr = calculate_return_rate
import_refining_data = import_refining_recipes


def market_prices_for_route(
    conn: sqlite3.Connection,
    recipe: RefiningRecipe,
    buy_city: str,
    sell_city: str,
) -> tuple[dict[tuple[str, str], int], int]:
    """Read conservative ask/bid prices from an attached market database."""

    material_prices: dict[tuple[str, str], int] = {}
    for material in recipe.materials:
        row = conn.execute(
            """SELECT sell_price_min FROM market.market_prices
               WHERE item_uniquename=? AND city=? AND quality=1
               ORDER BY enchantment LIMIT 1""",
            (material.item_uniquename, buy_city),
        ).fetchone()
        material_prices[(material.item_uniquename, buy_city)] = row[0] if row else 0
    row = conn.execute(
        """SELECT buy_price_max FROM market.market_prices
           WHERE item_uniquename=? AND city=? AND quality=1
           ORDER BY enchantment LIMIT 1""",
        (recipe.output_item_uniquename, sell_city),
    ).fetchone()
    return material_prices, max(0, _int(row[0])) if row and row[0] is not None else 0


__all__ = [
    "DB_FILE",
    "DEFAULT_ITEMS_FILE",
    "MaterialCalculation",
    "RefiningCityConfig",
    "RefiningMaterial",
    "RefiningRecipe",
    "RefiningResult",
    "calculate_focus_value",
    "calculate_refining",
    "calculate_refining_roi",
    "calculate_rrr",
    "calculate_return_rate",
    "ensure_refining_schema",
    "import_refining_data",
    "import_refining_recipes",
    "list_refining_recipes",
    "load_refining_cities",
    "market_prices_for_route",
    "rank_refining_routes",
    "return_rate_from_bonus",
    "save_refining_city",
]
