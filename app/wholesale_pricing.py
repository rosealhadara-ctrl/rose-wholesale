"""Wholesale pricing (القسم ١٧): supplier choice, margins, customer tiers.

Price for a trader in tier T, product P, unit U (piece / dozen / carton):

1. Purchase cost = the chosen supplier offer for that unit:
   - ``cheapest``: cheapest active offer *for that unit on its own*.
   - ``manual``: the supplier the manager picked (falls back to cheapest, with
     a warning, if that supplier no longer carries the item).
2. Selling price, first rule that applies:
   a. a fixed price for P in tier T for unit U;
   b. a fixed manual price on P for unit U;
   c. P's own margin %, applied to the cost;
   d. T's margin % (or the global margin when T has none), applied to the cost.
3. Round *up* to the nearest 0.50 EGP (117.30 -> 117.50, 117.60 -> 118.00).
4. Warn when the price ends up below the purchase cost.
"""

from __future__ import annotations

import sqlite3
from decimal import ROUND_CEILING, Decimal
from typing import Any, Dict, Iterable, List, Optional

UNITS = ("piece", "dozen", "carton")
UNIT_LABELS = {"piece": "قطعة", "dozen": "دستة", "carton": "كرتونة"}
DEFAULT_PIECES_PER_DOZEN = 12


def round_up_half(value: float) -> float:
    """Round up to the nearest 0.50, ignoring float noise (117.0000001 -> 117)."""
    amount = Decimal(str(value)).quantize(Decimal("0.0001"))
    halves = (amount * 2).to_integral_value(rounding=ROUND_CEILING)
    return float(halves / 2)


def apply_margin(cost: float, percent: float) -> float:
    amount = Decimal(str(cost)) * (Decimal("1") + Decimal(str(percent)) / Decimal("100"))
    return float(amount)


def get_settings(conn: sqlite3.Connection) -> Dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM ws_settings")}


def global_margin(settings: Dict[str, str]) -> float:
    try:
        return float(settings.get("global_margin_percent", "0") or 0)
    except ValueError:
        return 0.0


def list_tiers(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.*, (SELECT COUNT(*) FROM ws_trader_tiers tt WHERE tt.tier_id = t.id) AS traders
        FROM ws_customer_tiers t ORDER BY t.sort_order, t.id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def default_tier(conn: sqlite3.Connection) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM ws_customer_tiers ORDER BY is_default DESC, sort_order, id LIMIT 1"
    ).fetchone()
    return dict(row) if row else {"id": 0, "name": "", "margin_percent": None, "is_default": 1}


