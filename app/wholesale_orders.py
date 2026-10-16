"""Wholesale phase 3 (section ١٥): traders, orders, purchase orders, shortages.

Flow
1. An approved trader sends ONE order.  Prices are locked at that moment.
2. The order is split into one purchase order (PO) per supplier, numbered from
   the order number (1052-أ, 1052-ب).  The trader never sees the split.
3. The admin sends each PO to its supplier (WhatsApp or print) and records the
   reply per item: available / fewer / unavailable.
4. Confirming the review removes what is missing from the trader's invoice,
   recalculates the total and records the missing quantity as shortages.
5. Suppliers deliver to Rose.  Each PO is received on its own (arrived / short).
   Anything short is also removed and recorded as a shortage.
6. When every PO is received the order is "received" and waits for preparation
   (phase 5) and delivery (phase 6), as one shipment with one invoice.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Dict, List, Optional

from .db import utc_now
from .wholesale_pricing import (
    UNIT_LABELS,
    UNITS,
    get_settings,
    load_offers,
    load_tier_prices,
    price_product,
    tier_for_customer,
)
from .wholesale_service import WholesaleError, clean_text, normalize_name
from . import wholesale_payments as wp

SUFFIXES = "أبجدهوزحطيكلمنسعفصقرشتثخذضظغ"
ORDER_NUMBER_BASE = 1000
OPEN_STATUSES = ("submitted", "reviewed", "received", "preparing", "ready", "out_for_delivery")
CUSTOMER_STATUS = {
    "submitted": "وصلنا طلبك وبنراجعه",
    "reviewed": "اتراجع وبنجهّزه",
    "received": "بنجهّز طلبك",
    "preparing": "جاري تجهيز طلبك",
    "ready": "طلبك جاهز",
    "out_for_delivery": "خرج للتوصيل",
    "delivered": "اتسلّم",
    "cancelled": "اتلغى",
}
ADMIN_STATUS = {
    "submitted": "محتاج مراجعة",
    "reviewed": "مستني البضاعة من الموردين",
    "received": "البضاعة وصلت، مستني التجهيز",
    "preparing": "بيتجهز دلوقتي",
    "ready": "اتجهز، جاهز للتسليم",
    "out_for_delivery": "خرج للتوصيل",
    "delivered": "اتسلّم",
    "cancelled": "اتلغى",
}
MAX_ITEMS = 300
MAX_QTY = 9999


def order_number(order_id: int) -> int:
    return ORDER_NUMBER_BASE + int(order_id)


def money(value: float) -> str:
    return f"{value:,.2f}"


def notify(conn: sqlite3.Connection, customer_id: int, kind: str, title: str, body: str = "",
           order_id: Optional[int] = None, product_id: Optional[int] = None) -> None:
    conn.execute(
        "INSERT INTO ws_notifications(customer_id, kind, title, body, order_id, product_id, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (customer_id, kind, title[:200], body[:1500], order_id, product_id, utc_now()),
    )


# --------------------------------------------------------------------- traders


def trader_status(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_traders WHERE customer_id = ?", (customer_id,)).fetchone()
    return dict(row) if row else {"customer_id": customer_id, "status": "none"}


def require_trader(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    trader = trader_status(conn, customer_id)
    if trader["status"] != "approved":
        messages = {
            "none": "لازم تطلب الانضمام للجملة الأول.",
            "pending": "طلب انضمامك للجملة لسه بيتراجع.",
            "rejected": "طلب انضمامك للجملة ماتقبلش. كلّمنا لو محتاج تفاصيل.",
            "suspended": "حسابك في الجملة واقف مؤقتًا. كلّمنا.",
        }
        raise WholesaleError(messages.get(trader["status"], "مش مسموح."), 403)
    return trader


def apply_as_trader(conn: sqlite3.Connection, customer_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    shop = clean_text(data.get("shop_name"), 120)
    area = clean_text(data.get("area"), 120)
    address = clean_text(data.get("address"), 300)
    if not shop:
        raise WholesaleError("اكتب اسم المحل.")
    if not area:
        raise WholesaleError("اكتب المنطقة.")
    current = trader_status(conn, customer_id)
    if current["status"] in ("approved", "suspended"):
        raise WholesaleError("إنت مسجّل في الجملة خلاص.")
    now = utc_now()
    conn.execute(
        """
        INSERT INTO ws_traders(customer_id, status, shop_name, area, address, note, created_at)
        VALUES(?, 'pending', ?, ?, ?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET status = 'pending', shop_name = excluded.shop_name,
            area = excluded.area, address = excluded.address, note = excluded.note, created_at = excluded.created_at,
            decided_at = NULL, decided_by = NULL
        """,
        (customer_id, shop, area, address, clean_text(data.get("note"), 400), now),
    )
    return trader_status(conn, customer_id)


def list_trader_accounts(conn: sqlite3.Connection, status: str = "pending", q: str = "") -> List[Dict[str, Any]]:
    """Traders by status, or (with q and status 'search') any Rose customer."""
    default = conn.execute("SELECT id, name FROM ws_customer_tiers ORDER BY is_default DESC, sort_order, id LIMIT 1").fetchone()
    params: List[Any] = []
    if status == "search":
        sql = """
            SELECT c.id AS customer_id, c.name, c.phone, COALESCE(t.status, 'none') AS status,
                   t.shop_name, t.area, t.note, t.created_at, tt.tier_id, ct.name AS tier_name
            FROM customers c
            LEFT JOIN ws_traders t ON t.customer_id = c.id
            LEFT JOIN ws_trader_tiers tt ON tt.customer_id = c.id
            LEFT JOIN ws_customer_tiers ct ON ct.id = tt.tier_id
            WHERE c.status = 'active'
        """
    else:
        sql = """
            SELECT c.id AS customer_id, c.name, c.phone, t.status, t.shop_name, t.area, t.note,
                   t.address, t.zone_id, t.created_at, t.decided_at, tt.tier_id, ct.name AS tier_name,
                   (SELECT COUNT(*) FROM ws_orders o WHERE o.customer_id = c.id AND o.status != 'cancelled') AS orders
            FROM ws_traders t
            JOIN customers c ON c.id = t.customer_id
            LEFT JOIN ws_trader_tiers tt ON tt.customer_id = c.id
            LEFT JOIN ws_customer_tiers ct ON ct.id = tt.tier_id
            WHERE 1 = 1
        """
        if status in ("pending", "approved", "rejected", "suspended"):
            sql += " AND t.status = ?"
            params.append(status)
    if q:
        digits = re.sub(r"\D", "", q.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
        conds = ["c.name LIKE ?", "t.shop_name LIKE ?"]
        params += [f"%{q.strip()}%", f"%{q.strip()}%"]
        if digits:
            conds.append("c.phone LIKE ?")
            params.append(f"%{digits[-9:]}%")
        sql += " AND (" + " OR ".join(conds) + ")"
    elif status == "search":
        return []
    sql += " ORDER BY c.name LIMIT 100" if status == "search" else " ORDER BY t.created_at DESC LIMIT 300"
    rows = []
    for row in conn.execute(sql, params):
        item = dict(row)
        if item.get("tier_id") is None and default:
            item["tier_id"], item["tier_name"], item["is_default_tier"] = default["id"], default["name"], True
        rows.append(item)
    return rows


def decide_trader(conn: sqlite3.Connection, customer_id: int, status: str, tier_id: Optional[int], user_id: Optional[int]) -> Dict[str, Any]:
    if status not in ("approved", "rejected", "suspended"):
        raise WholesaleError("القرار مش صحيح.")
    if not conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
        raise WholesaleError("العميل مش موجود.", 404)
    current = trader_status(conn, customer_id)
    now = utc_now()
    if current["status"] == "none":
        conn.execute(
            "INSERT INTO ws_traders(customer_id, status, created_at, decided_at, decided_by) VALUES(?, ?, ?, ?, ?)",
            (customer_id, status, now, now, user_id),
        )
    else:
        conn.execute(
            "UPDATE ws_traders SET status = ?, decided_at = ?, decided_by = ? WHERE customer_id = ?",
            (status, now, user_id, customer_id),
        )
    if status == "approved" and tier_id:
        from .wholesale_service import set_trader_tier

        set_trader_tier(conn, customer_id, tier_id)
    if status == "approved" and current["status"] != "approved":
        notify(conn, customer_id, "account", "اتقبلت في روز جملة", "تقدر تطلب جملة دلوقتي بأسعار التجار.")
    elif status == "rejected" and current["status"] == "pending":
        notify(conn, customer_id, "account", "طلب الانضمام للجملة ماتقبلش", "كلّمنا لو محتاج تفاصيل.")
    return trader_status(conn, customer_id)


# --------------------------------------------------------------------- catalog


def catalog(conn: sqlite3.Connection, customer_id: int, q: str = "", section_id: Optional[int] = None,
            page: int = 1, per_page: int = 40, ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """Products the trader can order, priced for their tier only.

    No supplier, cost or tier information leaves this function.
    """
    tier = tier_for_customer(conn, customer_id)
    settings = get_settings(conn)
    where = [
        "p.is_visible = 1",
        "EXISTS (SELECT 1 FROM ws_offers o JOIN ws_suppliers s ON s.id = o.supplier_id "
        "WHERE o.product_id = p.id AND o.is_active = 1 AND s.is_active = 1)",
    ]
    params: List[Any] = []
    if q:
        key = normalize_name(q)
        where.append("(p.name_key LIKE ? OR p.code LIKE ?)")
        params += [f"%{key}%", f"%{q.strip()}%"]
    if section_id:
        where.append("p.section_id = ?")
        params.append(section_id)
    if ids is not None:
        if not ids:
            return {"items": [], "total": 0, "page": 1, "pages": 1}
        where.append("p.id IN (%s)" % ",".join("?" * len(ids)))
        params += list(ids)
    base = "FROM ws_products p LEFT JOIN ws_sections s ON s.id = p.section_id WHERE " + " AND ".join(where)
    total = conn.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
    per_page = max(10, min(per_page, 100))
    pages = max(1, -(-total // per_page))
    page = max(1, min(page, pages))
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT p.*, s.name AS section_name " + base + " ORDER BY s.sort_order, s.name, p.name_key LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        )
    ]
    pids = [r["id"] for r in rows]
    offers = load_offers(conn, pids)
    tier_prices = load_tier_prices(conn, pids)
    items = []
    for product in rows:
        priced = price_product(product, offers.get(product["id"], []), tier, settings, tier_prices.get(product["id"]))
        if not priced["available"]:
            continue
        units = {}
        for unit in UNITS:
            info = priced["units"][unit]
            units[unit] = (
                {"price": info["price"], "pieces": info["pieces"], "per_piece": info["per_piece"], "saving_percent": info.get("saving_percent", 0)}
                if info
                else None
            )
        image = product.get("image_path") or ""
        items.append({
            "id": product["id"],
            "name": product["name"],
            "section_id": product["section_id"],
            "section_name": product["section_name"] or "",
            "image_url": (image if image.startswith("http") else f"/api/wholesale/images/{image}") if image else "",
            "units": units,
        })
    return {"items": items, "total": total, "page": page, "pages": pages}


def trader_sections(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT s.id, s.name, COUNT(p.id) AS products FROM ws_sections s
            JOIN ws_products p ON p.section_id = s.id AND p.is_visible = 1
            WHERE EXISTS (SELECT 1 FROM ws_offers o WHERE o.product_id = p.id AND o.is_active = 1)
            GROUP BY s.id ORDER BY s.sort_order, s.name
            """
        )
    ]


