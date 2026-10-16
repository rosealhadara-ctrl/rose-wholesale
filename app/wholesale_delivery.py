"""Wholesale phase 6 (section ٥): delivery zones, drivers, trips, cash from drivers.

A trip
    ready             prepared, waiting for a driver
    (assigned)        the admin picked a driver; a 4-digit pickup code is made
    out_for_delivery  the driver pressed "خرجت للتوصيل": the trader gets the
                      driver's name, a call button and the pickup code
    (arrived)         the driver pressed "وصلت": the trader is told
    delivered         the driver typed the trader's code (and confirmed the
                      cash when paying cash); points / credit as in phase 4
    failed            "العميل مش موجود": the order goes back to ready and the
                      admin decides (another driver, another time)
Drivers hand the cash to the store; the admin records "استلمت الكاش".
The live map is postponed (section ١٣، المرحلة 6).
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .db import utc_now
from .wholesale_service import WholesaleError, clean_text
from . import wholesale_orders as wo
from . import wholesale_payments as wp

MAX_CODE_ATTEMPTS = 5
DRIVER_ROLES = ("driver", "both")
STAFF_ROLES = ("picker", "driver", "both")
TRIP_LABEL = {
    "assigned": "متسلّم للمندوب",
    "out": "خرج للتوصيل",
    "arrived": "المندوب وصل",
    "failed": "ماتسلّمش",
    "delivered": "اتسلّم",
}


# ---------------------------------------------------------------------- zones


def list_zones(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT z.*, (SELECT COUNT(*) FROM ws_traders t WHERE t.zone_id = z.id) AS traders
            FROM ws_delivery_zones z ORDER BY z.is_active DESC, z.sort_order, z.name
            """
        )
    ]


def save_zone(conn: sqlite3.Connection, data: Dict[str, Any], zone_id: Optional[int] = None) -> List[Dict[str, Any]]:
    now = utc_now()
    name = clean_text(data.get("name"), 80) if data.get("name") is not None else None
    fee = data.get("fee")
    minimum = data.get("min_order")
    for value in (fee, minimum):
        if value is not None and (float(value) < 0 or float(value) > 1_000_000):
            raise WholesaleError("فيه رقم برّه الحدود.")
    try:
        if zone_id is None:
            if not name:
                raise WholesaleError("اكتب اسم المنطقة.")
            conn.execute(
                "INSERT INTO ws_delivery_zones(name, fee, min_order, is_active, created_at, updated_at) VALUES(?, ?, ?, 1, ?, ?)",
                (name, float(fee or 0), float(minimum or 0), now, now),
            )
        else:
            row = conn.execute("SELECT * FROM ws_delivery_zones WHERE id = ?", (zone_id,)).fetchone()
            if not row:
                raise WholesaleError("المنطقة مش موجودة.", 404)
            conn.execute(
                "UPDATE ws_delivery_zones SET name = ?, fee = ?, min_order = ?, is_active = ?, updated_at = ? WHERE id = ?",
                (
                    name or row["name"],
                    float(fee) if fee is not None else row["fee"],
                    float(minimum) if minimum is not None else row["min_order"],
                    (1 if data["is_active"] else 0) if data.get("is_active") is not None else row["is_active"],
                    now,
                    zone_id,
                ),
            )
    except sqlite3.IntegrityError:
        raise WholesaleError("فيه منطقة بنفس الاسم.") from None
    return list_zones(conn)


def set_trader_delivery(conn: sqlite3.Connection, customer_id: int, zone_id: Optional[int] = None,
                        address: Optional[str] = None) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_traders WHERE customer_id = ?", (customer_id,)).fetchone()
    if not row:
        raise WholesaleError("التاجر مش مسجّل في الجملة.", 404)
    if zone_id is not None and zone_id != 0:
        if not conn.execute("SELECT 1 FROM ws_delivery_zones WHERE id = ?", (zone_id,)).fetchone():
            raise WholesaleError("المنطقة مش موجودة.", 404)
    conn.execute(
        "UPDATE ws_traders SET zone_id = ?, address = ? WHERE customer_id = ?",
        (
            (zone_id or None) if zone_id is not None else row["zone_id"],
            clean_text(address, 300) if address is not None else row["address"],
            customer_id,
        ),
    )
    return dict(conn.execute("SELECT customer_id, zone_id, address, area FROM ws_traders WHERE customer_id = ?", (customer_id,)).fetchone())


# -------------------------------------------------------------------- drivers


