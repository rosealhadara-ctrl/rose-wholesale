"""Wholesale phase 7 (sections ٧ and ٨): returns, ratings, complaints and suggestions.

Returns
    requested  the trader asked, within N days of delivery (setting, default 15):
               items, quantity, reason, note, optional photo
    accepted   the admin accepted; a driver collects it, or the trader brings it
    rejected   with a reason sent to the trader
    picked_up  the driver collected it from the trader
    inspected  a store employee checked each item: sound / damaged / missing
    refunded   the admin confirmed the cash refund (cash only, no store credit).
               For an order on account the amount comes off the trader's
               credit balance first.  The return's share of the order's points
               is taken back.
Sound and damaged quantities are kept in Rose only (reports and supplier
accounts); nothing goes back to Yaseer.
A return with the reason "wrong item" counts as a picking error for the
employee who prepared the order.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db import utc_now
from .wholesale_pricing import get_settings
from .wholesale_service import WholesaleError, clean_text
from . import wholesale_orders as wo
from . import wholesale_payments as wp

REASONS = {"damaged": "تالف", "wrong_item": "صنف غلط", "near_expiry": "قرب ينتهي", "not_needed": "مش مطلوب"}
CUSTOMER_LABEL = {
    "requested": "اتبعت",
    "accepted": "اتقبل",
    "rejected": "اترفض",
    "picked_up": "المندوب استلم الأصناف",
    "inspected": "اتفحص",
    "refunded": "اترجعلك المبلغ",
}
ADMIN_LABEL = {
    "requested": "محتاج قرار",
    "accepted": "مستني الاستلام من التاجر",
    "rejected": "اترفض",
    "picked_up": "مستني الفحص",
    "inspected": "مستني رجوع الفلوس",
    "refunded": "اتقفل",
}
LIKED = ["سرعة التوصيل", "الأسعار", "جودة الأصناف", "تعامل المندوب", "سهولة الطلب", "الأصناف كاملة"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    value = datetime.fromisoformat(ts)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def return_days(conn: sqlite3.Connection) -> int:
    try:
        return int(get_settings(conn).get("return_days", "15") or 15)
    except ValueError:
        return 15


def _delivered_qty(item: Dict[str, Any]) -> int:
    return wo._final_qty(item)


def returnable(conn: sqlite3.Connection, customer_id: int, order_id: int) -> Dict[str, Any]:
    """Items the trader can still return from a delivered order."""
    order = wo._order(conn, order_id)
    if order["customer_id"] != customer_id:
        raise WholesaleError("الطلب مش موجود.", 404)
    days = return_days(conn)
    delivered = _parse(order["delivered_at"])
    deadline = delivered + timedelta(days=days) if delivered else None
    open_ = order["status"] == "delivered" and deadline is not None and _now() <= deadline
    taken: Dict[int, int] = {}
    for r in conn.execute(
        """
        SELECT ri.order_item_id, SUM(ri.qty) AS q FROM ws_return_items ri JOIN ws_returns r ON r.id = ri.return_id
        WHERE r.order_id = ? AND r.status != 'rejected' GROUP BY ri.order_item_id
        """,
        (order_id,),
    ):
        taken[r["order_item_id"]] = r["q"]
    items = []
    for i in conn.execute("SELECT * FROM ws_order_items WHERE order_id = ? ORDER BY id", (order_id,)):
        got = _delivered_qty(dict(i))
        left = got - taken.get(i["id"], 0)
        if got > 0:
            items.append({"id": i["id"], "name": i["name"], "unit": i["unit"], "unit_label": wo.UNIT_LABELS[i["unit"]],
                          "delivered": got, "returnable": max(0, left), "unit_price": i["unit_price"]})
    return {
        "open": open_ and any(x["returnable"] > 0 for x in items),
        "days": days,
        "deadline": deadline.isoformat(timespec="seconds") if deadline else None,
        "items": items,
        "reasons": REASONS,
    }


def request_return(conn: sqlite3.Connection, customer_id: int, order_id: int, lines: List[Dict[str, Any]],
                   note: str = "", photo_path: str = "") -> Dict[str, Any]:
    info = returnable(conn, customer_id, order_id)
    if not info["open"]:
        raise WholesaleError(f"الإرجاع متاح لمدة {info['days']} يوم من الاستلام بس." if info["deadline"] else "الطلب لازم يتسلّم الأول.")
    allowed = {i["id"]: i for i in info["items"]}
    clean = []
    for line in lines:
        try:
            item_id, qty, reason = int(line.get("item_id")), int(line.get("qty")), str(line.get("reason"))
        except (TypeError, ValueError):
            raise WholesaleError("فيه صنف مكتوب غلط.") from None
        if qty <= 0:
            continue
        if item_id not in allowed or qty > allowed[item_id]["returnable"]:
            raise WholesaleError("الكمية أكبر من اللي ينفع يترجع.")
        if reason not in REASONS:
            raise WholesaleError("اختار سبب الإرجاع.")
        clean.append((item_id, qty, reason))
    if not clean:
        raise WholesaleError("اختار صنف واحد على الأقل.")
    max_refund = round(sum(allowed[i]["unit_price"] * q for i, q, _ in clean), 2)
    return_id = conn.execute(
        "INSERT INTO ws_returns(order_id, customer_id, status, note, photo_path, max_refund, created_at) VALUES(?, ?, 'requested', ?, ?, ?, ?)",
        (order_id, customer_id, clean_text(note, 400), photo_path, max_refund, utc_now()),
    ).lastrowid
    for item_id, qty, reason in clean:
        conn.execute("INSERT INTO ws_return_items(return_id, order_item_id, qty, reason) VALUES(?, ?, ?, ?)", (return_id, item_id, qty, reason))
    wo.notify(conn, customer_id, "return", f"طلب الإرجاع لطلب {wo.order_number(order_id)} وصلنا", "هنراجعه ونبلغك.", order_id=order_id)
    return return_view(conn, return_id)


def _return(conn: sqlite3.Connection, return_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_returns WHERE id = ?", (return_id,)).fetchone()
    if not row:
        raise WholesaleError("المرتجع مش موجود.", 404)
    return dict(row)


def return_view(conn: sqlite3.Connection, return_id: int, admin: bool = False) -> Dict[str, Any]:
    r = _return(conn, return_id)
    items = []
    for i in conn.execute(
        """
        SELECT ri.*, oi.name, oi.unit, oi.unit_price, oi.unit_cost, oi.supplier_item_name, s.alias AS supplier
        FROM ws_return_items ri JOIN ws_order_items oi ON oi.id = ri.order_item_id
        LEFT JOIN ws_suppliers s ON s.id = oi.supplier_id WHERE ri.return_id = ? ORDER BY ri.id
        """,
        (return_id,),
    ):
        item = {
            "id": i["id"], "name": i["name"], "unit": i["unit"], "unit_label": wo.UNIT_LABELS[i["unit"]], "qty": i["qty"],
            "reason": i["reason"], "reason_label": REASONS[i["reason"]], "unit_price": i["unit_price"],
            "sound_qty": i["sound_qty"], "damaged_qty": i["damaged_qty"], "missing_qty": i["missing_qty"],
        }
        if admin:
            item["supplier"] = i["supplier"] or ""
        items.append(item)
    view = {
        "id": r["id"],
        "order_id": r["order_id"],
        "order_number": wo.order_number(r["order_id"]),
        "status": r["status"],
        "status_label": (ADMIN_LABEL if admin else CUSTOMER_LABEL)[r["status"]],
        "note": r["note"],
        "has_photo": bool(r["photo_path"]),
        "pickup": r["pickup"],
        "reject_reason": r["reject_reason"],
        "max_refund": r["max_refund"],
        "refund_amount": r["refund_amount"],
        "refund_cash": r["refund_cash"],
        "refund_credit": r["refund_credit"],
        "points_removed": r["points_removed"],
        "created_at": r["created_at"],
        "items": items,
    }
    if admin:
        who = conn.execute(
            "SELECT c.name, c.phone, t.shop_name, t.area, t.address FROM customers c LEFT JOIN ws_traders t ON t.customer_id = c.id WHERE c.id = ?",
            (r["customer_id"],),
        ).fetchone()
        driver = conn.execute("SELECT name FROM ws_pickers WHERE id = ?", (r["driver_id"],)).fetchone() if r["driver_id"] else None
        order = wo._order(conn, r["order_id"])
        view.update({
            "customer": dict(who), "driver_id": r["driver_id"], "driver_name": driver["name"] if driver else "",
            "payment_method": order["payment_method"], "suggested_refund": _inspected_value(conn, return_id),
        })
    return view


def customer_returns(conn: sqlite3.Connection, customer_id: int, order_id: int) -> List[Dict[str, Any]]:
    return [return_view(conn, r["id"]) for r in conn.execute(
        "SELECT id FROM ws_returns WHERE order_id = ? AND customer_id = ? ORDER BY id DESC", (order_id, customer_id))]


def admin_returns(conn: sqlite3.Connection, status: str = "") -> Dict[str, Any]:
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM ws_returns GROUP BY status")}
    sql = """
        SELECT r.id, r.order_id, r.status, r.max_refund, r.refund_amount, r.created_at, r.pickup, c.name, c.phone, t.shop_name,
               (SELECT COUNT(*) FROM ws_return_items ri WHERE ri.return_id = r.id) AS items,
               (SELECT GROUP_CONCAT(DISTINCT ri.reason) FROM ws_return_items ri WHERE ri.return_id = r.id) AS reasons
        FROM ws_returns r JOIN customers c ON c.id = r.customer_id LEFT JOIN ws_traders t ON t.customer_id = r.customer_id
    """
    params: List[Any] = []
    if status:
        sql += " WHERE r.status = ?"
        params.append(status)
    sql += " ORDER BY r.id DESC LIMIT 200"
    rows = []
    for r in conn.execute(sql, params):
        item = dict(r)
        item["order_number"] = wo.order_number(item["order_id"])
        item["status_label"] = ADMIN_LABEL[item["status"]]
        item["reasons"] = "، ".join(REASONS[x] for x in (item["reasons"] or "").split(",") if x)
        rows.append(item)
    return {"counts": counts, "items": rows}


def decide(conn: sqlite3.Connection, return_id: int, approve: bool, pickup: str, driver_id: Optional[int], reason: str, user_id: Optional[int]) -> Dict[str, Any]:
    r = _return(conn, return_id)
    if r["status"] != "requested":
        raise WholesaleError("المرتجع ده اتاخد فيه قرار.")
    now = utc_now()
    number = wo.order_number(r["order_id"])
    if not approve:
        reason = clean_text(reason, 300)
        if not reason:
            raise WholesaleError("اكتب سبب الرفض علشان يوصل للتاجر.")
        conn.execute("UPDATE ws_returns SET status = 'rejected', reject_reason = ?, decided_by = ?, accepted_at = ? WHERE id = ?", (reason, user_id, now, return_id))
        wo.notify(conn, r["customer_id"], "return", f"طلب الإرجاع لطلب {number} ماتقبلش", reason, order_id=r["order_id"])
        return return_view(conn, return_id, admin=True)
    if pickup not in ("driver", "store"):
        raise WholesaleError("اختار مين هيستلم المرتجع.")
    if pickup == "driver":
        driver = conn.execute("SELECT * FROM ws_pickers WHERE id = ? AND role IN ('driver', 'both') AND is_active = 1", (driver_id,)).fetchone()
        if not driver:
            raise WholesaleError("اختار المندوب.")
    conn.execute(
        "UPDATE ws_returns SET status = 'accepted', pickup = ?, driver_id = ?, decided_by = ?, accepted_at = ? WHERE id = ?",
        (pickup, driver_id if pickup == "driver" else None, user_id, now, return_id),
    )
    body = "المندوب هيعدّي يستلم الأصناف." if pickup == "driver" else "هات الأصناف للمحل."
    wo.notify(conn, r["customer_id"], "return", f"طلب الإرجاع لطلب {number} اتقبل", body, order_id=r["order_id"])
    return return_view(conn, return_id, admin=True)


# ------------------------------------------------------------ staff (phone)


def driver_pickups(conn: sqlite3.Connection, driver_id: int) -> List[Dict[str, Any]]:
    rows = []
    for r in conn.execute(
        """
        SELECT r.id, r.order_id, c.name, c.phone, t.shop_name, t.area, t.address
        FROM ws_returns r JOIN customers c ON c.id = r.customer_id LEFT JOIN ws_traders t ON t.customer_id = r.customer_id
        WHERE r.status = 'accepted' AND r.pickup = 'driver' AND r.driver_id = ? ORDER BY r.id
        """,
        (driver_id,),
    ):
        items = [
            {"name": i["name"], "qty": i["qty"], "unit_label": wo.UNIT_LABELS[i["unit"]]}
            for i in conn.execute(
                "SELECT ri.qty, oi.name, oi.unit FROM ws_return_items ri JOIN ws_order_items oi ON oi.id = ri.order_item_id WHERE ri.return_id = ?",
                (r["id"],),
            )
        ]
        rows.append({"id": r["id"], "order_number": wo.order_number(r["order_id"]), "shop": r["shop_name"] or r["name"],
                     "phone": r["phone"], "address": " - ".join(x for x in (r["area"], r["address"]) if x), "items": items})
    return rows


def picked_up(conn: sqlite3.Connection, return_id: int, driver_id: int) -> None:
    r = _return(conn, return_id)
    if r["status"] != "accepted" or r["pickup"] != "driver" or r["driver_id"] != driver_id:
        raise WholesaleError("المرتجع ده مش معاك.", 409)
    conn.execute("UPDATE ws_returns SET status = 'picked_up', picked_up_at = ? WHERE id = ?", (utc_now(), return_id))
    wo.notify(conn, r["customer_id"], "return", "المندوب استلم الأصناف المرتجعة",
              f"هنفحصها ونرجّعلك المبلغ. طلب {wo.order_number(r['order_id'])}.", order_id=r["order_id"])


def to_inspect(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Returns waiting for a store employee: collected by a driver, or brought by the trader."""
    rows = []
    for r in conn.execute(
        """
        SELECT r.id FROM ws_returns r
        WHERE r.status = 'picked_up' OR (r.status = 'accepted' AND r.pickup = 'store') ORDER BY r.id
        """
    ):
        v = return_view(conn, r["id"])
        rows.append({"id": v["id"], "order_number": v["order_number"], "pickup": v["pickup"],
                     "items": [{k: i[k] for k in ("id", "name", "qty", "unit_label", "reason_label")} for i in v["items"]],
                     "note": v["note"]})
    return rows


