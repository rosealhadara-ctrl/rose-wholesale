"""Wholesale phase 8: Rose Plus (روز بلس).

A trader subscribes for one month (30 days) or three months (90 days).
While the subscription is active:
    - delivery is free (setting ``plus_free_delivery``)
    - the trader pays only ``plus_fee_share`` percent of the payment commission
    - their orders go first in the picking queue

The benefits are locked on the order when it is sent (``ws_orders.is_plus``),
so an order keeps them even if the subscription ends before delivery.

Paying for Plus: cash at the store, or an InstaPay / wallet transfer with a
screenshot. The admin confirms the money, which starts the subscription. A
renewal starts the day the current one ends, so no days are lost.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db import utc_now
from .wholesale_pricing import get_settings
from .wholesale_service import WholesaleError, clean_text

PLANS = {"month": ("شهر", 30), "quarter": ("3 شهور", 90)}
METHODS = {"cash": "كاش في المحل", "instapay": "انستاباي", "wallet": "محفظة", "admin": "من الإدارة"}
STATUS_LABEL = {"requested": "مستني التأكيد", "active": "شغّال", "rejected": "اترفض", "cancelled": "اتلغى", "expired": "خلص"}
REMIND_DAYS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(settings: Dict[str, str], key: str) -> float:
    try:
        return float(settings.get(key) or 0)
    except ValueError:
        return 0.0


def prices(settings: Dict[str, str]) -> Dict[str, float]:
    return {"month": _num(settings, "plus_price_month"), "quarter": _num(settings, "plus_price_quarter")}


def fee_share(settings: Dict[str, str]) -> float:
    """Part of the commission a Plus member pays, 0..1."""
    raw = settings.get("plus_fee_share")
    percent = 50.0 if raw in (None, "") else _num(settings, "plus_fee_share")
    return max(0.0, min(100.0, percent)) / 100.0


def active_plus(conn: sqlite3.Connection, customer_id: int) -> Optional[Dict[str, Any]]:
    now = utc_now()
    row = conn.execute(
        """
        SELECT * FROM ws_plus_subscriptions
        WHERE customer_id = ? AND status = 'active' AND starts_at <= ? AND ends_at > ?
        ORDER BY ends_at DESC LIMIT 1
        """,
        (customer_id, now, now),
    ).fetchone()
    return dict(row) if row else None


def _latest_end(conn: sqlite3.Connection, customer_id: int) -> Optional[datetime]:
    row = conn.execute(
        "SELECT MAX(ends_at) FROM ws_plus_subscriptions WHERE customer_id = ? AND status = 'active'", (customer_id,)
    ).fetchone()
    end = _parse(row[0]) if row and row[0] else None
    return end if end and end > _now() else None


def benefits(conn: sqlite3.Connection, customer_id: int, settings: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """What Plus changes for this trader right now (used by checkout and submit)."""
    settings = settings or get_settings(conn)
    active = active_plus(conn, customer_id)
    return {
        "active": bool(active),
        "free_delivery": bool(active) and settings.get("plus_free_delivery", "1") == "1",
        "fee_share": fee_share(settings) if active else 1.0,
        "ends_at": active["ends_at"] if active else None,
    }


def _days_left(ends_at: Optional[str]) -> int:
    end = _parse(ends_at)
    if not end:
        return 0
    seconds = (end - _now()).total_seconds()
    return max(0, int(-(-seconds // 86400)))


def _row_view(r: Dict[str, Any]) -> Dict[str, Any]:
    status = r["status"]
    if status == "active" and _parse(r["ends_at"]) and _parse(r["ends_at"]) <= _now():
        status = "expired"
    return {
        "id": r["id"],
        "plan": r["plan"],
        "plan_label": PLANS[r["plan"]][0],
        "price": r["price"],
        "method": r["method"],
        "method_label": METHODS[r["method"]],
        "status": status,
        "status_label": STATUS_LABEL[status],
        "starts_at": r["starts_at"],
        "ends_at": r["ends_at"],
        "reject_reason": r["reject_reason"],
        "created_at": r["created_at"],
    }


def saved_total(conn: sqlite3.Connection, customer_id: int) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(plus_delivery_saved + plus_fee_saved), 0) FROM ws_orders
        WHERE customer_id = ? AND is_plus = 1 AND status != 'cancelled'
        """,
        (customer_id,),
    ).fetchone()
    return round(float(row[0] or 0), 2)