# ---------------------------------------------------------------------- orders


def submit_order(conn: sqlite3.Connection, customer_id: int, items: List[Dict[str, Any]], note: str = "",
                 payment_method: str = "cash") -> Dict[str, Any]:
    require_trader(conn, customer_id)
    if not items:
        raise WholesaleError("السلة فاضية.")
    if len(items) > MAX_ITEMS:
        raise WholesaleError("الطلب فيه أصناف كتير. قسّمه على أكتر من طلب.")
    merged: Dict[tuple, Dict[str, Any]] = {}
    for raw in items:
        try:
            product_id, unit, qty = int(raw.get("product_id")), str(raw.get("unit")), int(raw.get("qty"))
        except (TypeError, ValueError):
            raise WholesaleError("فيه صنف في السلة مكتوب غلط.") from None
        if unit not in UNITS or qty < 1 or qty > MAX_QTY:
            raise WholesaleError("فيه كمية أو وحدة مش صحيحة.")
        key = (product_id, unit)
        line = merged.setdefault(key, {"product_id": product_id, "unit": unit, "qty": 0, "notes": []})
        line["qty"] = min(MAX_QTY, line["qty"] + qty)
        item_note = clean_text(raw.get("note"), 200)
        if item_note and item_note not in line["notes"]:
            line["notes"].append(item_note)

    tier = tier_for_customer(conn, customer_id)
    settings = get_settings(conn)
    pids = sorted({k[0] for k in merged})
    products = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM ws_products WHERE id IN (%s)" % ",".join("?" * len(pids)), pids)}
    offers = load_offers(conn, pids)
    tier_prices = load_tier_prices(conn, pids)
    lines = []
    problems = []
    for (product_id, unit), line in merged.items():
        product = products.get(product_id)
        if not product:
            problems.append("صنف اتشال من القايمة")
            continue
        priced = price_product(product, offers.get(product_id, []), tier, settings, tier_prices.get(product_id))
        info = priced["units"].get(unit) if priced["available"] else None
        if not info:
            problems.append(f"«{product['name']}» بال{UNIT_LABELS[unit]} مش متاح دلوقتي")
            continue
        offer = next((o for o in offers.get(product_id, []) if o["supplier_id"] == info["supplier_id"]), {})
        lines.append({
            "product_id": product_id,
            "supplier_id": info["supplier_id"],
            "name": product["name"],
            "supplier_item_name": offer.get("supplier_name", product["name"]),
            "supplier_code": offer.get("supplier_code", ""),
            "unit": unit,
            "pieces": info["pieces"],
            "qty": line["qty"],
            "unit_price": info["price"],
            "unit_cost": info["cost"],
            "note": " / ".join(line["notes"])[:300],
        })
    if problems:
        raise WholesaleError("لازم تشيل من السلة: " + "، ".join(problems[:5]), 409)

    now = utc_now()
    total = round(sum(l["qty"] * l["unit_price"] for l in lines), 2)
    pay = wp.validate_submit(conn, customer_id, payment_method, total)
    order_id = conn.execute(
        """
        INSERT INTO ws_orders(customer_id, status, tier_id, items_total, final_total, customer_note, created_at, updated_at,
            payment_method, payment_status, delivery_fee, payment_fee, fee_payer, grand_total)
        VALUES(?, 'submitted', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (customer_id, tier.get("id"), total, total, clean_text(note, 500), now, now,
         payment_method, wp.initial_payment_status(payment_method), pay["delivery"], pay["fee"], pay["fee_payer"], pay["grand"]),
    ).lastrowid
    if pay["is_plus"]:
        conn.execute(
            "UPDATE ws_orders SET is_plus = 1, plus_delivery_saved = ?, plus_fee_saved = ? WHERE id = ?",
            (pay["plus_delivery_saved"], pay["fee_saved"], order_id),
        )
    trader = conn.execute("SELECT zone_id, address, area FROM ws_traders WHERE customer_id = ?", (customer_id,)).fetchone()
    if trader:
        address = " - ".join(x for x in (trader["area"], trader["address"]) if x)
        conn.execute("UPDATE ws_orders SET zone_id = ?, delivery_address = ? WHERE id = ?", (trader["zone_id"], address, order_id))
    supplier_ids = sorted({l["supplier_id"] for l in lines})
    po_ids = {}
    for index, supplier_id in enumerate(supplier_ids):
        po_ids[supplier_id] = conn.execute(
            "INSERT INTO ws_purchase_orders(order_id, supplier_id, suffix, status, created_at) VALUES(?, ?, ?, 'pending', ?)",
            (order_id, supplier_id, SUFFIXES[index % len(SUFFIXES)] + ("" if index < len(SUFFIXES) else str(index // len(SUFFIXES))), now),
        ).lastrowid
    for l in lines:
        conn.execute(
            """
            INSERT INTO ws_order_items(order_id, purchase_order_id, product_id, supplier_id, name, supplier_item_name,
                supplier_code, unit, pieces, qty, unit_price, unit_cost, note, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, po_ids[l["supplier_id"]], l["product_id"], l["supplier_id"], l["name"], l["supplier_item_name"],
             l["supplier_code"], l["unit"], l["pieces"], l["qty"], l["unit_price"], l["unit_cost"], l["note"], now),
        )
    number = order_number(order_id)
    notify(conn, customer_id, "order", f"وصلنا طلبك رقم {number}",
           f"الإجمالي المبدئي {money(pay['grand'])} ج. هنراجع الطلب ونبلغك بالإجمالي النهائي.", order_id=order_id)
    return customer_order(conn, customer_id, order_id)