def inspect(conn: sqlite3.Connection, return_id: int, results: Dict[str, Dict[str, int]], picker_id: int) -> Dict[str, Any]:
    r = _return(conn, return_id)
    if not (r["status"] == "picked_up" or (r["status"] == "accepted" and r["pickup"] == "store")):
        raise WholesaleError("المرتجع ده مش مستني فحص.", 409)
    items = [dict(i) for i in conn.execute("SELECT * FROM ws_return_items WHERE return_id = ?", (return_id,))]
    for i in items:
        got = results.get(str(i["id"]), {})
        sound, damaged = int(got.get("sound", i["qty"]) or 0), int(got.get("damaged", 0) or 0)
        if sound < 0 or damaged < 0 or sound + damaged > i["qty"]:
            raise WholesaleError("مجموع السليم والتالف أكبر من المرتجع.")
        conn.execute(
            "UPDATE ws_return_items SET sound_qty = ?, damaged_qty = ?, missing_qty = ? WHERE id = ?",
            (sound, damaged, i["qty"] - sound - damaged, i["id"]),
        )
    now = utc_now()
    conn.execute(
        "UPDATE ws_returns SET status = 'inspected', inspected_at = ?, inspected_by = ?, picked_up_at = COALESCE(picked_up_at, ?) WHERE id = ?",
        (now, picker_id, now, return_id),
    )
    value = _inspected_value(conn, return_id)
    conn.execute("UPDATE ws_returns SET refund_amount = ? WHERE id = ?", (value, return_id))
    wo.notify(conn, r["customer_id"], "return", "الأصناف المرتجعة اتفحصت",
              f"المبلغ اللي هيرجعلك {wo.money(value)} ج. طلب {wo.order_number(r['order_id'])}.", order_id=r["order_id"])
    return {"id": return_id, "refund": value}