def tier_for_customer(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT t.* FROM ws_trader_tiers tt
        JOIN ws_customer_tiers t ON t.id = tt.tier_id
        WHERE tt.customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    return dict(row) if row else default_tier(conn)


def load_offers(
    conn: sqlite3.Connection, product_ids: Optional[Iterable[int]] = None
) -> Dict[int, List[Dict[str, Any]]]:
    sql = """
        SELECT o.*, s.alias AS supplier_alias, s.is_active AS supplier_active
        FROM ws_offers o JOIN ws_suppliers s ON s.id = o.supplier_id
    """
    params: List[Any] = []
    if product_ids is not None:
        ids = list(product_ids)
        if not ids:
            return {}
        sql += " WHERE o.product_id IN (%s)" % ",".join("?" * len(ids))
        params = ids
    sql += " ORDER BY o.product_id, o.supplier_id"
    result: Dict[int, List[Dict[str, Any]]] = {}
    for row in conn.execute(sql, params):
        result.setdefault(row["product_id"], []).append(dict(row))
    return result


def load_tier_prices(
    conn: sqlite3.Connection, product_ids: Optional[Iterable[int]] = None
) -> Dict[int, Dict[int, Dict[str, float]]]:
    sql = "SELECT product_id, tier_id, unit, price FROM ws_tier_prices"
    params: List[Any] = []
    if product_ids is not None:
        ids = list(product_ids)
        if not ids:
            return {}
        sql += " WHERE product_id IN (%s)" % ",".join("?" * len(ids))
        params = ids
    result: Dict[int, Dict[int, Dict[str, float]]] = {}
    for row in conn.execute(sql, params):
        result.setdefault(row["product_id"], {}).setdefault(row["tier_id"], {})[row["unit"]] = row["price"]
    return result


def _active(offers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [o for o in offers if o["is_active"] and o.get("supplier_active", 1)]


def _pieces(unit: str, offer: Optional[Dict[str, Any]], product: Dict[str, Any]) -> Optional[int]:
    if unit == "piece":
        return 1
    key = "pieces_per_dozen" if unit == "dozen" else "pieces_per_carton"
    value = (offer or {}).get(key) or product.get(key)
    if not value and unit == "dozen":
        value = DEFAULT_PIECES_PER_DOZEN
    return int(value) if value else None


def choose_sources(product: Dict[str, Any], offers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick the supplier offer used as purchase cost for each unit."""
    active = _active(offers)
    warnings: List[str] = []
    pool = active
    if product.get("source_mode") == "manual":
        chosen = [o for o in active if o["supplier_id"] == product.get("manual_supplier_id")]
        if chosen:
            pool = chosen
        elif active:
            warnings.append("المورد المختار يدوي مش متاح للصنف ده، فاتحسب على الأرخص.")
    units: Dict[str, Any] = {}
    for unit in UNITS:
        key = "price_" + unit
        candidates = [o for o in pool if o.get(key) is not None]
        if not candidates:
            units[unit] = None
            continue
        best = min(candidates, key=lambda o: (o[key], o["supplier_id"]))
        units[unit] = {
            "offer_id": best["id"],
            "supplier_id": best["supplier_id"],
            "supplier_alias": best.get("supplier_alias", ""),
            "cost": best[key],
            "pieces": _pieces(unit, best, product),
        }
    return {"units": units, "warnings": warnings, "active_offers": len(active)}


def price_product(
    product: Dict[str, Any],
    offers: List[Dict[str, Any]],
    tier: Dict[str, Any],
    settings: Dict[str, str],
    tier_prices: Optional[Dict[int, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Full price breakdown of one product for one customer tier."""
    sources = choose_sources(product, offers)
    fixed_for_tier = (tier_prices or {}).get(tier.get("id"), {})
    tier_margin = tier.get("margin_percent")
    fallback_margin = global_margin(settings) if tier_margin is None else float(tier_margin)
    units: Dict[str, Any] = {}
    warnings = list(sources["warnings"])
    for unit in UNITS:
        src = sources["units"][unit]
        cost = src["cost"] if src else None
        pieces = src["pieces"] if src else _pieces(unit, None, product)
        raw: Optional[float] = None
        rule = ""
        if unit in fixed_for_tier:
            raw, rule = fixed_for_tier[unit], "tier_fixed"
        elif product.get("fixed_price_" + unit) is not None:
            raw, rule = product["fixed_price_" + unit], "product_fixed"
        elif cost is not None and product.get("margin_percent") is not None:
            raw, rule = apply_margin(cost, product["margin_percent"]), "product_margin"
        elif cost is not None:
            raw = apply_margin(cost, fallback_margin)
            rule = "global_margin" if tier_margin is None else "tier_margin"
        if raw is None or cost is None:
            # No supplier sells this unit, so it cannot be ordered.
            units[unit] = None
            continue
        price = round_up_half(raw)
        below_cost = price < cost
        if below_cost:
            warnings.append(f"سعر ال{UNIT_LABELS[unit]} ({price:.2f}) أقل من سعر الشراء ({cost:.2f}).")
        units[unit] = {
            "price": price,
            "cost": cost,
            "rule": rule,
            "pieces": pieces,
            "per_piece": round(price / pieces, 2) if pieces else None,
            "supplier_id": src["supplier_id"],
            "supplier_alias": src["supplier_alias"],
            "below_cost": below_cost,
        }
    piece = units.get("piece")
    for unit in ("dozen", "carton"):
        info = units.get(unit)
        if info and piece and info["per_piece"]:
            saving = (1 - info["per_piece"] / piece["price"]) * 100 if piece["price"] else 0
            info["saving_percent"] = round(saving) if saving >= 1 else 0
        elif info:
            info["saving_percent"] = 0
    if units.get("carton") and not units["carton"]["pieces"]:
        warnings.append("عدد القطع في الكرتونة مش متسجل، فسعر القطعة جوه الكرتونة مش هيظهر.")
    sellable = any(units.values())
    return {
        "tier_id": tier.get("id"),
        "tier_name": tier.get("name", ""),
        "units": units,
        "warnings": warnings,
        "active_offers": sources["active_offers"],
        "available": bool(product.get("is_visible")) and sources["active_offers"] > 0 and sellable,
    }


def prices_for_customer(
    conn: sqlite3.Connection, customer_id: int, product_ids: Optional[List[int]] = None
) -> Dict[int, Dict[str, Any]]:
    """Prices a trader sees: only their own tier, only available products.

    The result deliberately carries no supplier, cost or tier details.
    """
    tier = tier_for_customer(conn, customer_id)
    settings = get_settings(conn)
    sql = "SELECT * FROM ws_products WHERE is_visible = 1"
    params: List[Any] = []
    if product_ids:
        sql += " AND id IN (%s)" % ",".join("?" * len(product_ids))
        params = list(product_ids)
    products = [dict(row) for row in conn.execute(sql, params)]
    ids = [p["id"] for p in products]
    offers = load_offers(conn, ids)
    tier_prices = load_tier_prices(conn, ids)
    result: Dict[int, Dict[str, Any]] = {}
    for product in products:
        priced = price_product(product, offers.get(product["id"], []), tier, settings, tier_prices.get(product["id"]))
        if not priced["available"]:
            continue
        result[product["id"]] = {
            unit: (
                {
                    "price": info["price"],
                    "pieces": info["pieces"],
                    "per_piece": info["per_piece"],
                    "saving_percent": info.get("saving_percent", 0),
                }
                if info
                else None
            )
            for unit, info in priced["units"].items()
        }
    return result
