"""Wholesale phase 4: payment methods, checkout totals, transfers, credit (آجل), points.

Totals of an order
    products   = items total (before review) or the final products total
    delivery   = flat delivery fee locked at submit (zones come in phase 6)
    fee        = payment method commission on (products + delivery)
    grand      = products + delivery + fee (fee only when the trader pays it)

Payment status
    cod      cash on delivery, collected when delivered
    unpaid   InstaPay / wallet: trader pays the final amount after review
    pending  transfer proof uploaded, waiting for the admin
    paid     money received
    credit   on account: charged to the trader's credit ledger when delivered

Credit (آجل) lives in Rose only and is separate from the فكة feature.
Points: one wholesale point per N EGP of the final products total, credited after
delivery into the wholesale points balance (app/wholesale_points.py).
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db import utc_now
from .wholesale_pricing import get_settings
from .wholesale_service import WholesaleError, clean_text

TRANSFER_METHODS = ("instapay", "wallet")
METHOD_CODES = ("cash", "instapay", "wallet", "credit")
DELIVERABLE = ("received", "preparing", "ready", "out_for_delivery")
DELIVERED_BY_ADMIN = ("received", "preparing", "ready")
PAYMENT_LABEL = {
    "cod": "كاش عند الاستلام",
    "unpaid": "مستني الدفع",
    "pending": "التحويل بيتراجع",
    "paid": "اتدفع",
    "credit": "على الحساب",
}


def _num(settings: Dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(settings.get(key, default) or 0)
    except ValueError:
        return default


def _round(value: float) -> float:
    return round(float(value) + 1e-9, 2)


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------- methods


def list_methods(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM ws_payment_methods ORDER BY sort_order, code")]


def method(conn: sqlite3.Connection, code: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_payment_methods WHERE code = ?", (code,)).fetchone()
    if not row:
        raise WholesaleError("طريقة الدفع مش موجودة.")
    return dict(row)


def update_method(conn: sqlite3.Connection, code: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    current = method(conn, code)
    values = dict(current)
    if data.get("is_enabled") is not None:
        values["is_enabled"] = 1 if data["is_enabled"] else 0
    for key, limit in (("label", 60), ("account", 120), ("instructions", 600)):
        if data.get(key) is not None:
            values[key] = clean_text(data[key], limit) if key != "instructions" else str(data[key]).strip()[:limit]
    if not values["label"]:
        raise WholesaleError("اكتب اسم طريقة الدفع.")
    if data.get("fee_type") is not None:
        if data["fee_type"] not in ("percent", "fixed"):
            raise WholesaleError("نوع العمولة غلط.")
        values["fee_type"] = data["fee_type"]
    if data.get("fee_payer") is not None:
        if data["fee_payer"] not in ("customer", "store"):
            raise WholesaleError("مين بيتحمل العمولة غلط.")
        values["fee_payer"] = data["fee_payer"]
    for key, top in (("fee_value", 100000), ("max_amount", 10_000_000)):
        if data.get(key) is not None:
            number = float(data[key])
            if number < 0 or number > top:
                raise WholesaleError("فيه رقم برّه الحدود.")
            values[key] = number
    if values["fee_type"] == "percent" and values["fee_value"] > 50:
        raise WholesaleError("نسبة العمولة كبيرة أوي.")
    if code in TRANSFER_METHODS and values["is_enabled"] and not values["account"]:
        raise WholesaleError("اكتب عنوان انستاباي أو رقم المحفظة قبل ما تشغّلها.")
    if code in ("cash", "credit"):
        values["fee_value"] = 0  # no commission on cash or credit
    conn.execute(
        """
        UPDATE ws_payment_methods SET label = ?, is_enabled = ?, account = ?, instructions = ?, fee_type = ?,
            fee_value = ?, fee_payer = ?, max_amount = ?, updated_at = ? WHERE code = ?
        """,
        (values["label"], values["is_enabled"], values["account"], values["instructions"], values["fee_type"],
         values["fee_value"], values["fee_payer"], values["max_amount"], utc_now(), code),
    )
    return list_methods(conn)


def fee_for(method_row: Dict[str, Any], base: float) -> float:
    if method_row["code"] not in TRANSFER_METHODS or base <= 0:
        return 0.0
    if method_row["fee_type"] == "fixed":
        return _round(method_row["fee_value"])
    return _round(base * float(method_row["fee_value"]) / 100.0)


def totals(products: float, delivery: float, method_row: Dict[str, Any], fee_share: float = 1.0) -> Dict[str, Any]:
    """``fee_share`` < 1 for Rose Plus members, who pay part of the commission."""
    base = _round(products + delivery)
    full_fee = fee_for(method_row, base)
    fee = _round(full_fee * fee_share) if fee_share < 1 else full_fee
    customer_pays_fee = method_row["fee_payer"] == "customer"
    return {
        "products": _round(products),
        "delivery": _round(delivery),
        "fee": fee,
        "fee_saved": _round(full_fee - fee) if customer_pays_fee else 0.0,
        "fee_payer": method_row["fee_payer"],
        "grand": _round(base + (fee if customer_pays_fee else 0)),
    }


def _order_fee_share(conn: sqlite3.Connection, order: Dict[str, Any]) -> float:
    if not order.get("is_plus"):
        return 1.0
    from .wholesale_plus import fee_share

    return fee_share(get_settings(conn))


def products_total(order: Dict[str, Any]) -> float:
    return float(order["items_total"] if order["status"] == "submitted" else order["final_total"])


def recalc_order(conn: sqlite3.Connection, order_id: int) -> Dict[str, Any]:
    order = dict(conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order_id,)).fetchone())
    t = totals(products_total(order), order["delivery_fee"], method(conn, order["payment_method"]), _order_fee_share(conn, order))
    conn.execute(
        "UPDATE ws_orders SET payment_fee = ?, fee_payer = ?, grand_total = ?, plus_fee_saved = ? WHERE id = ?",
        (t["fee"], t["fee_payer"], t["grand"], t["fee_saved"] if order.get("is_plus") else 0, order_id),
    )
    if order["payment_status"] == "pending":
        pass
    elif order["payment_method"] in TRANSFER_METHODS and order["paid_amount"] > 0:
        status = "paid" if order["paid_amount"] + 0.009 >= t["grand"] else "unpaid"
        conn.execute("UPDATE ws_orders SET payment_status = ? WHERE id = ?", (status, order_id))
    return t


def payment_view(conn: sqlite3.Connection, order: Dict[str, Any]) -> Dict[str, Any]:
    m = method(conn, order["payment_method"])
    paid = float(order["paid_amount"] or 0)
    refunded = float(order["refunded_amount"] or 0)
    due_back = 0.0
    if order["status"] == "cancelled":
        due_back = paid - refunded
    elif paid > order["grand_total"]:
        due_back = paid - order["grand_total"] - refunded
    pending = conn.execute(
        "SELECT id, amount, created_at FROM ws_payments WHERE order_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (order["id"],),
    ).fetchone()
    last_rejected = conn.execute(
        "SELECT reject_reason, decided_at FROM ws_payments WHERE order_id = ? AND status = 'rejected' ORDER BY id DESC LIMIT 1",
        (order["id"],),
    ).fetchone()
    can_pay = (
        order["payment_method"] in TRANSFER_METHODS
        and order["status"] in ("reviewed",) + DELIVERABLE
        and order["payment_status"] == "unpaid"
        and order["grand_total"] - paid > 0.009
    )
    return {
        "method": order["payment_method"],
        "method_label": m["label"],
        "status": order["payment_status"],
        "status_label": PAYMENT_LABEL.get(order["payment_status"], order["payment_status"]),
        "products": _round(products_total(order)),
        "delivery": order["delivery_fee"],
        "fee": order["payment_fee"],
        "fee_payer": order["fee_payer"],
        "grand": order["grand_total"],
        "paid": _round(paid),
        "to_pay": _round(max(0.0, order["grand_total"] - paid)) if can_pay else 0,
        "can_pay": can_pay,
        "account": m["account"] if order["payment_method"] in TRANSFER_METHODS else "",
        "instructions": m["instructions"],
        "pending_payment": dict(pending) if pending else None,
        "last_rejection": dict(last_rejected) if last_rejected and order["payment_status"] == "unpaid" else None,
        "refund_due": _round(max(0.0, due_back)),
        "refunded": _round(refunded),
        "can_change_method": order["status"] in ("submitted", "reviewed", "received") and order["payment_status"] in ("unpaid", "cod", "credit") and paid <= 0,
        "points_awarded": order["points_awarded"],
        "is_plus": bool(order.get("is_plus")),
        "plus_saved": _round((order.get("plus_delivery_saved") or 0) + (order.get("plus_fee_saved") or 0)),
    }


# ------------------------------------------------------------------- checkout


def checkout(conn: sqlite3.Connection, customer_id: int, products: float) -> Dict[str, Any]:
    """Options the trader sees in the cart for a given products total."""
    from .wholesale_plus import benefits

    settings = get_settings(conn)
    delivery, minimum, zone = delivery_terms(conn, customer_id, settings)
    plus = benefits(conn, customer_id, settings)
    delivery_saved = delivery if plus["free_delivery"] else 0.0
    delivery = 0.0 if plus["free_delivery"] else delivery
    credit = credit_summary(conn, customer_id)
    blocked = credit["overdue"] and settings.get("credit_block_overdue") == "1"
    options = []
    for m in list_methods(conn):
        if not m["is_enabled"]:
            continue
        if m["code"] == "credit" and credit["limit"] <= 0:
            continue
        t = totals(products, delivery, m, plus["fee_share"])
        reason = ""
        if m["code"] == "cash" and m["max_amount"] > 0 and t["grand"] > m["max_amount"]:
            reason = f"الكاش عند الاستلام لحد {m['max_amount']:,.0f} ج بس."
        if m["code"] == "credit" and t["grand"] > credit["available"] + 0.009:
            reason = f"المتاح في حسابك {credit['available']:,.2f} ج بس."
        options.append({
            "code": m["code"],
            "label": m["label"],
            "fee": t["fee"],
            "fee_payer": m["fee_payer"],
            "fee_text": _fee_text(m),
            "fee_saved": t["fee_saved"],
            "grand": t["grand"],
            "available": not reason,
            "reason": reason,
            "account": m["account"] if m["code"] in TRANSFER_METHODS else "",
            "instructions": m["instructions"],
        })
    return {
        "products": _round(products),
        "delivery": delivery,
        "zone": zone,
        "min_order": minimum,
        "below_min": minimum > 0 and products + 0.009 < minimum,
        "blocked": blocked,
        "blocked_reason": "عليك مبلغ آجل متأخر. سدّده الأول علشان تقدر تطلب تاني." if blocked else "",
        "methods": options,
        "credit": {k: credit[k] for k in ("limit", "balance", "available", "next_due", "overdue")},
        "pay_after_review": True,
        "plus": {
            "active": plus["active"],
            "ends_at": plus["ends_at"],
            "delivery_saved": _round(delivery_saved),
            "offer": settings.get("plus_enabled") == "1" and not plus["active"],
            "offer_delivery": _round(delivery) if settings.get("plus_free_delivery", "1") == "1" else 0.0,
        },
    }


def delivery_terms(conn: sqlite3.Connection, customer_id: int, settings: Optional[Dict[str, str]] = None):
    """Delivery fee and minimum order for a trader: their zone first, then the general settings."""
    settings = settings or get_settings(conn)
    fee, minimum, zone = _num(settings, "delivery_fee"), _num(settings, "min_order_total"), ""
    row = conn.execute(
        """
        SELECT z.name, z.fee, z.min_order FROM ws_traders t JOIN ws_delivery_zones z ON z.id = t.zone_id
        WHERE t.customer_id = ? AND z.is_active = 1
        """,
        (customer_id,),
    ).fetchone()
    if row:
        fee, zone = float(row["fee"]), row["name"]
        if row["min_order"]:
            minimum = float(row["min_order"])
    return fee, minimum, zone


def _fee_text(m: Dict[str, Any]) -> str:
    if m["code"] not in TRANSFER_METHODS or not m["fee_value"]:
        return "من غير عمولة"
    value = f"{m['fee_value']:g}٪" if m["fee_type"] == "percent" else f"{m['fee_value']:g} ج"
    return f"عمولة {value}" + ("" if m["fee_payer"] == "customer" else " علينا")


def validate_submit(conn: sqlite3.Connection, customer_id: int, method_code: str, products: float) -> Dict[str, Any]:
    """Raise if the order cannot be sent with this method; return its totals."""
    if method_code not in METHOD_CODES:
        raise WholesaleError("اختار طريقة الدفع.")
    info = checkout(conn, customer_id, products)
    if info["blocked"]:
        raise WholesaleError(info["blocked_reason"], 403)
    if info["below_min"]:
        raise WholesaleError(f"أقل قيمة للطلب {info['min_order']:,.2f} ج.")
    option = next((o for o in info["methods"] if o["code"] == method_code), None)
    if not option:
        raise WholesaleError("طريقة الدفع دي مش متاحة.")
    if not option["available"]:
        raise WholesaleError(option["reason"])
    from .wholesale_plus import benefits

    plus = benefits(conn, customer_id)
    t = totals(products, info["delivery"], method(conn, method_code), plus["fee_share"])
    return {"delivery": info["delivery"], "is_plus": plus["active"], "plus_delivery_saved": info["plus"]["delivery_saved"], **t}


def initial_payment_status(method_code: str) -> str:
    return {"cash": "cod", "credit": "credit"}.get(method_code, "unpaid")


def change_method(conn: sqlite3.Connection, customer_id: int, order: Dict[str, Any], method_code: str) -> None:
    view = payment_view(conn, order)
    if not view["can_change_method"]:
        raise WholesaleError("مينفعش تغيّر طريقة الدفع دلوقتي.")
    if method_code == order["payment_method"]:
        return
    pending = conn.execute("SELECT 1 FROM ws_payments WHERE order_id = ? AND status = 'pending'", (order["id"],)).fetchone()
    if pending:
        raise WholesaleError("فيه تحويل بيتراجع للطلب ده.")
    info = checkout(conn, customer_id, products_total(order))
    reserved_self = order["grand_total"] if order["payment_method"] == "credit" else 0
    option = next((o for o in info["methods"] if o["code"] == method_code), None)
    if not option:
        raise WholesaleError("طريقة الدفع دي مش متاحة.")
    if not option["available"] and not (method_code == "credit" and option["grand"] <= info["credit"]["available"] + reserved_self + 0.009):
        raise WholesaleError(option["reason"])
    conn.execute(
        "UPDATE ws_orders SET payment_method = ?, payment_status = ?, updated_at = ? WHERE id = ?",
        (method_code, initial_payment_status(method_code), utc_now(), order["id"]),
    )
    recalc_order(conn, order["id"])


# ------------------------------------------------------------------ transfers


def submit_transfer(conn: sqlite3.Connection, customer_id: int, purpose: str, method_code: str,
                    amount: Optional[float], proof_path: str, sender: str = "", order_id: Optional[int] = None) -> Dict[str, Any]:
    if method_code not in TRANSFER_METHODS:
        raise WholesaleError("اختار انستاباي أو محفظة.")
    m = method(conn, method_code)
    if not m["is_enabled"]:
        raise WholesaleError("طريقة الدفع دي مش متاحة دلوقتي.")
    now = utc_now()
    if purpose == "order":
        row = conn.execute("SELECT * FROM ws_orders WHERE id = ? AND customer_id = ?", (order_id, customer_id)).fetchone()
        if not row:
            raise WholesaleError("الطلب مش موجود.", 404)
        order = dict(row)
        view = payment_view(conn, order)
        if not view["can_pay"]:
            raise WholesaleError("الطلب ده مش محتاج دفع دلوقتي.")
        if method_code != order["payment_method"]:
            raise WholesaleError("طريقة الدفع مختلفة عن اللي اخترتها للطلب.")
        amount = view["to_pay"]
        payment_id = conn.execute(
            """
            INSERT INTO ws_payments(customer_id, order_id, purpose, method, amount, proof_path, sender, status, created_at)
            VALUES(?, ?, 'order', ?, ?, ?, ?, 'pending', ?)
            """,
            (customer_id, order_id, method_code, amount, proof_path, clean_text(sender, 80), now),
        ).lastrowid
        conn.execute("UPDATE ws_orders SET payment_status = 'pending', updated_at = ? WHERE id = ?", (now, order_id))
        return {"id": payment_id, "amount": amount}
    if purpose != "credit":
        raise WholesaleError("نوع الدفع غلط.")
    credit = credit_summary(conn, customer_id)
    if credit["balance"] <= 0:
        raise WholesaleError("مفيش عليك آجل.")
    try:
        amount = _round(float(amount or 0))
    except (TypeError, ValueError):
        raise WholesaleError("اكتب المبلغ.") from None
    if amount <= 0 or amount > credit["balance"] + 0.009:
        raise WholesaleError(f"المبلغ لازم يكون من 1 لحد {credit['balance']:,.2f} ج.")
    payment_id = conn.execute(
        """
        INSERT INTO ws_payments(customer_id, purpose, method, amount, proof_path, sender, status, created_at)
        VALUES(?, 'credit', ?, ?, ?, ?, 'pending', ?)
        """,
        (customer_id, method_code, amount, proof_path, clean_text(sender, 80), now),
    ).lastrowid
    return {"id": payment_id, "amount": amount}


def list_payments(conn: sqlite3.Connection, status: str = "pending") -> List[Dict[str, Any]]:
    from .wholesale_orders import order_number

    sql = """
        SELECT p.*, c.name AS customer_name, c.phone, t.shop_name, m.label AS method_label
        FROM ws_payments p JOIN customers c ON c.id = p.customer_id
        LEFT JOIN ws_traders t ON t.customer_id = p.customer_id
        LEFT JOIN ws_payment_methods m ON m.code = p.method
    """
    params: List[Any] = []
    if status in ("pending", "confirmed", "rejected"):
        sql += " WHERE p.status = ?"
        params.append(status)
    sql += " ORDER BY p.id DESC LIMIT 200"
    rows = []
    for r in conn.execute(sql, params):
        item = dict(r)
        item["order_number"] = order_number(item["order_id"]) if item["order_id"] else None
        item["has_proof"] = bool(item.pop("proof_path"))
        rows.append(item)
    return rows


def payment_proof_path(conn: sqlite3.Connection, payment_id: int) -> str:
    row = conn.execute("SELECT proof_path FROM ws_payments WHERE id = ?", (payment_id,)).fetchone()
    if not row or not row["proof_path"]:
        raise WholesaleError("مفيش صورة.", 404)
    return row["proof_path"]


def decide_payment(conn: sqlite3.Connection, payment_id: int, approve: bool, reason: str, user_id: Optional[int]) -> Dict[str, Any]:
    from .wholesale_orders import notify, order_number

    row = conn.execute("SELECT * FROM ws_payments WHERE id = ?", (payment_id,)).fetchone()
    if not row:
        raise WholesaleError("الدفعة مش موجودة.", 404)
    pay = dict(row)
    if pay["status"] != "pending":
        raise WholesaleError("الدفعة دي اتراجعت قبل كده.")
    now = utc_now()
    reason = clean_text(reason, 300)
    if not approve and not reason:
        raise WholesaleError("اكتب سبب الرفض علشان يوصل للتاجر.")
    conn.execute(
        "UPDATE ws_payments SET status = ?, reject_reason = ?, decided_at = ?, decided_by = ? WHERE id = ?",
        ("confirmed" if approve else "rejected", "" if approve else reason, now, user_id, payment_id),
    )
    if pay["purpose"] == "order":
        order = dict(conn.execute("SELECT * FROM ws_orders WHERE id = ?", (pay["order_id"],)).fetchone())
        number = order_number(order["id"])
        if approve:
            paid = _round(order["paid_amount"] + pay["amount"])
            status = "paid" if paid + 0.009 >= order["grand_total"] else "unpaid"
            conn.execute("UPDATE ws_orders SET paid_amount = ?, payment_status = ?, updated_at = ? WHERE id = ?", (paid, status, now, order["id"]))
            notify(conn, pay["customer_id"], "payment", f"استلمنا تحويلك لطلب {number}", f"المبلغ {pay['amount']:,.2f} ج.", order_id=order["id"])
        else:
            conn.execute("UPDATE ws_orders SET payment_status = 'unpaid', updated_at = ? WHERE id = ?", (now, order["id"]))
            notify(conn, pay["customer_id"], "payment", f"التحويل لطلب {number} ماتقبلش", reason + " ابعت صورة التحويل تاني.", order_id=order["id"])
    else:
        if approve:
            conn.execute(
                "INSERT INTO ws_credit_entries(customer_id, kind, amount, payment_id, method, note, created_by, created_at) VALUES(?, 'payment', ?, ?, ?, 'تحويل من التاجر', ?, ?)",
                (pay["customer_id"], pay["amount"], payment_id, pay["method"], user_id, now),
            )
            notify(conn, pay["customer_id"], "credit", "استلمنا سدادك", f"اتخصم {pay['amount']:,.2f} ج من الآجل.")
        else:
            notify(conn, pay["customer_id"], "credit", "تحويل سداد الآجل ماتقبلش", reason)
    return {"id": payment_id, "status": "confirmed" if approve else "rejected"}


def mark_refunded(conn: sqlite3.Connection, order_id: int, amount: float) -> None:
    order = dict(conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order_id,)).fetchone())
    due = payment_view(conn, order)["refund_due"]
    amount = _round(amount)
    if amount <= 0 or amount > due + 0.009:
        raise WholesaleError(f"المبلغ لازم يكون من 1 لحد {due:,.2f} ج.")
    conn.execute("UPDATE ws_orders SET refunded_amount = refunded_amount + ?, updated_at = ? WHERE id = ?", (amount, utc_now(), order_id))


# --------------------------------------------------------------------- credit


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        value = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def credit_summary(conn: sqlite3.Connection, customer_id: int, with_entries: bool = False) -> Dict[str, Any]:
    settings = get_settings(conn)
    account = conn.execute("SELECT * FROM ws_credit_accounts WHERE customer_id = ?", (customer_id,)).fetchone()
    limit = float(account["credit_limit"]) if account else 0.0
    period = int(account["period_days"]) if account and account["period_days"] else int(_num(settings, "credit_period_days", 30) or 30)
    entries = [dict(r) for r in conn.execute("SELECT * FROM ws_credit_entries WHERE customer_id = ? ORDER BY id", (customer_id,))]
    charges = []
    paid_pool = 0.0
    for e in entries:
        if e["kind"] == "charge" or (e["kind"] == "adjustment" and e["amount"] > 0):
            charges.append([float(e["amount"]), _parse(e["due_date"]) or _parse(e["created_at"])])
        elif e["kind"] == "payment":
            paid_pool += float(e["amount"])
        else:
            paid_pool += -float(e["amount"])
    balance = _round(sum(c[0] for c in charges) - paid_pool)
    next_due = None
    for amount, due in charges:
        if paid_pool + 0.009 >= amount:
            paid_pool -= amount
            continue
        next_due = due
        break
    overdue = bool(next_due and balance > 0.009 and next_due < _now_dt())
    reserved = conn.execute(
        "SELECT COALESCE(SUM(grand_total), 0) FROM ws_orders WHERE customer_id = ? AND payment_method = 'credit' AND status NOT IN ('delivered', 'cancelled')",
        (customer_id,),
    ).fetchone()[0]
    available = _round(max(0.0, limit - max(balance, 0) - reserved))
    if overdue:
        status, label = "overdue", "متأخر"
    elif limit > 0 and balance + reserved >= limit * 0.8:
        status, label = "near_limit", "قرب الحد"
    else:
        status, label = "ok", "منتظم"
    result = {
        "customer_id": customer_id,
        "limit": _round(limit),
        "period_days": period,
        "balance": balance,
        "reserved": _round(reserved),
        "available": available,
        "next_due": next_due.isoformat(timespec="seconds") if next_due and balance > 0.009 else None,
        "overdue": overdue,
        "status": status,
        "status_label": label,
    }
    if with_entries:
        from .wholesale_orders import order_number

        labels = {"charge": "طلب على الحساب", "payment": "سداد", "adjustment": "تسوية"}
        result["entries"] = [
            {**e, "label": labels[e["kind"]], "order_number": order_number(e["order_id"]) if e["order_id"] else None}
            for e in reversed(entries[-200:])
        ]
    return result


def set_credit_account(conn: sqlite3.Connection, customer_id: int, limit: float, period_days: Optional[int]) -> Dict[str, Any]:
    if not conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
        raise WholesaleError("العميل مش موجود.", 404)
    limit = float(limit or 0)
    if limit < 0 or limit > 100_000_000:
        raise WholesaleError("حد الآجل مش صحيح.")
    if period_days is not None and (int(period_days) < 1 or int(period_days) > 365):
        raise WholesaleError("فترة السداد لازم تكون من يوم لحد 365 يوم.")
    conn.execute(
        """
        INSERT INTO ws_credit_accounts(customer_id, credit_limit, period_days, updated_at) VALUES(?, ?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET credit_limit = excluded.credit_limit, period_days = excluded.period_days,
            updated_at = excluded.updated_at
        """,
        (customer_id, _round(limit), int(period_days) if period_days else None, utc_now()),
    )
    return credit_summary(conn, customer_id)


def record_credit_entry(conn: sqlite3.Connection, customer_id: int, kind: str, amount: float, note: str, user_id: Optional[int], method_code: str = "cash") -> Dict[str, Any]:
    from .wholesale_orders import notify

    if kind not in ("payment", "adjustment"):
        raise WholesaleError("نوع الحركة غلط.")
    amount = _round(float(amount or 0))
    if amount == 0 or (kind == "payment" and amount < 0):
        raise WholesaleError("اكتب المبلغ.")
    if kind == "payment":
        balance = credit_summary(conn, customer_id)["balance"]
        if amount > balance + 0.009:
            raise WholesaleError(f"المستحق {balance:,.2f} ج بس.")
    if kind == "adjustment" and not clean_text(note, 200):
        raise WholesaleError("اكتب سبب التسوية.")
    conn.execute(
        "INSERT INTO ws_credit_entries(customer_id, kind, amount, method, note, created_by, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (customer_id, kind, amount, method_code if kind == "payment" else "", clean_text(note, 200), user_id, utc_now()),
    )
    if kind == "payment":
        notify(conn, customer_id, "credit", "اتسجّل سدادك", f"اتخصم {amount:,.2f} ج من الآجل.")
    return credit_summary(conn, customer_id, with_entries=True)


def credit_accounts(conn: sqlite3.Connection, status: str = "") -> List[Dict[str, Any]]:
    ids = [r[0] for r in conn.execute(
        """
        SELECT customer_id FROM ws_credit_accounts
        UNION SELECT DISTINCT customer_id FROM ws_credit_entries
        UNION SELECT DISTINCT customer_id FROM ws_orders WHERE payment_method = 'credit' AND status NOT IN ('delivered', 'cancelled')
        """
    )]
    rows = []
    for cid in ids:
        info = credit_summary(conn, cid)
        if status and info["status"] != status:
            continue
        who = conn.execute(
            "SELECT c.name, c.phone, t.shop_name FROM customers c LEFT JOIN ws_traders t ON t.customer_id = c.id WHERE c.id = ?", (cid,)
        ).fetchone()
        rows.append({**info, "name": who["name"], "phone": who["phone"], "shop_name": who["shop_name"] or ""})
    rows.sort(key=lambda r: ({"overdue": 0, "near_limit": 1, "ok": 2}[r["status"]], -r["balance"]))
    return rows


# --------------------------------------------------------------- delivery


def award_points(conn: sqlite3.Connection, order: Dict[str, Any]) -> int:
    """Wholesale points for a delivered order (once), into the wholesale points balance."""
    from .wholesale_orders import order_number
    from .wholesale_points import earn_for_order

    rate = _num(get_settings(conn), "points_egp_per_point")
    if rate <= 0:
        return 0
    points = int(math.floor(float(order["final_total"]) / rate + 1e-9))
    if not earn_for_order(conn, order["customer_id"], order["id"], points, f"نقاط طلب رقم {order_number(order['id'])}"):
        return 0
    conn.execute("UPDATE ws_orders SET points_awarded = ? WHERE id = ?", (points, order["id"]))
    return points


def mark_delivered(conn: sqlite3.Connection, order_id: int, user_id: Optional[int]) -> Dict[str, Any]:
    from .wholesale_orders import notify, order_number

    row = conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise WholesaleError("الطلب مش موجود.", 404)
    order = dict(row)
    if order["status"] not in DELIVERABLE:
        raise WholesaleError("الطلب لازم البضاعة توصل كلها الأول.")
    if order["payment_method"] in TRANSFER_METHODS and order["payment_status"] != "paid":
        raise WholesaleError("التاجر لسه مادفعش التحويل، أو التحويل لسه مستني تأكيدك.")
    now = utc_now()
    settings = get_settings(conn)
    if order["payment_method"] == "cash":
        conn.execute("UPDATE ws_orders SET payment_status = 'paid', paid_amount = grand_total WHERE id = ?", (order_id,))
    elif order["payment_method"] == "credit":
        account = conn.execute("SELECT period_days FROM ws_credit_accounts WHERE customer_id = ?", (order["customer_id"],)).fetchone()
        period = int(account["period_days"]) if account and account["period_days"] else int(_num(settings, "credit_period_days", 30) or 30)
        due = (_now_dt() + timedelta(days=period)).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO ws_credit_entries(customer_id, kind, amount, order_id, note, due_date, created_by, created_at) VALUES(?, 'charge', ?, ?, ?, ?, ?, ?)",
            (order["customer_id"], order["grand_total"], order_id, f"طلب {order_number(order_id)}", due, user_id, now),
        )
    conn.execute("UPDATE ws_orders SET status = 'delivered', delivered_at = ?, updated_at = ? WHERE id = ?", (now, now, order_id))
    order = dict(conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order_id,)).fetchone())
    points = award_points(conn, order)
    body = f"الإجمالي {order['grand_total']:,.2f} ج."
    if order["payment_method"] == "credit":
        body += " اتسجّل على حسابك."
    if points:
        body += f" كسبت {points} نقطة في رصيد روز."
    notify(conn, order["customer_id"], "order", f"طلبك رقم {order_number(order_id)} اتسلّم", body, order_id=order_id)
    return {"points": points}