def _inspected_value(conn: sqlite3.Connection, return_id: int) -> float:
    """Sound and damaged items are paid back; what did not arrive is not."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM((COALESCE(ri.sound_qty, 0) + COALESCE(ri.damaged_qty, 0)) * oi.unit_price), 0)
        FROM ws_return_items ri JOIN ws_order_items oi ON oi.id = ri.order_item_id WHERE ri.return_id = ?
        """,
        (return_id,),
    ).fetchone()
    return round(row[0], 2)


# ------------------------------------------------------------------- refund


def _take_back_points(conn: sqlite3.Connection, r: Dict[str, Any], order: Dict[str, Any], amount: float) -> int:
    earned = int(order["points_awarded"] or 0)
    if earned <= 0 or float(order["final_total"]) <= 0:
        return 0
    refunded_before = conn.execute(
        "SELECT COALESCE(SUM(refund_amount), 0), COALESCE(SUM(points_removed), 0) FROM ws_returns WHERE order_id = ? AND status = 'refunded' AND id != ?",
        (order["id"], r["id"]),
    ).fetchone()
    share = min(1.0, (float(refunded_before[0]) + amount) / float(order["final_total"]))
    remove = max(0, min(earned, math.floor(earned * share + 1e-9)) - int(refunded_before[1]))
    if remove <= 0:
        return 0
    from .wholesale_points import take_back

    take_back(conn, order["customer_id"], remove, f"مرتجع من طلب رقم {wo.order_number(order['id'])}", r["id"])
    return remove