def plus_status(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    """The trader's Plus page. Also sends the "ending soon" reminder once."""
    from . import wholesale_orders as wo
    from . import wholesale_payments as wp

    settings = get_settings(conn)
    active = active_plus(conn, customer_id)
    if active and not active["reminded"] and _days_left(active["ends_at"]) <= REMIND_DAYS:
        renewed = conn.execute(
            "SELECT 1 FROM ws_plus_subscriptions WHERE customer_id = ? AND status IN ('active', 'requested') AND id != ? AND (starts_at IS NULL OR starts_at >= ?)",
            (customer_id, active["id"], active["ends_at"]),
        ).fetchone()
        conn.execute("UPDATE ws_plus_subscriptions SET reminded = 1 WHERE id = ?", (active["id"],))
        if not renewed:
            wo.notify(conn, customer_id, "plus", "اشتراك روز بلس قرب يخلص",
                      f"باقي {_days_left(active['ends_at'])} يوم. جدّد من صفحة روز بلس علشان التوصيل يفضل مجاني.")
    pending = conn.execute(
        "SELECT * FROM ws_plus_subscriptions WHERE customer_id = ? AND status = 'requested' ORDER BY id DESC LIMIT 1", (customer_id,)
    ).fetchone()
    upcoming = None
    if active:
        nxt = conn.execute(
            "SELECT * FROM ws_plus_subscriptions WHERE customer_id = ? AND status = 'active' AND starts_at >= ? ORDER BY starts_at LIMIT 1",
            (customer_id, active["ends_at"]),
        ).fetchone()
        upcoming = _row_view(dict(nxt)) if nxt else None
    last = conn.execute(
        "SELECT * FROM ws_plus_subscriptions WHERE customer_id = ? ORDER BY id DESC LIMIT 1", (customer_id,)
    ).fetchone()
    history = [_row_view(dict(r)) for r in conn.execute(
        "SELECT * FROM ws_plus_subscriptions WHERE customer_id = ? ORDER BY id DESC LIMIT 12", (customer_id,)
    )]
    delivery, _, _ = wp.delivery_terms(conn, customer_id, settings)
    transfer = [
        {"code": m["code"], "label": m["label"], "account": m["account"], "instructions": m["instructions"]}
        for m in wp.list_methods(conn) if m["code"] in ("instapay", "wallet") and m["is_enabled"] and m["account"]
    ]
    return {
        "enabled": settings.get("plus_enabled") == "1",
        "prices": prices(settings),
        "plans": [{"code": k, "label": v[0], "days": v[1], "price": prices(settings)[k]} for k, v in PLANS.items() if prices(settings)[k] > 0],
        "free_delivery": settings.get("plus_free_delivery", "1") == "1",
        "fee_share_percent": round(fee_share(settings) * 100, 2),
        "delivery_fee": delivery,
        "active": {**_row_view(active), "days_left": _days_left(active["ends_at"])} if active else None,
        "upcoming": upcoming,
        "pending": _row_view(dict(pending)) if pending else None,
        "last_rejection": _row_view(dict(last)) if last and last["status"] == "rejected" else None,
        "saved_total": saved_total(conn, customer_id),
        "transfer_methods": transfer,
        "history": history,
    }


def request_plus(conn: sqlite3.Connection, customer_id: int, plan: str, method_code: str,
                 proof_path: str = "", sender: str = "") -> Dict[str, Any]:
    settings = get_settings(conn)
    if settings.get("plus_enabled") != "1":
        raise WholesaleError("روز بلس مش متاح دلوقتي.")
    if plan not in PLANS:
        raise WholesaleError("اختار مدة الاشتراك.")
    price = prices(settings)[plan]
    if price <= 0:
        raise WholesaleError("المدة دي مش متاحة.")
    if method_code not in ("cash", "instapay", "wallet"):
        raise WholesaleError("اختار طريقة الدفع.")
    if method_code != "cash":
        from . import wholesale_payments as wp

        m = wp.method(conn, method_code)
        if not m["is_enabled"] or not m["account"]:
            raise WholesaleError("طريقة الدفع دي مش متاحة دلوقتي.")
        if not proof_path:
            raise WholesaleError("ارفع صورة التحويل.")
    if conn.execute("SELECT 1 FROM ws_plus_subscriptions WHERE customer_id = ? AND status = 'requested'", (customer_id,)).fetchone():
        raise WholesaleError("عندك طلب اشتراك لسه بيتراجع.")
    sub_id = conn.execute(
        """
        INSERT INTO ws_plus_subscriptions(customer_id, plan, price, method, proof_path, sender, status, created_at)
        VALUES(?, ?, ?, ?, ?, ?, 'requested', ?)
        """,
        (customer_id, plan, price, method_code, proof_path, clean_text(sender, 80), utc_now()),
    ).lastrowid
    return _row_view(dict(conn.execute("SELECT * FROM ws_plus_subscriptions WHERE id = ?", (sub_id,)).fetchone()))


def _sub(conn: sqlite3.Connection, sub_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_plus_subscriptions WHERE id = ?", (sub_id,)).fetchone()
    if not row:
        raise WholesaleError("الاشتراك مش موجود.", 404)
    return dict(row)


def _activate(conn: sqlite3.Connection, sub: Dict[str, Any], user_id: Optional[int]) -> Dict[str, Any]:
    from . import wholesale_orders as wo

    start = _latest_end(conn, sub["customer_id"]) or _now()
    end = start + timedelta(days=PLANS[sub["plan"]][1])
    now = utc_now()
    conn.execute(
        "UPDATE ws_plus_subscriptions SET status = 'active', starts_at = ?, ends_at = ?, decided_at = ?, decided_by = ? WHERE id = ?",
        (_iso(start), _iso(end), now, user_id, sub["id"]),
    )
    renewal = start > _now() + timedelta(minutes=1)
    wo.notify(conn, sub["customer_id"], "plus",
              "اتجدد اشتراك روز بلس" if renewal else "اشتراك روز بلس اتفعّل",
              f"شغّال لحد {end.date().isoformat()}. التوصيل مجاني وطلباتك ليها أولوية في التجهيز.")
    return _row_view(dict(conn.execute("SELECT * FROM ws_plus_subscriptions WHERE id = ?", (sub["id"],)).fetchone()))


def decide_plus(conn: sqlite3.Connection, sub_id: int, approve: bool, reason: str, user_id: Optional[int]) -> Dict[str, Any]:
    from . import wholesale_orders as wo

    sub = _sub(conn, sub_id)
    if sub["status"] != "requested":
        raise WholesaleError("الطلب ده اتاخد فيه قرار.")
    if approve:
        return _activate(conn, sub, user_id)
    reason = clean_text(reason, 300)
    if not reason:
        raise WholesaleError("اكتب سبب الرفض علشان يوصل للتاجر.")
    conn.execute(
        "UPDATE ws_plus_subscriptions SET status = 'rejected', reject_reason = ?, decided_at = ?, decided_by = ? WHERE id = ?",
        (reason, utc_now(), user_id, sub_id),
    )
    wo.notify(conn, sub["customer_id"], "plus", "طلب اشتراك روز بلس ماتقبلش", reason)
    return _row_view(_sub(conn, sub_id))


def grant_plus(conn: sqlite3.Connection, customer_id: int, plan: str, user_id: Optional[int], paid: bool = True) -> Dict[str, Any]:
    """The admin adds Plus directly, e.g. the trader paid cash at the store."""
    from . import wholesale_orders as wo

    wo.require_trader(conn, customer_id)
    if plan not in PLANS:
        raise WholesaleError("اختار مدة الاشتراك.")
    price = prices(get_settings(conn))[plan] if paid else 0.0
    sub_id = conn.execute(
        "INSERT INTO ws_plus_subscriptions(customer_id, plan, price, method, status, created_at) VALUES(?, ?, ?, ?, 'requested', ?)",
        (customer_id, plan, price, "cash" if paid else "admin", utc_now()),
    ).lastrowid
    return _activate(conn, _sub(conn, sub_id), user_id)


def cancel_plus(conn: sqlite3.Connection, sub_id: int, user_id: Optional[int]) -> Dict[str, Any]:
    from . import wholesale_orders as wo

    sub = _sub(conn, sub_id)
    if sub["status"] != "active" or (_parse(sub["ends_at"]) and _parse(sub["ends_at"]) <= _now()):
        raise WholesaleError("الاشتراك ده مش شغّال.")
    conn.execute(
        "UPDATE ws_plus_subscriptions SET status = 'cancelled', decided_at = ?, decided_by = ? WHERE id = ?",
        (utc_now(), user_id, sub_id),
    )
    wo.notify(conn, sub["customer_id"], "plus", "اشتراك روز بلس اتلغى", "لو فيه فلوس ليك، الإدارة هتتواصل معاك.")
    return _row_view(_sub(conn, sub_id))


def proof_path(conn: sqlite3.Connection, sub_id: int) -> str:
    sub = _sub(conn, sub_id)
    if not sub["proof_path"]:
        raise WholesaleError("مفيش صورة.", 404)
    return sub["proof_path"]


def admin_plus(conn: sqlite3.Connection) -> Dict[str, Any]:
    settings = get_settings(conn)
    now = utc_now()
    base = """
        SELECT s.*, c.name, c.phone, t.shop_name FROM ws_plus_subscriptions s
        JOIN customers c ON c.id = s.customer_id LEFT JOIN ws_traders t ON t.customer_id = s.customer_id
    """

    def rows(where: str, params: tuple, order: str) -> List[Dict[str, Any]]:
        out = []
        for r in conn.execute(base + where + order, params):
            item = _row_view(dict(r))
            item.update({"customer_id": r["customer_id"], "name": r["name"], "phone": r["phone"],
                         "shop_name": r["shop_name"] or "", "has_proof": bool(r["proof_path"]), "sender": r["sender"],
                         "days_left": _days_left(r["ends_at"])})
            out.append(item)
        return out

    requests = rows(" WHERE s.status = 'requested'", (), " ORDER BY s.id")
    active = rows(" WHERE s.status = 'active' AND s.starts_at <= ? AND s.ends_at > ?", (now, now), " ORDER BY s.ends_at")
    month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    revenue = conn.execute(
        "SELECT COALESCE(SUM(price), 0) FROM ws_plus_subscriptions WHERE status IN ('active', 'cancelled') AND decided_at >= ?",
        (_iso(month_start),),
    ).fetchone()[0]
    saved = conn.execute(
        "SELECT COALESCE(SUM(plus_delivery_saved + plus_fee_saved), 0) FROM ws_orders WHERE is_plus = 1 AND status != 'cancelled' AND created_at >= ?",
        (_iso(month_start),),
    ).fetchone()[0]
    return {
        "settings": {k: settings.get(k, "") for k in ("plus_enabled", "plus_price_month", "plus_price_quarter", "plus_free_delivery", "plus_fee_share")},
        "requests": requests,
        "active": active,
        "stats": {
            "active": len({a["customer_id"] for a in active}),
            "ending_soon": len([a for a in active if a["days_left"] <= REMIND_DAYS]),
            "revenue_month": round(float(revenue), 2),
            "saved_month": round(float(saved), 2),
        },
    }