def _items(conn: sqlite3.Connection, order_id: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM ws_order_items WHERE order_id = ? ORDER BY purchase_order_id, id", (order_id,))]


def _final_qty(item: Dict[str, Any]) -> int:
    if item.get("picked_qty") is not None:
        return int(item["picked_qty"])
    if item["received_qty"] is not None:
        return int(item["received_qty"])
    if item["confirmed_qty"] is not None:
        return int(item["confirmed_qty"])
    return int(item["qty"])


def _order(conn: sqlite3.Connection, order_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise WholesaleError("الطلب مش موجود.", 404)
    return dict(row)


def customer_order(conn: sqlite3.Connection, customer_id: int, order_id: int) -> Dict[str, Any]:
    order = _order(conn, order_id)
    if order["customer_id"] != customer_id:
        raise WholesaleError("الطلب مش موجود.", 404)
    reviewed = order["status"] not in ("submitted",)
    items = []
    for item in _items(conn, order_id):
        final = _final_qty(item)
        state = "pending"
        if order["status"] == "cancelled":
            state = "cancelled"
        elif reviewed:
            state = "available" if final >= item["qty"] else ("partial" if final > 0 else "unavailable")
        items.append({
            "product_id": item["product_id"],
            "name": item["name"],
            "unit": item["unit"],
            "pieces": item["pieces"],
            "qty": item["qty"],
            "final_qty": final if reviewed else item["qty"],
            "unit_price": item["unit_price"],
            "line_total": round((final if reviewed else item["qty"]) * item["unit_price"], 2),
            "note": item["note"],
            "state": state,
        })
    return {
        "id": order["id"],
        "number": order_number(order["id"]),
        "status": order["status"],
        "status_label": CUSTOMER_STATUS.get(order["status"], order["status"]),
        "is_open": order["status"] in OPEN_STATUSES,
        "can_cancel": order["status"] == "submitted",
        "items_total": order["items_total"],
        "final_total": order["final_total"],
        "payment": wp.payment_view(conn, order),
        "delivery": _delivery_view(conn, order),
        "reviewed": reviewed,
        "customer_note": order["customer_note"],
        "cancel_reason": order["cancel_reason"],
        "created_at": order["created_at"],
        "items": items,
    }


def _delivery_view(conn: sqlite3.Connection, order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from .wholesale_delivery import delivery_for_customer

    return delivery_for_customer(conn, order)


def customer_orders(conn: sqlite3.Connection, customer_id: int, scope: str = "all") -> List[Dict[str, Any]]:
    sql = "SELECT * FROM ws_orders WHERE customer_id = ?"
    params: List[Any] = [customer_id]
    if scope == "open":
        sql += " AND status IN (%s)" % ",".join("?" * len(OPEN_STATUSES))
        params += list(OPEN_STATUSES)
    elif scope == "done":
        sql += " AND status IN ('delivered', 'cancelled')"
    sql += " ORDER BY id DESC LIMIT 100"
    result = []
    for order in conn.execute(sql, params):
        count = conn.execute("SELECT COUNT(*) FROM ws_order_items WHERE order_id = ?", (order["id"],)).fetchone()[0]
        result.append({
            "id": order["id"],
            "number": order_number(order["id"]),
            "status": order["status"],
            "status_label": CUSTOMER_STATUS.get(order["status"], order["status"]),
            "is_open": order["status"] in OPEN_STATUSES,
            "total": order["grand_total"],
            "payment_status": order["payment_status"],
            "needs_payment": wp.payment_view(conn, dict(order))["can_pay"],
            "items": count,
            "created_at": order["created_at"],
        })
    return result


def cancel_by_customer(conn: sqlite3.Connection, customer_id: int, order_id: int) -> Dict[str, Any]:
    order = _order(conn, order_id)
    if order["customer_id"] != customer_id:
        raise WholesaleError("الطلب مش موجود.", 404)
    if order["status"] != "submitted":
        raise WholesaleError("الطلب اتراجع خلاص ومينفعش يتلغي من هنا. كلّمنا.")
    _cancel(conn, order_id, "العميل لغاه")
    return customer_order(conn, customer_id, order_id)


def _cancel(conn: sqlite3.Connection, order_id: int, reason: str) -> None:
    now = utc_now()
    conn.execute(
        "UPDATE ws_orders SET status = 'cancelled', cancel_reason = ?, cancelled_at = ?, updated_at = ? WHERE id = ?",
        (clean_text(reason, 300), now, now, order_id),
    )
    conn.execute("UPDATE ws_purchase_orders SET status = 'cancelled' WHERE order_id = ? AND status != 'received'", (order_id,))


# ------------------------------------------------------------- admin: orders


def admin_orders(conn: sqlite3.Connection, status: str = "", q: str = "", page: int = 1) -> Dict[str, Any]:
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM ws_orders GROUP BY status")}
    sql = """
        SELECT o.*, c.name AS customer_name, c.phone, t.shop_name,
               (SELECT COUNT(*) FROM ws_order_items i WHERE i.order_id = o.id) AS items,
               (SELECT COUNT(*) FROM ws_order_items i WHERE i.order_id = o.id AND i.note != '') AS notes,
               (SELECT COUNT(*) FROM ws_purchase_orders p WHERE p.order_id = o.id) AS pos
        FROM ws_orders o JOIN customers c ON c.id = o.customer_id
        LEFT JOIN ws_traders t ON t.customer_id = o.customer_id
        WHERE 1 = 1
    """
    params: List[Any] = []
    if status == "open":
        sql += " AND o.status IN (%s)" % ",".join("?" * len(OPEN_STATUSES))
        params += list(OPEN_STATUSES)
    elif status:
        sql += " AND o.status = ?"
        params.append(status)
    if q:
        digits = re.sub(r"\D", "", q)
        conds = ["c.name LIKE ?", "t.shop_name LIKE ?"]
        params += [f"%{q.strip()}%", f"%{q.strip()}%"]
        if digits:
            conds += ["c.phone LIKE ?", "o.id = ?"]
            params += [f"%{digits[-9:]}%", int(digits) - ORDER_NUMBER_BASE]
        sql += " AND (" + " OR ".join(conds) + ")"
    total = conn.execute("SELECT COUNT(*) FROM (" + sql + ")", params).fetchone()[0]
    per_page = 50
    pages = max(1, -(-total // per_page))
    page = max(1, min(page, pages))
    rows = conn.execute(sql + " ORDER BY o.id DESC LIMIT ? OFFSET ?", params + [per_page, (page - 1) * per_page]).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["number"] = order_number(item["id"])
        item["status_label"] = ADMIN_STATUS.get(item["status"], item["status"])
        item["payment_label"] = wp.PAYMENT_LABEL.get(item["payment_status"], "")
        items.append(item)
    return {"counts": counts, "items": items, "total": total, "page": page, "pages": pages}


def _po_status(conn: sqlite3.Connection, po_id: int) -> None:
    """Move a PO to 'confirmed' once every item has the supplier's answer."""
    po = conn.execute("SELECT * FROM ws_purchase_orders WHERE id = ?", (po_id,)).fetchone()
    if not po or po["status"] in ("received", "cancelled"):
        return
    pending = conn.execute(
        "SELECT COUNT(*) FROM ws_order_items WHERE purchase_order_id = ? AND review_status = 'pending'", (po_id,)
    ).fetchone()[0]
    if pending == 0 and po["status"] != "confirmed":
        conn.execute("UPDATE ws_purchase_orders SET status = 'confirmed', confirmed_at = ? WHERE id = ?", (utc_now(), po_id))
    elif pending and po["status"] == "confirmed":
        conn.execute("UPDATE ws_purchase_orders SET status = ?, confirmed_at = NULL WHERE id = ?",
                     ("sent" if po["sent_at"] else "pending", po_id))


def admin_order(conn: sqlite3.Connection, order_id: int) -> Dict[str, Any]:
    order = _order(conn, order_id)
    customer = conn.execute(
        "SELECT c.id, c.name, c.phone, t.shop_name, t.area FROM customers c LEFT JOIN ws_traders t ON t.customer_id = c.id WHERE c.id = ?",
        (order["customer_id"],),
    ).fetchone()
    tier = conn.execute("SELECT name FROM ws_customer_tiers WHERE id = ?", (order["tier_id"],)).fetchone()
    items = _items(conn, order_id)
    pos = []
    for po in conn.execute(
        "SELECT p.*, s.alias, s.phone AS supplier_phone FROM ws_purchase_orders p JOIN ws_suppliers s ON s.id = p.supplier_id WHERE p.order_id = ? ORDER BY p.id",
        (order_id,),
    ):
        po_items = [i for i in items if i["purchase_order_id"] == po["id"]]
        lines = []
        for i in po_items:
            lines.append({**i, "final_qty": _final_qty(i), "line_total": round(_final_qty(i) * i["unit_price"], 2),
                          "line_cost": round(_final_qty(i) * i["unit_cost"], 2)})
        pos.append({
            **dict(po),
            "number": f"{order_number(order_id)}-{po['suffix']}",
            "items": lines,
            "cost_total": round(sum(l["line_cost"] for l in lines), 2),
            "message": po_message(order_id, dict(po), po_items),
            "whatsapp_phone": _wa_phone(po["supplier_phone"]),
        })
    final_total = round(sum(_final_qty(i) * i["unit_price"] for i in items), 2) if order["status"] != "submitted" else order["items_total"]
    picker = conn.execute("SELECT name FROM ws_pickers WHERE id = ?", (order.get("picker_id"),)).fetchone() if order.get("picker_id") else None
    driver = conn.execute("SELECT name FROM ws_pickers WHERE id = ?", (order.get("driver_id"),)).fetchone() if order.get("driver_id") else None
    return {
        **order,
        "picker_name": picker["name"] if picker else "",
        "driver_name": driver["name"] if driver else "",
        "number": order_number(order_id),
        "status_label": ADMIN_STATUS.get(order["status"], order["status"]),
        "customer": dict(customer) if customer else {},
        "tier_name": tier["name"] if tier else "",
        "purchase_orders": pos,
        "payment": wp.payment_view(conn, order),
        "pending_decisions": sum(1 for i in items if i["review_status"] == "pending"),
        "preview_total": round(sum((i["confirmed_qty"] if i["review_status"] != "pending" else i["qty"]) * i["unit_price"] for i in items), 2),
        "final_total": final_total,
        "cost_total": round(sum(_final_qty(i) * i["unit_cost"] for i in items), 2),
    }


def _wa_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("01") and len(digits) == 11:
        digits = "2" + digits
    return digits


def po_message(order_id: int, po: Dict[str, Any], items: List[Dict[str, Any]]) -> str:
    lines = [f"طلب شراء رقم {order_number(order_id)}-{po['suffix']} من روز", ""]
    for i in items:
        name = i["supplier_item_name"] or i["name"]
        code = f" (كود {i['supplier_code']})" if i["supplier_code"] else ""
        note = f" - ملاحظة: {i['note']}" if i["note"] else ""
        lines.append(f"- {name}{code}: {i['qty']} {UNIT_LABELS[i['unit']]}{note}")
    lines += ["", "ياريت تأكد المتوفر والناقص. شكرًا."]
    return "\n".join(lines)


def mark_po_sent(conn: sqlite3.Connection, po_id: int) -> None:
    po = conn.execute("SELECT * FROM ws_purchase_orders WHERE id = ?", (po_id,)).fetchone()
    if not po:
        raise WholesaleError("طلب الشراء مش موجود.", 404)
    if po["status"] == "pending":
        conn.execute("UPDATE ws_purchase_orders SET status = 'sent', sent_at = ? WHERE id = ?", (utc_now(), po_id))
    elif not po["sent_at"]:
        conn.execute("UPDATE ws_purchase_orders SET sent_at = ? WHERE id = ?", (utc_now(), po_id))


def set_item_decision(conn: sqlite3.Connection, order_id: int, item_id: int, decision: str, qty: Optional[int] = None) -> None:
    order = _order(conn, order_id)
    if order["status"] != "submitted":
        raise WholesaleError("المراجعة اتأكدت خلاص.")
    item = conn.execute("SELECT * FROM ws_order_items WHERE id = ? AND order_id = ?", (item_id, order_id)).fetchone()
    if not item:
        raise WholesaleError("الصنف مش موجود في الطلب.", 404)
    if decision == "available":
        confirmed = item["qty"]
    elif decision == "unavailable":
        confirmed = 0
    elif decision == "partial":
        if qty is None or qty < 1 or qty >= item["qty"]:
            raise WholesaleError(f"الكمية المتوفرة لازم تكون من 1 لحد {item['qty'] - 1}.")
        confirmed = int(qty)
    elif decision == "pending":
        confirmed = None
    else:
        raise WholesaleError("القرار مش صحيح.")
    conn.execute("UPDATE ws_order_items SET review_status = ?, confirmed_qty = ? WHERE id = ?", (decision, confirmed, item_id))
    _po_status(conn, item["purchase_order_id"])


def po_all_available(conn: sqlite3.Connection, order_id: int, po_id: int) -> None:
    order = _order(conn, order_id)
    if order["status"] != "submitted":
        raise WholesaleError("المراجعة اتأكدت خلاص.")
    conn.execute(
        "UPDATE ws_order_items SET review_status = 'available', confirmed_qty = qty WHERE order_id = ? AND purchase_order_id = ? AND review_status = 'pending'",
        (order_id, po_id),
    )
    _po_status(conn, po_id)


def _record_shortage(conn: sqlite3.Connection, order: Dict[str, Any], item: Dict[str, Any], missing: int, source: str) -> None:
    if missing <= 0:
        return
    conn.execute(
        """
        INSERT INTO ws_shortages(customer_id, product_id, name, unit, qty, source, order_id, status, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, 'waiting', ?)
        """,
        (order["customer_id"], item["product_id"], item["name"], item["unit"], missing, source, order["id"], utc_now()),
    )


def _missing_text(missing: List[str]) -> str:
    if not missing:
        return ""
    shown = "، ".join(missing[:6]) + ("، وغيرهم" if len(missing) > 6 else "")
    return f" مش متوفر: {shown}. اتسجلوا في النواقص وهنبلغك أول ما يتوفروا."


def confirm_review(conn: sqlite3.Connection, order_id: int) -> Dict[str, Any]:
    order = _order(conn, order_id)
    if order["status"] != "submitted":
        raise WholesaleError("الطلب ده اتراجع قبل كده.")
    items = _items(conn, order_id)
    pending = [i for i in items if i["review_status"] == "pending"]
    if pending:
        raise WholesaleError(f"لسه {len(pending)} صنف من غير قرار (متوفر أو لأ).")
    now = utc_now()
    missing = []
    for item in items:
        lack = item["qty"] - int(item["confirmed_qty"] or 0)
        if lack > 0:
            _record_shortage(conn, order, item, lack, "review")
            missing.append(f"{item['name']} ({lack} {UNIT_LABELS[item['unit']]})")
    total = round(sum(int(i["confirmed_qty"] or 0) * i["unit_price"] for i in items), 2)
    number = order_number(order_id)
    for po in conn.execute("SELECT id FROM ws_purchase_orders WHERE order_id = ?", (order_id,)).fetchall():
        has = conn.execute("SELECT COALESCE(SUM(confirmed_qty), 0) FROM ws_order_items WHERE purchase_order_id = ?", (po["id"],)).fetchone()[0]
        if not has:
            conn.execute("UPDATE ws_purchase_orders SET status = 'cancelled' WHERE id = ?", (po["id"],))
    if total <= 0:
        conn.execute("UPDATE ws_orders SET final_total = 0, reviewed_at = ?, updated_at = ? WHERE id = ?", (now, now, order_id))
        _cancel(conn, order_id, "كل الأصناف مش متوفرة")
        notify(conn, order["customer_id"], "order", f"طلبك رقم {number} اتلغى",
               "للأسف كل الأصناف مش متوفرة دلوقتي." + _missing_text(missing), order_id=order_id)
    else:
        conn.execute(
            "UPDATE ws_orders SET status = 'reviewed', final_total = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
            (total, now, now, order_id),
        )
        grand = wp.recalc_order(conn, order_id)["grand"]
        pay_note = " ادفع المبلغ من صفحة الطلب علشان نكمّل." if order["payment_method"] in wp.TRANSFER_METHODS else ""
        notify(conn, order["customer_id"], "order", f"طلبك رقم {number} اتراجع",
               f"الإجمالي النهائي {money(grand)} ج." + pay_note + _missing_text(missing), order_id=order_id)
    from .wholesale_team import after_review

    after_review(conn, order_id)  # tell the shop's employees which of their items were missing
    return admin_order(conn, order_id)


def receive_po(conn: sqlite3.Connection, po_id: int, received: Dict[str, int]) -> Dict[str, Any]:
    po = conn.execute("SELECT * FROM ws_purchase_orders WHERE id = ?", (po_id,)).fetchone()
    if not po:
        raise WholesaleError("طلب الشراء مش موجود.", 404)
    order = _order(conn, po["order_id"])
    if order["status"] != "reviewed":
        raise WholesaleError("الاستلام بيبدأ بعد تأكيد المراجعة." if order["status"] == "submitted" else "الطلب ده مش في مرحلة الاستلام.")
    if po["status"] in ("received", "cancelled"):
        raise WholesaleError("طلب الشراء ده اتقفل.")
    items = [dict(r) for r in conn.execute("SELECT * FROM ws_order_items WHERE purchase_order_id = ?", (po_id,))]
    now = utc_now()
    for item in items:
        expected = int(item["confirmed_qty"] or 0)
        value = received.get(str(item["id"]), expected)
        try:
            got = int(value)
        except (TypeError, ValueError):
            raise WholesaleError("فيه كمية مستلمة مكتوبة غلط.") from None
        if got < 0 or got > expected:
            raise WholesaleError(f"الكمية المستلمة من «{item['name']}» لازم تكون من 0 لحد {expected}.")
        conn.execute("UPDATE ws_order_items SET received_qty = ? WHERE id = ?", (got, item["id"]))
        _record_shortage(conn, order, item, expected - got, "receipt")
    conn.execute("UPDATE ws_purchase_orders SET status = 'received', received_at = ? WHERE id = ?", (now, po_id))
    remaining = conn.execute(
        "SELECT COUNT(*) FROM ws_purchase_orders WHERE order_id = ? AND status NOT IN ('received', 'cancelled')", (order["id"],)
    ).fetchone()[0]
    all_items = _items(conn, order["id"])
    total = round(sum(_final_qty(i) * i["unit_price"] for i in all_items), 2)
    conn.execute("UPDATE ws_orders SET final_total = ?, updated_at = ? WHERE id = ?", (total, now, order["id"]))
    grand = wp.recalc_order(conn, order["id"])["grand"]
    if remaining == 0:
        number = order_number(order["id"])
        short = [
            f"{i['name']} ({int(i['confirmed_qty'] or 0) - int(i['received_qty'] or 0)} {UNIT_LABELS[i['unit']]})"
            for i in all_items
            if i["received_qty"] is not None and int(i["received_qty"]) < int(i["confirmed_qty"] or 0)
        ]
        if total <= 0:
            _cancel(conn, order["id"], "البضاعة ماوصلتش من الموردين")
            notify(conn, order["customer_id"], "order", f"طلبك رقم {number} اتلغى",
                   "للأسف البضاعة ماوصلتش." + _missing_text(short), order_id=order["id"])
        else:
            # No message here: the trader gets one detailed message when picking ends.
            conn.execute("UPDATE ws_orders SET status = 'received', received_at = ?, updated_at = ? WHERE id = ?", (now, now, order["id"]))
    return admin_order(conn, order["id"])


def cancel_by_admin(conn: sqlite3.Connection, order_id: int, reason: str) -> Dict[str, Any]:
    order = _order(conn, order_id)
    if order["status"] not in ("submitted", "reviewed", "received"):
        raise WholesaleError("الطلب ده مينفعش يتلغي.")
    reason = clean_text(reason, 300) or "اتلغى من الإدارة"
    _cancel(conn, order_id, reason)
    notify(conn, order["customer_id"], "order", f"طلبك رقم {order_number(order_id)} اتلغى", reason, order_id=order_id)
    return admin_order(conn, order_id)


def admin_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "orders_to_review": one("SELECT COUNT(*) FROM ws_orders WHERE status = 'submitted'"),
        "orders_receiving": one("SELECT COUNT(*) FROM ws_orders WHERE status = 'reviewed'"),
        "pending_traders": one("SELECT COUNT(*) FROM ws_traders WHERE status = 'pending'"),
        "pending_payments": one("SELECT COUNT(*) FROM ws_payments WHERE status = 'pending'"),
        "returns_attention": one("SELECT COUNT(*) FROM ws_returns WHERE status IN ('requested', 'inspected')"),
        "feedback_open": one("SELECT COUNT(*) FROM ws_feedback WHERE status = 'open'"),
        "plus_requests": one("SELECT COUNT(*) FROM ws_plus_subscriptions WHERE status = 'requested'"),
        "chat_reports": one("SELECT COUNT(*) FROM ws_chat_reports WHERE status = 'open'"),
        "delivery_attention": one("SELECT COUNT(*) FROM ws_orders WHERE status = 'ready' AND (driver_id IS NULL OR delivery_status IN ('', 'failed'))"),
        "waiting_shortages": one("SELECT COUNT(DISTINCT COALESCE(product_id, -id)) FROM ws_shortages WHERE status = 'waiting'"),
    }


# ------------------------------------------------------------------ shortages


def customer_shortages(conn: sqlite3.Connection, customer_id: int) -> List[Dict[str, Any]]:
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM ws_shortages WHERE customer_id = ? AND status IN ('waiting', 'available') ORDER BY status = 'available' DESC, id DESC LIMIT 200",
            (customer_id,),
        )
    ]
    ids = sorted({r["product_id"] for r in rows if r["product_id"] and r["status"] == "available"})
    orderable = {i["id"]: i for i in catalog(conn, customer_id, ids=ids, per_page=100)["items"]} if ids else {}
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "product_id": r["product_id"],
            "name": r["name"],
            "unit": r["unit"],
            "qty": r["qty"],
            "source": r["source"],
            "status": r["status"],
            "note": r["note"],
            "created_at": r["created_at"],
            "order_number": order_number(r["order_id"]) if r["order_id"] else None,
            "product": orderable.get(r["product_id"]),
        })
    return result


