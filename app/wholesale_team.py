"""Trader team accounts: the shop owner (manager) and their employees (staff).

The manager is the approved trader (their Rose customer account). They add
employees by name and mobile number; each employee gets a 4-digit code and
signs in on /wholesale/team/. Per employee the manager chooses:

    can_see_prices    see prices, or only names and units (default: no)
    can_send_direct   send straight to Rose, or to the manager first (default: no)
    can_use_notebook  add to the shop's shortages notebook (default: yes)

Without direct sending, an employee's list waits for the manager, who can
change quantities, remove lines and approve. Approved lines go to the
manager's cart and are sent with the manager's normal checkout; the request
is then linked to the order.

If an item an employee asked for turns out unavailable at review, that
employee is told, and the item stays blocked for the whole team until Rose
marks it available again (then the employee is told too).
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
from .wholesale_orders import MAX_QTY, UNIT_LABELS, catalog, notify, order_number, require_trader, submit_order
from .wholesale_picking import PBKDF2_ROUNDS, normalize_phone
from .wholesale_pricing import UNITS
from .wholesale_service import WholesaleError, clean_text

SESSION_COOKIE = "rose_team_session"
SESSION_HOURS = 24 * 14
MAX_FAILED = 5
LOCK_MINUTES = 15
MAX_STAFF = 20
STATUS_LABEL = {"pending": "مستني موافقة المدير", "approved": "المدير وافق", "rejected": "المدير رفض", "ordered": "اتبعت لروز"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()


def _new_pin() -> str:
    while True:
        pin = f"{secrets.randbelow(10000):04d}"
        if len(set(pin)) > 1 and pin not in ("1234", "4321", "0123"):
            return pin


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "name": row["name"],
        "phone": row["phone"],
        "can_see_prices": bool(row["can_see_prices"]),
        "can_send_direct": bool(row["can_send_direct"]),
        "can_use_notebook": bool(row["can_use_notebook"]),
        "direct_method": row["direct_method"],
        "is_active": bool(row["is_active"]),
        "last_login_at": row["last_login_at"],
    }


# ------------------------------------------------------------ manager: staff


def _staff_of(conn: sqlite3.Connection, owner_id: int, staff_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_trader_staff WHERE id = ? AND owner_id = ?", (staff_id, owner_id)).fetchone()
    if not row:
        raise WholesaleError("الموظف مش موجود.", 404)
    return dict(row)


def list_staff(conn: sqlite3.Connection, owner_id: int) -> List[Dict[str, Any]]:
    require_trader(conn, owner_id)
    out = []
    for r in conn.execute("SELECT * FROM ws_trader_staff WHERE owner_id = ? ORDER BY is_active DESC, name", (owner_id,)):
        item = _public(dict(r))
        item["pending"] = conn.execute("SELECT COUNT(*) FROM ws_team_requests WHERE staff_id = ? AND status = 'pending'", (r["id"],)).fetchone()[0]
        out.append(item)
    return out


def _perms(data: Dict[str, Any], current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    current = current or {"can_see_prices": 0, "can_send_direct": 0, "can_use_notebook": 1, "direct_method": "cash"}
    out = {}
    for key in ("can_see_prices", "can_send_direct", "can_use_notebook"):
        out[key] = int(bool(data[key])) if data.get(key) is not None else int(current[key])
    method = data.get("direct_method") or current["direct_method"]
    if method not in ("cash", "credit"):
        raise WholesaleError("طريقة الدفع للطلبات المباشرة لازم تكون كاش أو آجل.")
    out["direct_method"] = method
    return out


def add_staff(conn: sqlite3.Connection, owner_id: int, name: str, phone: str, perms: Dict[str, Any]) -> Dict[str, Any]:
    require_trader(conn, owner_id)
    name = clean_text(name, 60)
    if len(name) < 2:
        raise WholesaleError("اكتب اسم الموظف.")
    phone = normalize_phone(phone)
    if conn.execute("SELECT COUNT(*) FROM ws_trader_staff WHERE owner_id = ? AND is_active = 1", (owner_id,)).fetchone()[0] >= MAX_STAFF:
        raise WholesaleError(f"أقصى عدد {MAX_STAFF} موظف.")
    if conn.execute("SELECT 1 FROM ws_trader_staff WHERE phone = ?", (phone,)).fetchone():
        raise WholesaleError("الرقم ده متسجّل كموظف قبل كده.")
    p = _perms(perms)
    pin, salt = _new_pin(), secrets.token_hex(16)
    staff_id = conn.execute(
        """
        INSERT INTO ws_trader_staff(owner_id, name, phone, pin_hash, pin_salt, can_see_prices, can_send_direct, can_use_notebook, direct_method, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (owner_id, name, phone, _hash_pin(pin, salt), salt, p["can_see_prices"], p["can_send_direct"], p["can_use_notebook"], p["direct_method"], utc_now()),
    ).lastrowid
    return {**_public(_staff_of(conn, owner_id, staff_id)), "pin": pin}


