"""Wholesale phase 5 (section ٥): prep employees, picking and receiving from the phone.

Employees sign in at /pick/ with their mobile number and a 4-digit code.  The
owner adds them from the admin page and sends the link and code on WhatsApp.

Picking
    received   all supplier goods arrived, waiting for an employee
    preparing  an employee pressed "ابدأ التجهيز": the order is locked to them
               and the timer starts; the trader is told preparation started
    ready      every item marked ready / fewer / missing; the time is saved,
               missing quantity becomes a shortage, the totals are recomputed
               and the trader gets ONE message with all the details
The trader never sees item-by-item progress while the order is being prepared.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db import utc_now
from .wholesale_service import WholesaleError, clean_text
from . import wholesale_orders as wo
from . import wholesale_payments as wp

SESSION_COOKIE = "rose_picker_session"
SESSION_HOURS = 14
MAX_FAILED = 5
LOCK_MINUTES = 15
PBKDF2_ROUNDS = 120_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    value = datetime.fromisoformat(ts)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    if digits.startswith("0020"):
        digits = "0" + digits[4:]
    elif digits.startswith("20") and len(digits) == 12:
        digits = "0" + digits[2:]
    if not re.fullmatch(r"01\d{9}", digits):
        raise WholesaleError("رقم الموبايل لازم يكون 11 رقم ويبدأ بـ 01.")
    return digits


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def _new_pin() -> str:
    while True:
        pin = f"{secrets.randbelow(10000):04d}"
        if len(set(pin)) > 1 and pin not in ("1234", "4321", "0123"):
            return pin


# --------------------------------------------------------------- admin side


def list_pickers(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    week = _iso(today - timedelta(days=6))
    rows = []
    for p in conn.execute("SELECT * FROM ws_pickers ORDER BY is_active DESC, name"):
        stats = conn.execute(
            """
            SELECT
              SUM(CASE WHEN pick_finished_at >= ? THEN 1 ELSE 0 END) AS today,
              SUM(CASE WHEN pick_finished_at >= ? THEN 1 ELSE 0 END) AS week,
              AVG(CASE WHEN pick_finished_at >= ? THEN pick_seconds END) AS avg_seconds,
              COUNT(pick_finished_at) AS total
            FROM ws_orders WHERE picker_id = ? AND pick_finished_at IS NOT NULL
            """,
            (_iso(today), week, _iso(today - timedelta(days=29)), p["id"]),
        ).fetchone()
        working = conn.execute(
            "SELECT id FROM ws_orders WHERE picker_id = ? AND status = 'preparing' LIMIT 1", (p["id"],)
        ).fetchone()
        rows.append({
            "id": p["id"],
            "name": p["name"],
            "phone": p["phone"],
            "role": p["role"],
            "is_active": bool(p["is_active"]),
            "last_login_at": p["last_login_at"],
            "locked": bool(p["locked_until"] and _parse(p["locked_until"]) > _now()),
            "today": stats["today"] or 0,
            "week": stats["week"] or 0,
            "total": stats["total"] or 0,
            "avg_seconds": int(stats["avg_seconds"]) if stats["avg_seconds"] is not None else None,
            "working_on": wo.order_number(working["id"]) if working else None,
            "errors": _errors(conn, p["id"], _iso(today - timedelta(days=29))),
        })
    return rows


def _errors(conn: sqlite3.Connection, picker_id: int, since: str) -> int:
    from .wholesale_returns import picking_errors

    return picking_errors(conn, picker_id, since)


def _picker(conn: sqlite3.Connection, picker_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_pickers WHERE id = ?", (picker_id,)).fetchone()
    if not row:
        raise WholesaleError("الموظف مش موجود.", 404)
    return dict(row)


def create_picker(conn: sqlite3.Connection, name: str, phone: str, role: str = "picker") -> Dict[str, Any]:
    if role not in ("picker", "driver", "both"):
        raise WholesaleError("الدور غلط.")
    name = clean_text(name, 60)
    if not name:
        raise WholesaleError("اكتب اسم الموظف.")
    phone = normalize_phone(phone)
    pin = _new_pin()
    salt = secrets.token_hex(16)
    now = utc_now()
    try:
        picker_id = conn.execute(
            "INSERT INTO ws_pickers(name, phone, pin_hash, pin_salt, role, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (name, phone, _hash_pin(pin, salt), salt, role, now, now),
        ).lastrowid
    except sqlite3.IntegrityError:
        raise WholesaleError("فيه موظف بنفس رقم الموبايل.") from None
    return {**_picker_public(_picker(conn, picker_id)), "pin": pin}


def _picker_public(p: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": p["id"], "name": p["name"], "phone": p["phone"], "is_active": bool(p["is_active"]), "role": p.get("role") or "picker"}


def set_role(conn: sqlite3.Connection, picker_id: int, role: str) -> Dict[str, Any]:
    if role not in ("picker", "driver", "both"):
        raise WholesaleError("الدور غلط.")
    _picker(conn, picker_id)
    conn.execute("UPDATE ws_pickers SET role = ?, updated_at = ? WHERE id = ?", (role, utc_now(), picker_id))
    if role == "picker":
        _release_trips(conn, picker_id)
    return _picker_public(_picker(conn, picker_id))


def _release_trips(conn: sqlite3.Connection, picker_id: int) -> None:
    conn.execute(
        """
        UPDATE ws_orders SET status = 'ready', driver_id = NULL, delivery_status = '', out_at = NULL, arrived_at = NULL
        WHERE driver_id = ? AND status IN ('ready', 'out_for_delivery') AND delivery_status IN ('assigned', 'out', 'arrived', 'failed')
        """,
        (picker_id,),
    )


def reset_pin(conn: sqlite3.Connection, picker_id: int) -> Dict[str, Any]:
    _picker(conn, picker_id)
    pin = _new_pin()
    salt = secrets.token_hex(16)
    conn.execute(
        "UPDATE ws_pickers SET pin_hash = ?, pin_salt = ?, failed_attempts = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
        (_hash_pin(pin, salt), salt, utc_now(), picker_id),
    )
    conn.execute("DELETE FROM ws_picker_sessions WHERE picker_id = ?", (picker_id,))
    return {**_picker_public(_picker(conn, picker_id)), "pin": pin}


def set_active(conn: sqlite3.Connection, picker_id: int, active: bool) -> Dict[str, Any]:
    _picker(conn, picker_id)
    conn.execute("UPDATE ws_pickers SET is_active = ?, updated_at = ? WHERE id = ?", (1 if active else 0, utc_now(), picker_id))
    if not active:
        conn.execute("DELETE FROM ws_picker_sessions WHERE picker_id = ?", (picker_id,))
        conn.execute(
            "UPDATE ws_orders SET status = 'received', picker_id = NULL, pick_started_at = NULL WHERE picker_id = ? AND status = 'preparing'",
            (picker_id,),
        )
        _release_trips(conn, picker_id)
    return _picker_public(_picker(conn, picker_id))


# ------------------------------------------------------------------ sessions


def login(conn: sqlite3.Connection, phone: str, pin: str) -> str:
    try:
        phone = normalize_phone(phone)
    except WholesaleError:
        raise WholesaleError("رقم الموبايل أو الكود غلط.", 401) from None
    row = conn.execute("SELECT * FROM ws_pickers WHERE phone = ?", (phone,)).fetchone()
    if not row or not row["is_active"]:
        raise WholesaleError("رقم الموبايل أو الكود غلط.", 401)
    p = dict(row)
    now = _now()
    if p["locked_until"] and _parse(p["locked_until"]) > now:
        raise WholesaleError("محاولات كتير غلط. استنى ربع ساعة، أو اطلب كود جديد من الإدارة.", 429)
    pin = str(pin or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).strip()
    if not re.fullmatch(r"\d{4}", pin) or not hmac.compare_digest(_hash_pin(pin, p["pin_salt"]), p["pin_hash"]):
        failed = p["failed_attempts"] + 1
        locked = _iso(now + timedelta(minutes=LOCK_MINUTES)) if failed >= MAX_FAILED else None
        conn.execute(
            "UPDATE ws_pickers SET failed_attempts = ?, locked_until = ? WHERE id = ?",
            (0 if locked else failed, locked, p["id"]),
        )
        conn.commit()  # keep the failed attempt: the error below rolls back the rest of the request
        raise WholesaleError("رقم الموبايل أو الكود غلط.", 401)
    token = secrets.token_urlsafe(32)
    conn.execute("UPDATE ws_pickers SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE id = ?", (_iso(now), p["id"]))
    conn.execute("DELETE FROM ws_picker_sessions WHERE expires_at < ?", (_iso(now),))
    conn.execute(
        "INSERT INTO ws_picker_sessions(picker_id, token_hash, expires_at, created_at) VALUES(?, ?, ?, ?)",
        (p["id"], hashlib.sha256(token.encode()).hexdigest(), _iso(now + timedelta(hours=SESSION_HOURS)), _iso(now)),
    )
    return token


def current_picker(conn: sqlite3.Connection, token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    row = conn.execute(
        """
        SELECT p.* FROM ws_picker_sessions s JOIN ws_pickers p ON p.id = s.picker_id
        WHERE s.token_hash = ? AND s.expires_at > ? AND p.is_active = 1
        """,
        (hashlib.sha256(token.encode()).hexdigest(), utc_now()),
    ).fetchone()
    return _picker_public(dict(row)) if row else None


def logout(conn: sqlite3.Connection, token: str) -> None:
    if token:
        conn.execute("DELETE FROM ws_picker_sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))


# ------------------------------------------------------------------- picking


def _seconds_since(ts: Optional[str]) -> int:
    start = _parse(ts)
    return max(0, int((_now() - start).total_seconds())) if start else 0


def queue(conn: sqlite3.Connection, picker_id: int) -> Dict[str, Any]:
    rows = conn.execute(
        """
        SELECT o.id, o.status, o.received_at, o.picker_id, o.pick_started_at, o.is_plus, c.name AS customer_name,
               t.shop_name, t.area, k.name AS picker_name,
               (SELECT COUNT(*) FROM ws_order_items i WHERE i.order_id = o.id AND COALESCE(i.received_qty, i.confirmed_qty, i.qty) > 0) AS items,
               (SELECT COUNT(*) FROM ws_order_items i WHERE i.order_id = o.id AND i.note != '' AND COALESCE(i.received_qty, i.confirmed_qty, i.qty) > 0) AS notes
        FROM ws_orders o JOIN customers c ON c.id = o.customer_id
        LEFT JOIN ws_traders t ON t.customer_id = o.customer_id
        LEFT JOIN ws_pickers k ON k.id = o.picker_id
        WHERE o.status IN ('received', 'preparing')
        ORDER BY (o.picker_id = ?) DESC, o.is_plus DESC, o.received_at, o.id
        """,
        (picker_id,),
    ).fetchall()
    items = []
    for r in rows:
        mine = r["picker_id"] == picker_id
        items.append({
            "id": r["id"],
            "number": wo.order_number(r["id"]),
            "shop": r["shop_name"] or r["customer_name"],
            "area": r["area"] or "",
            "items": r["items"],
            "has_notes": r["notes"] > 0,
            "waiting_seconds": _seconds_since(r["received_at"]),
            "status": r["status"],
            "mine": mine,
            "locked_by": r["picker_name"] if r["status"] == "preparing" and not mine else None,
            "priority": bool(r["is_plus"]),  # Rose Plus orders go first
        })
    stats = [s for s in list_pickers(conn) if s["id"] == picker_id]
    return {"orders": items, "stats": stats[0] if stats else {}}


def _items_for_pick(conn: sqlite3.Connection, order_id: int) -> List[Dict[str, Any]]:
    rows = []
    for i in conn.execute("SELECT * FROM ws_order_items WHERE order_id = ? ORDER BY id", (order_id,)):
        expected = i["received_qty"] if i["received_qty"] is not None else (i["confirmed_qty"] if i["confirmed_qty"] is not None else i["qty"])
        if not expected:
            continue
        rows.append({
            "id": i["id"],
            "name": i["name"],
            "unit": i["unit"],
            "unit_label": wo.UNIT_LABELS[i["unit"]],
            "pieces": i["pieces"],
            "qty": int(expected),
            "note": i["note"],
            "pick_status": i["pick_status"],
            "picked_qty": i["picked_qty"],
        })
    return rows


def pick_view(conn: sqlite3.Connection, order_id: int, picker_id: int) -> Dict[str, Any]:
    order = wo._order(conn, order_id)
    if order["status"] not in ("received", "preparing", "ready"):
        raise WholesaleError("الطلب ده مش في مرحلة التجهيز.", 409)
    customer = conn.execute(
        "SELECT c.name, t.shop_name, t.area FROM customers c LEFT JOIN ws_traders t ON t.customer_id = c.id WHERE c.id = ?",
        (order["customer_id"],),
    ).fetchone()
    locked_by = None
    if order["status"] == "preparing" and order["picker_id"] != picker_id:
        other = conn.execute("SELECT name FROM ws_pickers WHERE id = ?", (order["picker_id"],)).fetchone()
        locked_by = other["name"] if other else "موظف تاني"
    return {
        "id": order_id,
        "number": wo.order_number(order_id),
        "status": order["status"],
        "shop": customer["shop_name"] or customer["name"],
        "area": customer["area"] or "",
        "customer_note": order["customer_note"],
        "started_at": order["pick_started_at"],
        "elapsed": _seconds_since(order["pick_started_at"]) if order["status"] == "preparing" else order["pick_seconds"],
        "mine": order["picker_id"] == picker_id,
        "locked_by": locked_by,
        "items": _items_for_pick(conn, order_id),
    }


def start_pick(conn: sqlite3.Connection, order_id: int, picker_id: int) -> Dict[str, Any]:
    order = wo._order(conn, order_id)
    if order["status"] == "preparing" and order["picker_id"] == picker_id:
        return pick_view(conn, order_id, picker_id)
    if order["status"] != "received":
        view = pick_view(conn, order_id, picker_id)
        if view["locked_by"]:
            raise WholesaleError(f"{view['locked_by']} بيجهّز الطلب ده.", 409)
        raise WholesaleError("الطلب ده مش مستني تجهيز.", 409)
    busy = conn.execute("SELECT id FROM ws_orders WHERE picker_id = ? AND status = 'preparing'", (picker_id,)).fetchone()
    if busy:
        raise WholesaleError(f"خلّص طلب {wo.order_number(busy['id'])} الأول.", 409)
    now = utc_now()
    cur = conn.execute(
        "UPDATE ws_orders SET status = 'preparing', picker_id = ?, pick_started_at = ?, updated_at = ? WHERE id = ? AND status = 'received'",
        (picker_id, now, now, order_id),
    )
    if not cur.rowcount:
        raise WholesaleError("موظف تاني لسه مستلم الطلب ده.", 409)
    conn.execute("UPDATE ws_order_items SET pick_status = 'pending', picked_qty = NULL WHERE order_id = ?", (order_id,))
    wo.notify(conn, order["customer_id"], "order", f"بدأنا نجهّز طلبك رقم {wo.order_number(order_id)}",
              "هنبلغك أول ما يخلص بكل التفاصيل.", order_id=order_id)
    return pick_view(conn, order_id, picker_id)


def _require_mine(conn: sqlite3.Connection, order_id: int, picker_id: int) -> Dict[str, Any]:
    order = wo._order(conn, order_id)
    if order["status"] != "preparing" or order["picker_id"] != picker_id:
        raise WholesaleError("الطلب ده مش معاك دلوقتي.", 409)
    return order


def mark_item(conn: sqlite3.Connection, order_id: int, item_id: int, picker_id: int, status: str, qty: Optional[int] = None) -> Dict[str, Any]:
    _require_mine(conn, order_id, picker_id)
    item = next((i for i in _items_for_pick(conn, order_id) if i["id"] == item_id), None)
    if not item:
        raise WholesaleError("الصنف مش في الطلب.", 404)
    if status == "ready":
        picked = item["qty"]
    elif status == "missing":
        picked = 0
    elif status == "short":
        if qty is None or int(qty) < 1 or int(qty) >= item["qty"]:
            raise WholesaleError(f"العدد لازم يكون من 1 لحد {item['qty'] - 1}.")
        picked = int(qty)
    elif status == "pending":
        picked = None
    else:
        raise WholesaleError("الحالة غلط.")
    conn.execute("UPDATE ws_order_items SET pick_status = ?, picked_qty = ? WHERE id = ?", (status, picked, item_id))
    return pick_view(conn, order_id, picker_id)


def finish_pick(conn: sqlite3.Connection, order_id: int, picker_id: int) -> Dict[str, Any]:
    order = _require_mine(conn, order_id, picker_id)
    items = _items_for_pick(conn, order_id)
    pending = [i for i in items if i["pick_status"] == "pending"]
    if pending:
        raise WholesaleError(f"لسه {len(pending)} صنف من غير علامة.")
    now = utc_now()
    seconds = _seconds_since(order["pick_started_at"])
    all_rows = [dict(r) for r in conn.execute("SELECT * FROM ws_order_items WHERE order_id = ?", (order_id,))]
    for row in all_rows:
        expected = row["received_qty"] if row["received_qty"] is not None else (row["confirmed_qty"] if row["confirmed_qty"] is not None else row["qty"])
        if row["picked_qty"] is not None and row["picked_qty"] < expected:
            wo._record_shortage(conn, order, row, int(expected) - int(row["picked_qty"]), "picking")
    total = round(sum(wo._final_qty(i) * i["unit_price"] for i in all_rows), 2)
    conn.execute(
        "UPDATE ws_orders SET final_total = ?, pick_finished_at = ?, pick_seconds = ?, updated_at = ? WHERE id = ?",
        (total, now, seconds, now, order_id),
    )
    grand = wp.recalc_order(conn, order_id)["grand"]
    number = wo.order_number(order_id)
    picked_rows = [i for i in all_rows if i["picked_qty"] is not None]
    ready = sum(1 for i in picked_rows if i["pick_status"] == "ready")
    fewer = sum(1 for i in picked_rows if i["pick_status"] == "short")
    missing = [i for i in picked_rows if i["pick_status"] == "missing"]
    missing_after_review = [
        f"{i['name']} ({int(i['confirmed_qty'] or 0) - wo._final_qty(i)} {wo.UNIT_LABELS[i['unit']]})"
        for i in all_rows if int(i["confirmed_qty"] or 0) > wo._final_qty(i)
    ]
    if total <= 0:
        wo._cancel(conn, order_id, "الأصناف ماكانتش موجودة وقت التجهيز")
        wo.notify(conn, order["customer_id"], "order", f"طلبك رقم {number} اتلغى",
                  "للأسف الأصناف ماكانتش موجودة وقت التجهيز." + wo._missing_text(missing_after_review), order_id=order_id)
    else:
        conn.execute("UPDATE ws_orders SET status = 'ready' WHERE id = ?", (order_id,))
        parts = [f"{ready} صنف جاهز"]
        if fewer:
            parts.append(f"{fewer} بعدد أقل")
        if missing:
            parts.append(f"{len(missing)} مش متوفر")
        body = "، ".join(parts) + f". الإجمالي النهائي {wo.money(grand)} ج."
        fresh = dict(conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order_id,)).fetchone())
        view = wp.payment_view(conn, fresh)
        if view["can_pay"]:
            body += " حوّل المبلغ من صفحة الطلب علشان نوصّله."
        if view["refund_due"]:
            body += f" ليك {wo.money(view['refund_due'])} ج هنرجّعهم كاش."
        body += wo._missing_text(missing_after_review)
        wo.notify(conn, order["customer_id"], "order", f"طلبك رقم {number} جاهز", body, order_id=order_id)
    return {"id": order_id, "number": number, "seconds": seconds, "status": "cancelled" if total <= 0 else "ready", "total": grand}


def release_pick(conn: sqlite3.Connection, order_id: int) -> None:
    """Admin: free an order an employee started but did not finish."""
    order = wo._order(conn, order_id)
    if order["status"] != "preparing":
        raise WholesaleError("الطلب ده مش بيتجهز دلوقتي.")
    conn.execute(
        "UPDATE ws_orders SET status = 'received', picker_id = NULL, pick_started_at = NULL, updated_at = ? WHERE id = ?",
        (utc_now(), order_id),
    )
    conn.execute("UPDATE ws_order_items SET pick_status = 'pending', picked_qty = NULL WHERE order_id = ?", (order_id,))


# ----------------------------------------------------------------- receiving


def receiving_list(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Purchase orders the store is waiting for (no prices for employees)."""
    result = []
    for po in conn.execute(
        """
        SELECT p.*, s.alias, o.id AS oid FROM ws_purchase_orders p
        JOIN ws_suppliers s ON s.id = p.supplier_id JOIN ws_orders o ON o.id = p.order_id
        WHERE o.status = 'reviewed' AND p.status NOT IN ('received', 'cancelled')
        ORDER BY s.alias, p.id
        """
    ):
        items = [
            {"id": i["id"], "name": i["supplier_item_name"] or i["name"], "code": i["supplier_code"],
             "unit_label": wo.UNIT_LABELS[i["unit"]], "qty": int(i["confirmed_qty"] or 0), "note": i["note"]}
            for i in conn.execute("SELECT * FROM ws_order_items WHERE purchase_order_id = ? ORDER BY id", (po["id"],))
            if i["confirmed_qty"]
        ]
        if not items:
            continue
        result.append({"id": po["id"], "number": f"{wo.order_number(po['oid'])}-{po['suffix']}", "supplier": po["alias"],
                       "status": po["status"], "items": items})
    return result


def receive_from_phone(conn: sqlite3.Connection, po_id: int, received: Dict[str, int], picker_id: int) -> Dict[str, Any]:
    wo.receive_po(conn, po_id, received)
    conn.execute("UPDATE ws_purchase_orders SET received_by_picker = ? WHERE id = ?", (picker_id, po_id))
    return {"ok": True}