def drivers(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    rows = []
    for d in conn.execute(
        "SELECT id, name, phone, role, is_active FROM ws_pickers WHERE role IN ('driver', 'both') ORDER BY is_active DESC, name"
    ):
        cash = conn.execute(
            "SELECT COALESCE(SUM(cash_collected), 0) AS amount, COUNT(*) AS orders FROM ws_orders WHERE driver_id = ? AND status = 'delivered' AND settlement_id IS NULL AND cash_collected > 0",
            (d["id"],),
        ).fetchone()
        rows.append({
            **dict(d),
            "is_active": bool(d["is_active"]),
            "cash_on_hand": round(cash["amount"], 2),
            "cash_orders": cash["orders"],
            "active_trips": conn.execute(
                "SELECT COUNT(*) FROM ws_orders WHERE driver_id = ? AND status IN ('ready', 'out_for_delivery') AND delivery_status IN ('assigned', 'out', 'arrived')",
                (d["id"],),
            ).fetchone()[0],
            "delivered_today": conn.execute(
                "SELECT COUNT(*) FROM ws_orders WHERE driver_id = ? AND status = 'delivered' AND delivered_at >= ?", (d["id"], today)
            ).fetchone()[0],
        })
    return rows


def _driver(conn: sqlite3.Connection, driver_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_pickers WHERE id = ?", (driver_id,)).fetchone()
    if not row or row["role"] not in DRIVER_ROLES:
        raise WholesaleError("المندوب مش موجود.", 404)
    if not row["is_active"]:
        raise WholesaleError("المندوب ده موقوف.")
    return dict(row)


def settle_cash(conn: sqlite3.Connection, driver_id: int, user_id: Optional[int], note: str = "") -> Dict[str, Any]:
    _driver(conn, driver_id)
    rows = conn.execute(
        "SELECT id, cash_collected FROM ws_orders WHERE driver_id = ? AND status = 'delivered' AND settlement_id IS NULL AND cash_collected > 0",
        (driver_id,),
    ).fetchall()
    if not rows:
        raise WholesaleError("مفيش كاش مع المندوب ده.")
    amount = round(sum(r["cash_collected"] for r in rows), 2)
    settlement_id = conn.execute(
        "INSERT INTO ws_driver_settlements(driver_id, amount, orders, note, created_by, created_at) VALUES(?, ?, ?, ?, ?, ?)",
        (driver_id, amount, len(rows), clean_text(note, 200), user_id, utc_now()),
    ).lastrowid
    conn.execute(
        "UPDATE ws_orders SET settlement_id = ? WHERE id IN (%s)" % ",".join("?" * len(rows)),
        [settlement_id] + [r["id"] for r in rows],
    )
    return {"id": settlement_id, "amount": amount, "orders": len(rows)}


# ---------------------------------------------------------------------- trips


def _cash_due(conn: sqlite3.Connection, order: Dict[str, Any]) -> float:
    if order["payment_method"] != "cash":
        return 0.0
    return round(max(0.0, float(order["grand_total"]) - float(order["paid_amount"] or 0)), 2)


def board(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Everything the admin's delivery tab shows."""
    rows = conn.execute(
        """
        SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, t.shop_name, z.name AS zone_name, d.name AS driver_name
        FROM ws_orders o JOIN customers c ON c.id = o.customer_id
        LEFT JOIN ws_traders t ON t.customer_id = o.customer_id
        LEFT JOIN ws_delivery_zones z ON z.id = o.zone_id
        LEFT JOIN ws_pickers d ON d.id = o.driver_id
        WHERE o.status IN ('ready', 'out_for_delivery')
        ORDER BY z.name, o.id
        """
    ).fetchall()
    waiting, active, failed = [], [], []
    for r in rows:
        o = dict(r)
        view = wp.payment_view(conn, o)
        item = {
            "id": o["id"],
            "number": wo.order_number(o["id"]),
            "shop": o["shop_name"] or o["customer_name"],
            "phone": o["customer_phone"],
            "zone": o["zone_name"] or "",
            "address": o["delivery_address"],
            "grand": o["grand_total"],
            "payment_label": view["method_label"] + " · " + view["status_label"],
            "cash_due": _cash_due(conn, o),
            "blocked": "التاجر لسه مادفعش التحويل" if o["payment_method"] in wp.TRANSFER_METHODS and o["payment_status"] != "paid" else "",
            "driver_id": o["driver_id"],
            "driver_name": o["driver_name"] or "",
            "delivery_status": o["delivery_status"],
            "status_label": TRIP_LABEL.get(o["delivery_status"], ""),
            "code": o["delivery_code"],
            "eta": o["delivery_eta"],
            "note": o["delivery_note"],
            "code_attempts": o["code_attempts"],
        }
        if o["delivery_status"] == "failed":
            failed.append(item)
        elif o["delivery_status"] in ("assigned", "out", "arrived"):
            active.append(item)
        else:
            waiting.append(item)
    return {"waiting": waiting, "active": active, "failed": failed, "drivers": drivers(conn), "zones": list_zones(conn)}


def assign(conn: sqlite3.Connection, order_id: int, driver_id: int, eta: str = "") -> Dict[str, Any]:
    order = wo._order(conn, order_id)
    if order["status"] != "ready":
        raise WholesaleError("الطلب لازم يكون جاهز الأول." if order["status"] != "out_for_delivery" else "الطلب خرج خلاص. لو عايز تغيّر المندوب، رجّعه الأول.")
    _driver(conn, driver_id)
    code = order["delivery_code"] or f"{secrets.randbelow(9000) + 1000}"
    conn.execute(
        """
        UPDATE ws_orders SET driver_id = ?, delivery_status = 'assigned', delivery_code = ?, delivery_eta = ?,
            delivery_note = '', code_attempts = 0, assigned_at = ?, updated_at = ? WHERE id = ?
        """,
        (driver_id, code, clean_text(eta, 60), utc_now(), utc_now(), order_id),
    )
    return {"id": order_id, "code": code}


def unassign(conn: sqlite3.Connection, order_id: int) -> None:
    order = wo._order(conn, order_id)
    if order["status"] not in ("ready", "out_for_delivery"):
        raise WholesaleError("الطلب ده مش في التوصيل.")
    conn.execute(
        "UPDATE ws_orders SET status = 'ready', driver_id = NULL, delivery_status = '', out_at = NULL, arrived_at = NULL, updated_at = ? WHERE id = ?",
        (utc_now(), order_id),
    )


def driver_trips(conn: sqlite3.Connection, driver_id: int) -> Dict[str, Any]:
    rows = conn.execute(
        """
        SELECT o.*, c.name AS customer_name, c.phone AS customer_phone, t.shop_name, z.name AS zone_name
        FROM ws_orders o JOIN customers c ON c.id = o.customer_id
        LEFT JOIN ws_traders t ON t.customer_id = o.customer_id
        LEFT JOIN ws_delivery_zones z ON z.id = o.zone_id
        WHERE o.driver_id = ? AND o.status IN ('ready', 'out_for_delivery') AND o.delivery_status IN ('assigned', 'out', 'arrived')
        ORDER BY o.delivery_status = 'arrived' DESC, o.delivery_status = 'out' DESC, o.assigned_at
        """,
        (driver_id,),
    ).fetchall()
    me = next((d for d in drivers(conn) if d["id"] == driver_id), {"cash_on_hand": 0, "cash_orders": 0, "delivered_today": 0})
    return {
        "trips": [trip_view(conn, dict(r)) for r in rows],
        "cash_on_hand": me["cash_on_hand"],
        "cash_orders": me["cash_orders"],
        "delivered_today": me["delivered_today"],
    }


def trip_view(conn: sqlite3.Connection, o: Dict[str, Any]) -> Dict[str, Any]:
    if "shop_name" not in o:
        extra = conn.execute(
            """
            SELECT c.name AS customer_name, c.phone AS customer_phone, t.shop_name, z.name AS zone_name
            FROM customers c LEFT JOIN ws_traders t ON t.customer_id = c.id LEFT JOIN ws_delivery_zones z ON z.id = ?
            WHERE c.id = ?
            """,
            (o["zone_id"], o["customer_id"]),
        ).fetchone()
        o.update(dict(extra))
    items = conn.execute(
        "SELECT COUNT(*) FROM ws_order_items WHERE order_id = ? AND COALESCE(picked_qty, received_qty, confirmed_qty, qty) > 0", (o["id"],)
    ).fetchone()[0]
    return {
        "id": o["id"],
        "number": wo.order_number(o["id"]),
        "shop": o["shop_name"] or o["customer_name"],
        "phone": o["customer_phone"],
        "zone": o.get("zone_name") or "",
        "address": o["delivery_address"],
        "items": items,
        "eta": o["delivery_eta"],
        "delivery_status": o["delivery_status"],
        "cash_due": _cash_due(conn, o),
        "payment_method": o["payment_method"],
        "payment_label": {"cash": "كاش عند الاستلام", "credit": "على حساب التاجر", "instapay": "مدفوع تحويل", "wallet": "مدفوع تحويل"}[o["payment_method"]],
        "code_locked": o["code_attempts"] >= MAX_CODE_ATTEMPTS,
    }


def _trip(conn: sqlite3.Connection, order_id: int, driver_id: int) -> Dict[str, Any]:
    order = wo._order(conn, order_id)
    if order["driver_id"] != driver_id or order["status"] not in ("ready", "out_for_delivery") or order["delivery_status"] not in ("assigned", "out", "arrived"):
        raise WholesaleError("المشوار ده مش معاك.", 409)
    return order


def go_out(conn: sqlite3.Connection, order_id: int, driver_id: int) -> Dict[str, Any]:
    order = _trip(conn, order_id, driver_id)
    if order["payment_method"] in wp.TRANSFER_METHODS and order["payment_status"] != "paid":
        raise WholesaleError("التاجر لسه مادفعش التحويل. كلّم الإدارة.")
    if order["delivery_status"] == "assigned":
        now = utc_now()
        conn.execute(
            "UPDATE ws_orders SET status = 'out_for_delivery', delivery_status = 'out', out_at = ?, updated_at = ? WHERE id = ?",
            (now, now, order_id),
        )
        driver = _driver(conn, driver_id)
        cash = _cash_due(conn, order)
        body = f"المندوب {driver['name']} ({driver['phone']}) في الطريق ليك."
        if order["delivery_eta"]:
            body += f" الوقت المتوقع: {order['delivery_eta']}."
        body += f" كود الاستلام: {order['delivery_code']}، اديه للمندوب لما يوصل."
        if cash:
            body += f" جهّز {wo.money(cash)} ج كاش."
        wo.notify(conn, order["customer_id"], "delivery", f"طلبك رقم {wo.order_number(order_id)} خرج للتوصيل", body, order_id=order_id)
    return trip_view(conn, dict(conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order_id,)).fetchone()))


def arrived(conn: sqlite3.Connection, order_id: int, driver_id: int) -> Dict[str, Any]:
    order = _trip(conn, order_id, driver_id)
    if order["delivery_status"] == "assigned":
        go_out(conn, order_id, driver_id)
    if order["delivery_status"] != "arrived":
        now = utc_now()
        conn.execute("UPDATE ws_orders SET delivery_status = 'arrived', arrived_at = ?, updated_at = ? WHERE id = ?", (now, now, order_id))
        wo.notify(conn, order["customer_id"], "delivery", "المندوب وصل", f"اديله كود الاستلام لطلب {wo.order_number(order_id)}.", order_id=order_id)
    return trip_view(conn, dict(conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order_id,)).fetchone()))