def request_provision(conn: sqlite3.Connection, customer_id: int, name: str, note: str = "", unit: str = "piece", qty: int = 1) -> Dict[str, Any]:
    require_trader(conn, customer_id)
    name = clean_text(name, 150)
    if len(name) < 2:
        raise WholesaleError("اكتب اسم الصنف اللي عايزه.")
    unit = unit if unit in UNITS else "piece"
    qty = max(1, min(int(qty or 1), MAX_QTY))
    open_count = conn.execute(
        "SELECT COUNT(*) FROM ws_shortages WHERE customer_id = ? AND source = 'request' AND status = 'waiting'", (customer_id,)
    ).fetchone()[0]
    if open_count >= 50:
        raise WholesaleError("عندك طلبات توفير كتير مفتوحة.")
    conn.execute(
        "INSERT INTO ws_shortages(customer_id, product_id, name, unit, qty, source, note, status, created_at) VALUES(?, NULL, ?, ?, ?, 'request', ?, 'waiting', ?)",
        (customer_id, name, unit, qty, clean_text(note, 300), utc_now()),
    )
    return {"ok": True}


def close_own_shortage(conn: sqlite3.Connection, customer_id: int, shortage_id: int) -> None:
    cur = conn.execute("UPDATE ws_shortages SET status = 'closed' WHERE id = ? AND customer_id = ?", (shortage_id, customer_id))
    if not cur.rowcount:
        raise WholesaleError("مش موجود.", 404)


