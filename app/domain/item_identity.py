"""Canonical catalogue and Albion Data Project item identifiers."""

from __future__ import annotations

import re
from dataclasses import dataclass

_LEVEL_SUFFIX = re.compile(r"_LEVEL(?P<level>\d+)$", re.IGNORECASE)
_RESOURCE_CATEGORIES = {"resource", "resources", "refinedresource", "refinedresources"}


@dataclass(frozen=True, slots=True)
class ItemIdentity:
    catalogue_id: str
    market_id: str
    enchantment: int


def normalize_item_identity(
    uniquename: str,
    explicit_enchantment: object = None,
    shopcategory: str | None = None,
    shopsubcategory: str | None = None,
) -> ItemIdentity:
    """Resolve the ID expected by AODP without changing the catalogue key.

    Explicit item metadata is authoritative. ``_LEVELN`` is only a fallback for
    resources/refined resources because other item families use LEVEL as part of
    their ordinary catalogue name.
    """

    catalogue_id = str(uniquename).strip()
    base, separator, suffix = catalogue_id.rpartition("@")
    if separator and suffix.isdigit():
        return ItemIdentity(catalogue_id, catalogue_id, int(suffix))

    explicit_present = explicit_enchantment not in (None, "")
    try:
        explicit = int(explicit_enchantment) if explicit_present else None
    except (TypeError, ValueError):
        explicit = None
        explicit_present = False

    category_tokens = {
        str(shopcategory or "").lower(),
        str(shopsubcategory or "").lower(),
    }
    is_resource = bool(category_tokens & _RESOURCE_CATEGORIES)
    level_match = _LEVEL_SUFFIX.search(catalogue_id) if is_resource else None

    if explicit is not None and explicit > 0:
        return ItemIdentity(catalogue_id, f"{catalogue_id}@{explicit}", explicit)
    if explicit_present:
        return ItemIdentity(catalogue_id, catalogue_id, max(0, explicit or 0))
    if level_match:
        level = int(level_match.group("level"))
        return ItemIdentity(catalogue_id, f"{catalogue_id}@{level}", level)
    return ItemIdentity(catalogue_id, catalogue_id, 0)
