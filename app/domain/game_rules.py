"""Versioned Albion Online economy constants used by default.

The market rates below reflect Into the Fray Patch 6 (2022-09-14): Premium
characters pay 4% sales tax and 2.5% order setup fee; non-Premium characters
pay double.  Keeping the effective date and source beside the values makes a
future rules update explicit and reviewable.
"""

from __future__ import annotations

from app.domain.market import FeePolicy

MARKET_RULES_EFFECTIVE_FROM = "2022-09-14"
MARKET_RULES_SOURCE = (
    "https://forum.albiononline.com/index.php/Thread/169206-"
    "14-September-2022-Into-the-Fray-Patch-6/"
)

PREMIUM_TRANSACTION_TAX_BPS = 400
PREMIUM_SETUP_FEE_BPS = 250
NON_PREMIUM_TRANSACTION_TAX_BPS = 800
NON_PREMIUM_SETUP_FEE_BPS = 500


def market_fee_policy(
    premium: bool,
    *,
    transport_flat: int = 0,
    risk_reserve_bps: int = 0,
) -> FeePolicy:
    """Return the fixed in-game market fee policy for a player profile."""

    return FeePolicy(
        transaction_tax_bps=(
            PREMIUM_TRANSACTION_TAX_BPS if premium else NON_PREMIUM_TRANSACTION_TAX_BPS
        ),
        setup_fee_bps=PREMIUM_SETUP_FEE_BPS if premium else NON_PREMIUM_SETUP_FEE_BPS,
        transport_flat=transport_flat,
        risk_reserve_bps=risk_reserve_bps,
    )