def admin_shortages(conn: sqlite3.Connection, supplier_id: Optional[int] = None) -> Dict[str, Any]:
    groups: Dict[int, Dict[str, Any]] = {}
    for r in conn.execute(
        """
        SELECT sh.*, p.name AS product_name, p.is_visible FROM ws_shortages sh
        JOIN ws_products p ON p.id = sh.product_id
        WHERE sh.status = 'waiting' AND sh.product_id IS NOT NULL ORDER BY sh.id
        """
    ):
        g = groups.setdefault(r["product_id"], {
            "product_id": r["product_id"], "name": r["product_name"], "is_visible": bool(r["is_visible"]),
            "customers": set(), "units": {}, "first_at": r["created_at"], "last_at": r["created_at"], "sources": set(),
        })
        g["customers"].add(r["customer_id"])
        g["units"][r["unit"]] = g["units"].get(r["unit"], 0) + r["qty"]
        g["last_at"] = r["created_at"]
        g["sources"].add(r["source"])
    offers = load_offers(conn, list(groups)) if groups else {}
    result = []
    for pid, g in groups.items():
        active = [o for o in offers.get(pid, []) if o["is_active"]]
        suppliers = [{"id": o["supplier_id"], "alias": o["supplier_alias"]} for o in active]
        if supplier_id and not any(s["id"] == supplier_id for s in suppliers):
            continue
        result.append({
            **{k: v for k, v in g.items() if k not in ("customers", "sources")},
            "customers": len(g["customers"]),
            "sources": sorted(g["sources"]),
            "suppliers": suppliers,
            "units_text": "، ".join(f"{q} {UNIT_LABELS[u]}" for u, q in g["units"].items()),
        })
    result.sort(key=lambda g: (-g["customers"], g["name"]))
    requests = []
    for r in conn.execute(
        """
        SELECT sh.*, c.name AS customer_name, c.phone, t.shop_name FROM ws_shortages sh
        JOIN customers c ON c.id = sh.customer_id LEFT JOIN ws_traders t ON t.customer_id = sh.customer_id
        WHERE sh.status = 'waiting' AND sh.product_id IS NULL ORDER BY sh.id DESC LIMIT 300
        """
    ):
        requests.append({**dict(r), "unit_label": UNIT_LABELS.get(r["unit"], "")})
    by_supplier: Dict[str, int] = {}
    for g in result:
        for s in g["suppliers"] or [{"alias": "بدون مورد"}]:
            by_supplier[s["alias"]] = by_supplier.get(s["alias"], 0) + 1
    return {"products": result, "requests": requests, "by_supplier": by_supplier}