def refund(conn: sqlite3.Connection, return_id: int, amount: float, user_id: Optional[int]) -> Dict[str, Any]:
    r = _return(conn, return_id)
    if r["status"] != "inspected":
        raise WholesaleError("المرتجع لازم يتفحص الأول.")
    top = _inspected_value(conn, return_id)
    amount = round(float(amount), 2)
    if amount < 0 or amount > top + 0.009:
        raise WholesaleError(f"المبلغ لازم يكون من 0 لحد {wo.money(top)} ج.")
    order = wo._order(conn, r["order_id"])
    credit_part = 0.0
    if order["payment_method"] == "credit" and amount > 0:
        balance = wp.credit_summary(conn, r["customer_id"])["balance"]
        credit_part = round(min(amount, max(0.0, balance)), 2)
        if credit_part > 0:
            conn.execute(
                "INSERT INTO ws_credit_entries(customer_id, kind, amount, order_id, note, created_by, created_at) VALUES(?, 'adjustment', ?, ?, ?, ?, ?)",
                (r["customer_id"], -credit_part, order["id"], f"مرتجع طلب {wo.order_number(order['id'])}", user_id, utc_now()),
            )
    cash_part = round(amount - credit_part, 2)
    points = _take_back_points(conn, r, order, amount)
    conn.execute(
        """
        UPDATE ws_returns SET status = 'refunded', refund_amount = ?, refund_cash = ?, refund_credit = ?, points_removed = ?,
            refunded_at = ?, decided_by = ? WHERE id = ?
        """,
        (amount, cash_part, credit_part, points, utc_now(), user_id, return_id),
    )
    parts = []
    if cash_part:
        parts.append(f"{wo.money(cash_part)} ج كاش")
    if credit_part:
        parts.append(f"{wo.money(credit_part)} ج اتخصموا من الآجل")
    body = ("رجعلك " + " و".join(parts) + ".") if parts else "مفيش مبلغ مستحق للمرتجع ده."
    if points:
        body += f" واتشال {points} نقطة."
    wo.notify(conn, r["customer_id"], "return", f"المرتجع من طلب {wo.order_number(order['id'])} اتقفل", body, order_id=order["id"])
    return return_view(conn, return_id, admin=True)