def update_staff(conn: sqlite3.Connection, owner_id: int, staff_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    require_trader(conn, owner_id)
    s = _staff_of(conn, owner_id, staff_id)
    p = _perms(data, s)
    active = int(bool(data["is_active"])) if data.get("is_active") is not None else s["is_active"]
    conn.execute(
        "UPDATE ws_trader_staff SET can_see_prices = ?, can_send_direct = ?, can_use_notebook = ?, direct_method = ?, is_active = ? WHERE id = ?",
        (p["can_see_prices"], p["can_send_direct"], p["can_use_notebook"], p["direct_method"], active, staff_id),
    )
    if not active:
        conn.execute("DELETE FROM ws_trader_staff_sessions WHERE staff_id = ?", (staff_id,))
    return _public(_staff_of(conn, owner_id, staff_id))


def reset_pin(conn: sqlite3.Connection, owner_id: int, staff_id: int) -> Dict[str, Any]:
    require_trader(conn, owner_id)
    _staff_of(conn, owner_id, staff_id)
    pin, salt = _new_pin(), secrets.token_hex(16)
    conn.execute(
        "UPDATE ws_trader_staff SET pin_hash = ?, pin_salt = ?, failed_attempts = 0, locked_until = NULL WHERE id = ?",
        (_hash_pin(pin, salt), salt, staff_id),
    )
    conn.execute("DELETE FROM ws_trader_staff_sessions WHERE staff_id = ?", (staff_id,))
    return {**_public(_staff_of(conn, owner_id, staff_id)), "pin": pin}


# ------------------------------------------------------------- staff: login


def login(conn: sqlite3.Connection, phone: str, pin: str) -> str:
    try:
        phone = normalize_phone(phone)
    except WholesaleError:
        raise WholesaleError("رقم الموبايل أو الكود غلط.", 401) from None
    row = conn.execute("SELECT * FROM ws_trader_staff WHERE phone = ?", (phone,)).fetchone()
    if not row or not row["is_active"]:
        raise WholesaleError("رقم الموبايل أو الكود غلط.", 401)
    s = dict(row)
    now = _now()
    if s["locked_until"] and datetime.fromisoformat(s["locked_until"]) > now:
        raise WholesaleError("محاولات كتير غلط. استنى ربع ساعة، أو اطلب كود جديد من المدير.", 429)
    pin = str(pin or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).strip()
    if not re.fullmatch(r"\d{4}", pin) or not hmac.compare_digest(_hash_pin(pin, s["pin_salt"]), s["pin_hash"]):
        failed = s["failed_attempts"] + 1
        locked = _iso(now + timedelta(minutes=LOCK_MINUTES)) if failed >= MAX_FAILED else None
        conn.execute("UPDATE ws_trader_staff SET failed_attempts = ?, locked_until = ? WHERE id = ?", (0 if locked else failed, locked, s["id"]))
        conn.commit()  # keep the failed attempt: the error below rolls back the rest of the request
        raise WholesaleError("رقم الموبايل أو الكود غلط.", 401)
    require_trader(conn, s["owner_id"])
    token = secrets.token_urlsafe(32)
    conn.execute("UPDATE ws_trader_staff SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE id = ?", (_iso(now), s["id"]))
    conn.execute("DELETE FROM ws_trader_staff_sessions WHERE expires_at < ?", (_iso(now),))
    conn.execute(
        "INSERT INTO ws_trader_staff_sessions(staff_id, token_hash, expires_at, created_at) VALUES(?, ?, ?, ?)",
        (s["id"], hashlib.sha256(token.encode()).hexdigest(), _iso(now + timedelta(hours=SESSION_HOURS)), _iso(now)),
    )
    return token


def current_staff(conn: sqlite3.Connection, token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    row = conn.execute(
        """
        SELECT s.* FROM ws_trader_staff_sessions x JOIN ws_trader_staff s ON s.id = x.staff_id
        JOIN ws_traders t ON t.customer_id = s.owner_id AND t.status = 'approved'
        WHERE x.token_hash = ? AND x.expires_at > ? AND s.is_active = 1
        """,
        (hashlib.sha256(token.encode()).hexdigest(), utc_now()),
    ).fetchone()
    return _public(dict(row)) if row else None


def logout(conn: sqlite3.Connection, token: str) -> None:
    if token:
        conn.execute("DELETE FROM ws_trader_staff_sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))


# ----------------------------------------------------------- staff: ordering


def blocked_products(conn: sqlite3.Connection, owner_id: int) -> set:
    """Products that were not available for this shop and are still waiting."""
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT product_id FROM ws_shortages WHERE customer_id = ? AND status = 'waiting' AND product_id IS NOT NULL AND source != 'request'",
        (owner_id,),
    )}


def _strip(item: Dict[str, Any], prices: bool, blocked: set) -> Dict[str, Any]:
    units = {u: (v if prices else ({"pieces": v["pieces"]} if v else None)) for u, v in item["units"].items()}
    return {**item, "units": units, "waiting": item["id"] in blocked}


def staff_catalog(conn: sqlite3.Connection, staff: Dict[str, Any], q: str = "", section_id: Optional[int] = None, page: int = 1) -> Dict[str, Any]:
    data = catalog(conn, staff["owner_id"], q=q, section_id=section_id, page=page)
    blocked = blocked_products(conn, staff["owner_id"])
    data["items"] = [_strip(i, staff["can_see_prices"], blocked) for i in data["items"]]
    return data


def _clean_lines(conn: sqlite3.Connection, owner_id: int, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not items:
        raise WholesaleError("القايمة فاضية.")
    ids = sorted({int(i["product_id"]) for i in items})
    found = {}
    for start in range(0, len(ids), 100):
        for p in catalog(conn, owner_id, ids=ids[start:start + 100], per_page=100)["items"]:
            found[p["id"]] = p
    blocked = blocked_products(conn, owner_id)
    lines, problems = {}, []
    for i in items:
        p = found.get(int(i["product_id"]))
        unit = i.get("unit") if i.get("unit") in UNITS else "piece"
        if not p or not p["units"].get(unit):
            problems.append("صنف مش متاح دلوقتي")
            continue
        if p["id"] in blocked:
            problems.append(f"«{p['name']}» مستني يتوفر")
            continue
        qty = max(1, min(int(i.get("qty") or 1), MAX_QTY))
        key = (p["id"], unit)
        if key in lines:
            lines[key]["qty"] = min(MAX_QTY, lines[key]["qty"] + qty)
        else:
            lines[key] = {"product_id": p["id"], "name": p["name"], "unit": unit, "qty": qty, "note": clean_text(i.get("note"), 200)}
    if problems:
        raise WholesaleError("شيل من القايمة: " + "، ".join(dict.fromkeys(problems)), 409)
    return list(lines.values())


def staff_notice(conn: sqlite3.Connection, staff_id: int, title: str, body: str = "", product_id: Optional[int] = None) -> None:
    conn.execute(
        "INSERT INTO ws_trader_staff_notices(staff_id, title, body, product_id, created_at) VALUES(?, ?, ?, ?, ?)",
        (staff_id, title[:200], body[:600], product_id, utc_now()),
    )


def submit_request(conn: sqlite3.Connection, staff: Dict[str, Any], items: List[Dict[str, Any]], note: str = "") -> Dict[str, Any]:
    owner = staff["owner_id"]
    require_trader(conn, owner)
    lines = _clean_lines(conn, owner, items)
    note = clean_text(note, 300)
    now = utc_now()
    order = None
    if staff["can_send_direct"]:
        order = submit_order(
            conn, owner,
            [{"product_id": l["product_id"], "unit": l["unit"], "qty": l["qty"], "note": l["note"]} for l in lines],
            f"طلب من {staff['name']}" + (f": {note}" if note else ""),
            staff["direct_method"],
        )
    req_id = conn.execute(
        "INSERT INTO ws_team_requests(owner_id, staff_id, status, note, order_id, created_at, decided_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (owner, staff["id"], "ordered" if order else "pending", note, order["id"] if order else None, now, now if order else None),
    ).lastrowid
    for l in lines:
        conn.execute(
            "INSERT INTO ws_team_request_items(request_id, product_id, name, unit, qty, note) VALUES(?, ?, ?, ?, ?, ?)",
            (req_id, l["product_id"], l["name"], l["unit"], l["qty"], l["note"]),
        )
    if order:
        notify(conn, owner, "team", f"{staff['name']} بعت طلب لروز", f"طلب رقم {order['number']} ({len(lines)} صنف).", order_id=order["id"])
    else:
        notify(conn, owner, "team", f"{staff['name']} بعت قايمة مستنية موافقتك", f"{len(lines)} صنف. راجعها من «طلبات الفريق».")
    return request_view(conn, req_id, prices=bool(staff["can_see_prices"]))


def request_view(conn: sqlite3.Connection, req_id: int, prices: bool) -> Dict[str, Any]:
    r = dict(conn.execute(
        "SELECT r.*, s.name AS staff_name FROM ws_team_requests r JOIN ws_trader_staff s ON s.id = r.staff_id WHERE r.id = ?", (req_id,)
    ).fetchone())
    rows = [dict(x) for x in conn.execute("SELECT * FROM ws_team_request_items WHERE request_id = ? ORDER BY id", (req_id,))]
    priced = {}
    if prices:
        ids = sorted({x["product_id"] for x in rows if x["product_id"]})
        if ids:
            priced = {p["id"]: p for p in catalog(conn, r["owner_id"], ids=ids, per_page=100)["items"]}
    blocked = blocked_products(conn, r["owner_id"])
    items = []
    for x in rows:
        p = priced.get(x["product_id"])
        unit = p["units"].get(x["unit"]) if p else None
        items.append({
            "id": x["id"], "product_id": x["product_id"], "name": x["name"], "unit": x["unit"], "unit_label": UNIT_LABELS[x["unit"]],
            "qty": x["qty"], "note": x["note"], "waiting": x["product_id"] in blocked,
            "price": unit["price"] if unit and prices else None,
            "product": p if prices and r["status"] == "pending" else None,
        })
    return {
        "id": r["id"], "status": r["status"], "status_label": STATUS_LABEL[r["status"]], "note": r["note"],
        "manager_note": r["manager_note"], "staff_id": r["staff_id"], "staff_name": r["staff_name"],
        "order_id": r["order_id"], "order_number": order_number(r["order_id"]) if r["order_id"] else None,
        "created_at": r["created_at"], "items": items,
        "total": round(sum((i["price"] or 0) * i["qty"] for i in items), 2) if prices else None,
    }


def staff_requests(conn: sqlite3.Connection, staff: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [request_view(conn, r[0], prices=bool(staff["can_see_prices"])) for r in conn.execute(
        "SELECT id FROM ws_team_requests WHERE staff_id = ? ORDER BY id DESC LIMIT 30", (staff["id"],)
    )]


def staff_notices(conn: sqlite3.Connection, staff_id: int, mark_read: bool = False) -> Dict[str, Any]:
    items = [dict(r) for r in conn.execute(
        "SELECT id, title, body, read_at, created_at FROM ws_trader_staff_notices WHERE staff_id = ? ORDER BY id DESC LIMIT 50", (staff_id,)
    )]
    unread = sum(1 for i in items if not i["read_at"])
    if mark_read and unread:
        conn.execute("UPDATE ws_trader_staff_notices SET read_at = ? WHERE staff_id = ? AND read_at IS NULL", (utc_now(), staff_id))
    return {"items": items, "unread": unread}


# ------------------------------------------------------- manager: approving


def owner_requests(conn: sqlite3.Connection, owner_id: int, status: str = "pending") -> List[Dict[str, Any]]:
    require_trader(conn, owner_id)
    sql = "SELECT id FROM ws_team_requests WHERE owner_id = ?"
    params: List[Any] = [owner_id]
    if status in STATUS_LABEL:
        sql += " AND status = ?"
        params.append(status)
    return [request_view(conn, r[0], prices=True) for r in conn.execute(sql + " ORDER BY id DESC LIMIT 50", params)]


def pending_count(conn: sqlite3.Connection, owner_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM ws_team_requests WHERE owner_id = ? AND status = 'pending'", (owner_id,)).fetchone()[0]


def decide_request(conn: sqlite3.Connection, owner_id: int, req_id: int, approve: bool,
                   changes: Optional[List[Dict[str, Any]]] = None, manager_note: str = "") -> Dict[str, Any]:
    require_trader(conn, owner_id)
    r = conn.execute("SELECT * FROM ws_team_requests WHERE id = ? AND owner_id = ?", (req_id, owner_id)).fetchone()
    if not r:
        raise WholesaleError("الطلب مش موجود.", 404)
    if r["status"] != "pending":
        raise WholesaleError("الطلب ده اتاخد فيه قرار.")
    note = clean_text(manager_note, 300)
    if approve:
        for c in changes or []:
            item = conn.execute("SELECT * FROM ws_team_request_items WHERE id = ? AND request_id = ?", (int(c.get("item_id") or 0), req_id)).fetchone()
            if not item:
                continue
            if c.get("remove"):
                conn.execute("DELETE FROM ws_team_request_items WHERE id = ?", (item["id"],))
            elif c.get("qty"):
                conn.execute("UPDATE ws_team_request_items SET qty = ? WHERE id = ?", (max(1, min(int(c["qty"]), MAX_QTY)), item["id"]))
        left = conn.execute("SELECT COUNT(*) FROM ws_team_request_items WHERE request_id = ?", (req_id,)).fetchone()[0]
        if not left:
            raise WholesaleError("شلت كل الأصناف. لو مش عايزها، ارفض الطلب.")
    status = "approved" if approve else "rejected"
    conn.execute("UPDATE ws_team_requests SET status = ?, manager_note = ?, decided_at = ? WHERE id = ?", (status, note, utc_now(), req_id))
    staff_notice(conn, r["staff_id"], "المدير وافق على قايمتك" if approve else "المدير رفض قايمتك", note)
    return request_view(conn, req_id, prices=True)


def link_order(conn: sqlite3.Connection, owner_id: int, request_ids: List[int], order_id: int, number: int) -> None:
    """After the manager sends an order with approved team lists in the cart."""
    for rid in {int(x) for x in request_ids or []}:
        r = conn.execute("SELECT * FROM ws_team_requests WHERE id = ? AND owner_id = ? AND status = 'approved'", (rid, owner_id)).fetchone()
        if not r:
            continue
        conn.execute("UPDATE ws_team_requests SET status = 'ordered', order_id = ? WHERE id = ?", (order_id, rid))
        staff_notice(conn, r["staff_id"], f"قايمتك اتبعتت لروز في طلب رقم {number}")


# ------------------------------------------------------------- order hooks


def after_review(conn: sqlite3.Connection, order_id: int) -> None:
    """Tell each employee which of the items they asked for were not available."""
    reqs = conn.execute("SELECT id, staff_id FROM ws_team_requests WHERE order_id = ?", (order_id,)).fetchall()
    if not reqs:
        return
    missing = {(r["product_id"], r["unit"]): r["name"] for r in conn.execute(
        "SELECT product_id, unit, name FROM ws_order_items WHERE order_id = ? AND COALESCE(confirmed_qty, qty) < qty", (order_id,)
    )}
    if not missing:
        return
    for req in reqs:
        for item in conn.execute("SELECT product_id, unit, name FROM ws_team_request_items WHERE request_id = ?", (req["id"],)):
            if (item["product_id"], item["unit"]) in missing:
                staff_notice(conn, req["staff_id"], f"«{item['name']}» مش متوفر دلوقتي",
                             "اتشال من الطلب. هنبلغك أول ما يتوفر، ولحد كده مش هتقدر تطلبه.", item["product_id"])


def after_available(conn: sqlite3.Connection, product_id: int, customer_ids: List[int]) -> None:
    """A product is available again: tell the employees who were told it was missing."""
    name = conn.execute("SELECT name FROM ws_products WHERE id = ?", (product_id,)).fetchone()
    for owner in set(customer_ids):
        staff_ids = [r[0] for r in conn.execute(
            """
            SELECT DISTINCT n.staff_id FROM ws_trader_staff_notices n JOIN ws_trader_staff s ON s.id = n.staff_id
            WHERE s.owner_id = ? AND n.product_id = ?
            """,
            (owner, product_id),
        )]
        for staff_id in staff_ids:
            conn.execute("UPDATE ws_trader_staff_notices SET product_id = NULL WHERE staff_id = ? AND product_id = ?", (staff_id, product_id))
            staff_notice(conn, staff_id, f"«{name['name'] if name else 'الصنف'}» اتوفر", "تقدر تطلبه تاني.")