def shortage_customers(conn: sqlite3.Connection, product_id: int) -> List[Dict[str, Any]]:
    return [
        {**dict(r), "unit_label": UNIT_LABELS.get(r["unit"], ""), "order_number": order_number(r["order_id"]) if r["order_id"] else None}
        for r in conn.execute(
            """
            SELECT sh.id, sh.unit, sh.qty, sh.source, sh.order_id, sh.created_at, c.name, c.phone, t.shop_name
            FROM ws_shortages sh JOIN customers c ON c.id = sh.customer_id LEFT JOIN ws_traders t ON t.customer_id = sh.customer_id
            WHERE sh.product_id = ? AND sh.status = 'waiting' ORDER BY sh.id
            """,
            (product_id,),
        )
    ]


def mark_available(conn: sqlite3.Connection, product_id: int) -> int:
    """'اتوفر، بلّغهم': notify every trader waiting for this product."""
    product = conn.execute("SELECT * FROM ws_products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        raise WholesaleError("الصنف مش موجود.", 404)
    rows = conn.execute(
        "SELECT * FROM ws_shortages WHERE product_id = ? AND status = 'waiting'", (product_id,)
    ).fetchall()
    if not rows:
        return 0
    now = utc_now()
    conn.execute("UPDATE ws_shortages SET status = 'available', notified_at = ? WHERE product_id = ? AND status = 'waiting'", (now, product_id))
    for customer_id in sorted({r["customer_id"] for r in rows}):
        notify(conn, customer_id, "available", f"«{product['name']}» اتوفر", "تقدر تطلبه دلوقتي من نواقصك أو من شاشة الجملة.", product_id=product_id)
    from .wholesale_team import after_available

    after_available(conn, product_id, [r["customer_id"] for r in rows])  # employees who were told it was missing
    return len({r["customer_id"] for r in rows})


def close_request(conn: sqlite3.Connection, shortage_id: int, product_id: Optional[int], message: str = "") -> None:
    row = conn.execute("SELECT * FROM ws_shortages WHERE id = ? AND product_id IS NULL", (shortage_id,)).fetchone()
    if not row:
        raise WholesaleError("طلب التوفير مش موجود.", 404)
    now = utc_now()
    if product_id:
        product = conn.execute("SELECT name FROM ws_products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            raise WholesaleError("الصنف مش موجود.", 404)
        conn.execute("UPDATE ws_shortages SET status = 'available', product_id = ?, notified_at = ? WHERE id = ?", (product_id, now, shortage_id))
        notify(conn, row["customer_id"], "available", f"«{product['name']}» بقى متوفر", "الصنف اللي طلبت توفيره موجود دلوقتي.", product_id=product_id)
    else:
        conn.execute("UPDATE ws_shortages SET status = 'closed', notified_at = ? WHERE id = ?", (now, shortage_id))
        if message:
            notify(conn, row["customer_id"], "request", f"بخصوص طلب توفير «{row['name']}»", clean_text(message, 400))


# ------------------------------------------------------------- notifications


def notifications(conn: sqlite3.Connection, customer_id: int, limit: int = 60) -> Dict[str, Any]:
    rows = [
        {**dict(r), "order_number": order_number(r["order_id"]) if r["order_id"] else None}
        for r in conn.execute(
            "SELECT id, kind, title, body, order_id, product_id, read_at, created_at FROM ws_notifications WHERE customer_id = ? ORDER BY id DESC LIMIT ?",
            (customer_id, limit),
        )
    ]
    return {"items": rows, "unread": unread_count(conn, customer_id)}


def unread_count(conn: sqlite3.Connection, customer_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM ws_notifications WHERE customer_id = ? AND read_at IS NULL", (customer_id,)).fetchone()[0]


def mark_all_read(conn: sqlite3.Connection, customer_id: int) -> None:
    conn.execute("UPDATE ws_notifications SET read_at = ? WHERE customer_id = ? AND read_at IS NULL", (utc_now(), customer_id))