def picking_errors(conn: sqlite3.Connection, picker_id: int, since: str) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM ws_return_items ri JOIN ws_returns r ON r.id = ri.return_id JOIN ws_orders o ON o.id = r.order_id
        WHERE ri.reason = 'wrong_item' AND r.status NOT IN ('requested', 'rejected') AND o.picker_id = ? AND r.created_at >= ?
        """,
        (picker_id, since),
    ).fetchone()[0]


# ------------------------------------------------------------------ ratings


def rate(conn: sqlite3.Connection, customer_id: int, order_id: int, stars: int, liked: List[str], improve: str) -> Dict[str, Any]:
    order = wo._order(conn, order_id)
    if order["customer_id"] != customer_id:
        raise WholesaleError("الطلب مش موجود.", 404)
    if order["status"] != "delivered":
        raise WholesaleError("التقييم بعد استلام الطلب.")
    if not 1 <= int(stars) <= 5:
        raise WholesaleError("اختار عدد النجوم.")
    liked = [x for x in liked if x in LIKED][:6]
    improve = clean_text(improve, 600)
    now = utc_now()
    try:
        rating_id = conn.execute(
            "INSERT INTO ws_ratings(order_id, customer_id, stars, liked, improve, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (order_id, customer_id, int(stars), json.dumps(liked, ensure_ascii=False), improve, now),
        ).lastrowid
    except sqlite3.IntegrityError:
        raise WholesaleError("قيّمت الطلب ده قبل كده.") from None
    if improve:
        conn.execute(
            "INSERT INTO ws_feedback(customer_id, kind, order_id, rating_id, text, created_at) VALUES(?, 'rating', ?, ?, ?, ?)",
            (customer_id, order_id, rating_id, improve, now),
        )
    return {"id": rating_id}


def rating_for(conn: sqlite3.Connection, order_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT stars, liked, improve, created_at FROM ws_ratings WHERE order_id = ?", (order_id,)).fetchone()
    if not row:
        return None
    return {**dict(row), "liked": json.loads(row["liked"] or "[]")}


def ratings_report(conn: sqlite3.Connection) -> Dict[str, Any]:
    rows = [
        {**dict(r), "liked": json.loads(r["liked"] or "[]"), "order_number": wo.order_number(r["order_id"])}
        for r in conn.execute(
            """
            SELECT g.*, c.name, t.shop_name FROM ws_ratings g JOIN customers c ON c.id = g.customer_id
            LEFT JOIN ws_traders t ON t.customer_id = g.customer_id ORDER BY g.id DESC LIMIT 300
            """
        )
    ]
    dist = {s: 0 for s in range(1, 6)}
    liked: Dict[str, int] = {}
    for r in rows:
        dist[r["stars"]] += 1
        for x in r["liked"]:
            liked[x] = liked.get(x, 0) + 1
    total = len(rows)
    return {
        "count": total,
        "average": round(sum(r["stars"] for r in rows) / total, 2) if total else None,
        "distribution": dist,
        "liked": sorted(liked.items(), key=lambda kv: -kv[1]),
        "items": rows,
    }


# ----------------------------------------------------------------- feedback


def send_feedback(conn: sqlite3.Connection, customer_id: int, kind: str, text: str, order_id: Optional[int] = None) -> Dict[str, Any]:
    if kind not in ("complaint", "suggestion"):
        raise WholesaleError("اختار شكوى أو اقتراح.")
    text = clean_text(text, 1000)
    if len(text) < 3:
        raise WholesaleError("اكتب التفاصيل.")
    if order_id:
        order = wo._order(conn, order_id)
        if order["customer_id"] != customer_id:
            raise WholesaleError("الطلب مش موجود.", 404)
    open_count = conn.execute("SELECT COUNT(*) FROM ws_feedback WHERE customer_id = ? AND status = 'open'", (customer_id,)).fetchone()[0]
    if open_count >= 20:
        raise WholesaleError("عندك رسايل كتير مفتوحة. استنى ردّنا.")
    fid = conn.execute(
        "INSERT INTO ws_feedback(customer_id, kind, order_id, text, created_at) VALUES(?, ?, ?, ?, ?)",
        (customer_id, kind, order_id or None, text, utc_now()),
    ).lastrowid
    return {"id": fid}


def my_feedback(conn: sqlite3.Connection, customer_id: int) -> List[Dict[str, Any]]:
    return [
        {**dict(r), "order_number": wo.order_number(r["order_id"]) if r["order_id"] else None}
        for r in conn.execute(
            "SELECT id, kind, order_id, text, status, reply, replied_at, created_at FROM ws_feedback WHERE customer_id = ? AND kind != 'rating' ORDER BY id DESC LIMIT 50",
            (customer_id,),
        )
    ]


def admin_feedback(conn: sqlite3.Connection, kind: str = "", status: str = "open") -> Dict[str, Any]:
    counts = {f"{r['kind']}:{r['status']}": r["n"] for r in conn.execute("SELECT kind, status, COUNT(*) AS n FROM ws_feedback GROUP BY kind, status")}
    sql = """
        SELECT f.*, c.name, c.phone, t.shop_name, g.stars FROM ws_feedback f JOIN customers c ON c.id = f.customer_id
        LEFT JOIN ws_traders t ON t.customer_id = f.customer_id LEFT JOIN ws_ratings g ON g.id = f.rating_id WHERE 1 = 1
    """
    params: List[Any] = []
    if kind in ("complaint", "suggestion", "rating"):
        sql += " AND f.kind = ?"
        params.append(kind)
    if status in ("open", "resolved"):
        sql += " AND f.status = ?"
        params.append(status)
    sql += " ORDER BY f.id DESC LIMIT 300"
    items = [{**dict(r), "order_number": wo.order_number(r["order_id"]) if r["order_id"] else None} for r in conn.execute(sql, params)]
    return {"counts": counts, "items": items}


def reply_feedback(conn: sqlite3.Connection, feedback_id: int, reply: Optional[str], resolve: bool) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_feedback WHERE id = ?", (feedback_id,)).fetchone()
    if not row:
        raise WholesaleError("الرسالة مش موجودة.", 404)
    now = utc_now()
    reply = clean_text(reply, 1000) if reply is not None else None
    if reply:
        conn.execute("UPDATE ws_feedback SET reply = ?, replied_at = ? WHERE id = ?", (reply, now, feedback_id))
        title = {"complaint": "رد على شكوتك", "suggestion": "رد على اقتراحك", "rating": "رد على تقييمك"}[row["kind"]]
        wo.notify(conn, row["customer_id"], "feedback", title, reply, order_id=row["order_id"])
    if resolve:
        conn.execute("UPDATE ws_feedback SET status = 'resolved', resolved_at = ? WHERE id = ?", (now, feedback_id))
    elif reply is None:
        conn.execute("UPDATE ws_feedback SET status = 'open', resolved_at = NULL WHERE id = ?", (feedback_id,))
    return dict(conn.execute("SELECT * FROM ws_feedback WHERE id = ?", (feedback_id,)).fetchone())