def deliver(conn: sqlite3.Connection, order_id: int, driver_id: int, code: str, cash_received: Optional[float]) -> Dict[str, Any]:
    order = _trip(conn, order_id, driver_id)
    if order["delivery_status"] != "arrived":
        raise WholesaleError("دوس «وصلت» الأول.")
    if order["code_attempts"] >= MAX_CODE_ATTEMPTS:
        raise WholesaleError("الكود اتكتب غلط كذا مرة. كلّم الإدارة.", 423)
    code = re.sub(r"\D", "", str(code or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    if code != order["delivery_code"]:
        conn.execute("UPDATE ws_orders SET code_attempts = code_attempts + 1 WHERE id = ?", (order_id,))
        conn.commit()  # keep the wrong try: the error below rolls back the rest of the request
        left = MAX_CODE_ATTEMPTS - order["code_attempts"] - 1
        raise WholesaleError(f"الكود غلط. فاضل {left} محاولات." if left > 0 else "الكود غلط. كلّم الإدارة.", 400)
    due = _cash_due(conn, order)
    if due:
        if cash_received is None or abs(float(cash_received) - due) > 0.009:
            raise WholesaleError(f"لازم تستلم {wo.money(due)} ج كاش وتأكد.")
    wp.mark_delivered(conn, order_id, None)
    conn.execute(
        "UPDATE ws_orders SET delivery_status = 'delivered', cash_collected = ? WHERE id = ?",
        (due, order_id),
    )
    return {"id": order_id, "number": wo.order_number(order_id), "cash": due}


def fail(conn: sqlite3.Connection, order_id: int, driver_id: int, reason: str) -> Dict[str, Any]:
    order = _trip(conn, order_id, driver_id)
    reason = clean_text(reason, 200) or "العميل مش موجود"
    now = utc_now()
    conn.execute(
        "UPDATE ws_orders SET status = 'ready', delivery_status = 'failed', delivery_note = ?, updated_at = ? WHERE id = ?",
        (reason, now, order_id),
    )
    wo.notify(conn, order["customer_id"], "delivery", f"ماقدرناش نسلّم طلبك رقم {wo.order_number(order_id)}",
              f"{reason}. هنكلّمك نحدد ميعاد تاني.", order_id=order_id)
    return {"id": order_id}


def delivery_for_customer(conn: sqlite3.Connection, order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What the trader sees on the order page while it is out for delivery."""
    if order["status"] != "out_for_delivery" or not order["driver_id"]:
        return None
    driver = conn.execute("SELECT name, phone FROM ws_pickers WHERE id = ?", (order["driver_id"],)).fetchone()
    return {
        "driver_name": driver["name"] if driver else "",
        "driver_phone": driver["phone"] if driver else "",
        "eta": order["delivery_eta"],
        "code": order["delivery_code"],
        "arrived": order["delivery_status"] == "arrived",
        "cash_due": _cash_due(conn, order),
    }
