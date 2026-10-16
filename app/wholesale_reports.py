"""Wholesale phase 8: store reports and supplier reports.

Periods are calendar days, months or years in Cairo time. A sale counts on
the day its order was delivered, at its final value after review (what the
trader actually received). Returns count on the day the money went back.

Every number has the same number for the period before it, for comparison.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .wholesale_orders import UNIT_LABELS, order_number
from .wholesale_service import WholesaleError

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

FINAL_QTY = "COALESCE(i.picked_qty, i.received_qty, i.confirmed_qty, i.qty)"
MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
METHOD_LABEL = {"cash": "كاش", "instapay": "انستاباي", "wallet": "محفظة", "credit": "آجل"}


def local_tz():
    name = os.getenv("WHOLESALE_TIMEZONE", "Africa/Cairo")
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass
    return timezone(timedelta(hours=float(os.getenv("WHOLESALE_UTC_OFFSET_HOURS", "3"))))


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _local(y: int, m: int = 1, d: int = 1, h: int = 0) -> datetime:
    return datetime(y, m, d, h, tzinfo=local_tz())


def _add_month(y: int, m: int, n: int = 1) -> Tuple[int, int]:
    total = y * 12 + (m - 1) + n
    return total // 12, total % 12 + 1


def period_range(period: str, ref: str = "") -> Dict[str, Any]:
    """Start/end (UTC strings) of the period, the one before it, and the chart buckets."""
    today = datetime.now(local_tz()).date()
    try:
        if period == "day":
            d = date.fromisoformat(ref) if ref else today
            start, end = _local(d.year, d.month, d.day), _local(d.year, d.month, d.day) + timedelta(days=1)
            p = d - timedelta(days=1)
            prev = (_local(p.year, p.month, p.day), start)
            buckets = [(f"{h}", start + timedelta(hours=h), start + timedelta(hours=h + 1)) for h in range(24)]
            label, key = d.isoformat(), d.isoformat()
            prev_key, next_key = p.isoformat(), (d + timedelta(days=1)).isoformat()
            title = d.strftime("%d/%m/%Y")
        elif period == "month":
            y, m = (int(ref[:4]), int(ref[5:7])) if ref else (today.year, today.month)
            ny, nm = _add_month(y, m, 1)
            py, pm = _add_month(y, m, -1)
            start, end = _local(y, m), _local(ny, nm)
            prev = (_local(py, pm), start)
            days = (date(ny, nm, 1) - date(y, m, 1)).days
            buckets = [(f"{i + 1}", _local(y, m, i + 1), _local(y, m, i + 1) + timedelta(days=1)) for i in range(days)]
            key, prev_key, next_key = f"{y:04d}-{m:02d}", f"{py:04d}-{pm:02d}", f"{ny:04d}-{nm:02d}"
            title = f"{MONTHS[m - 1]} {y}"
        elif period == "year":
            y = int(ref[:4]) if ref else today.year
            start, end = _local(y), _local(y + 1)
            prev = (_local(y - 1), start)
            buckets = [(MONTHS[i], _local(y, i + 1), _local(*_add_month(y, i + 1, 1))) for i in range(12)]
            key, prev_key, next_key = str(y), str(y - 1), str(y + 1)
            title = str(y)
        else:
            raise WholesaleError("اختار يومي أو شهري أو سنوي.")
    except (ValueError, IndexError):
        raise WholesaleError("التاريخ مش مظبوط.") from None
    now_key = {"day": today.isoformat(), "month": f"{today.year:04d}-{today.month:02d}", "year": str(today.year)}[period]
    return {
        "period": period,
        "key": key,
        "title": title,
        "prev_key": prev_key,
        "next_key": next_key if next_key <= now_key else None,
        "start": _utc(start),
        "end": _utc(end),
        "prev_start": _utc(prev[0]),
        "prev_end": _utc(prev[1]),
        "buckets": [(lbl, _utc(a), _utc(b)) for lbl, a, b in buckets],
    }


def _one(conn: sqlite3.Connection, sql: str, params: tuple) -> float:
    row = conn.execute(sql, params).fetchone()
    return float(row[0] or 0) if row else 0.0


def _metrics(conn: sqlite3.Connection, start: str, end: str) -> Dict[str, float]:
    span = (start, end)
    sales = conn.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(final_total), 0), COALESCE(SUM(delivery_fee), 0),
               COALESCE(SUM(CASE WHEN fee_payer = 'customer' THEN payment_fee ELSE 0 END), 0),
               COALESCE(SUM(points_awarded), 0), COALESCE(SUM(is_plus), 0)
        FROM ws_orders WHERE status = 'delivered' AND delivered_at >= ? AND delivered_at < ?
        """,
        span,
    ).fetchone()
    profit = _one(conn, f"""
        SELECT SUM({FINAL_QTY} * (i.unit_price - i.unit_cost)) FROM ws_order_items i JOIN ws_orders o ON o.id = i.order_id
        WHERE o.status = 'delivered' AND o.delivered_at >= ? AND o.delivered_at < ?
    """, span)
    returns = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(refund_amount), 0) FROM ws_returns WHERE status = 'refunded' AND refunded_at >= ? AND refunded_at < ?",
        span,
    ).fetchone()
    orders, value = int(sales[0]), round(float(sales[1]), 2)
    return {
        "sales": value,
        "orders": orders,
        "average": round(value / orders, 2) if orders else 0.0,
        "profit": round(profit, 2),
        "returns_count": int(returns[0]),
        "returns": round(float(returns[1]), 2),
        "net": round(value - float(returns[1]), 2),
        "delivery_fees": round(float(sales[2]), 2),
        "payment_fees": round(float(sales[3]), 2),
        "points": int(sales[4]),
        "plus_orders": int(sales[5]),
        "new_orders": int(_one(conn, "SELECT COUNT(*) FROM ws_orders WHERE created_at >= ? AND created_at < ?", span)),
        "cancelled": int(_one(conn, "SELECT COUNT(*) FROM ws_orders WHERE status = 'cancelled' AND cancelled_at >= ? AND cancelled_at < ?", span)),
        "shortages": int(_one(conn, "SELECT COUNT(*) FROM ws_shortages WHERE source != 'request' AND created_at >= ? AND created_at < ?", span)),
        "requests": int(_one(conn, "SELECT COUNT(*) FROM ws_shortages WHERE source = 'request' AND created_at >= ? AND created_at < ?", span)),
        "plus_revenue": round(_one(conn, "SELECT SUM(price) FROM ws_plus_subscriptions WHERE status IN ('active', 'cancelled') AND decided_at >= ? AND decided_at < ?", span), 2),
        "new_traders": int(_one(conn, "SELECT COUNT(*) FROM ws_traders WHERE status = 'approved' AND COALESCE(decided_at, created_at) >= ? AND COALESCE(decided_at, created_at) < ?", span)),
    }


def _change(now: float, before: float) -> Optional[float]:
    if not before:
        return None
    return round((now - before) / before * 100, 1)


def store_report(conn: sqlite3.Connection, period: str, ref: str = "") -> Dict[str, Any]:
    r = period_range(period, ref)
    span = (r["start"], r["end"])
    cur = _metrics(conn, *span)
    prev = _metrics(conn, r["prev_start"], r["prev_end"])
    chart = []
    for label, a, b in r["buckets"]:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(final_total), 0) FROM ws_orders WHERE status = 'delivered' AND delivered_at >= ? AND delivered_at < ?",
            (a, b),
        ).fetchone()
        chart.append({"label": label, "orders": int(row[0]), "sales": round(float(row[1]), 2)})
    delivered = "o.status = 'delivered' AND o.delivered_at >= ? AND o.delivered_at < ?"
    top_products = [dict(x) for x in conn.execute(f"""
        SELECT i.name, i.unit, SUM({FINAL_QTY}) AS qty, ROUND(SUM({FINAL_QTY} * i.unit_price), 2) AS value,
               ROUND(SUM({FINAL_QTY} * (i.unit_price - i.unit_cost)), 2) AS profit, COUNT(DISTINCT o.id) AS orders
        FROM ws_order_items i JOIN ws_orders o ON o.id = i.order_id
        WHERE {delivered} AND {FINAL_QTY} > 0
        GROUP BY COALESCE(i.product_id, i.name), i.unit ORDER BY value DESC LIMIT 15
    """, span)]
    for p in top_products:
        p["unit_label"] = UNIT_LABELS.get(p["unit"], p["unit"])
    top_traders = [dict(x) for x in conn.execute(f"""
        SELECT o.customer_id, c.name, c.phone, t.shop_name, COUNT(*) AS orders, ROUND(SUM(o.final_total), 2) AS value
        FROM ws_orders o JOIN customers c ON c.id = o.customer_id LEFT JOIN ws_traders t ON t.customer_id = o.customer_id
        WHERE {delivered} GROUP BY o.customer_id ORDER BY value DESC LIMIT 15
    """, span)]
    units = {row["unit"]: dict(row) for row in conn.execute(f"""
        SELECT i.unit, SUM({FINAL_QTY}) AS qty, ROUND(SUM({FINAL_QTY} * i.unit_price), 2) AS value
        FROM ws_order_items i JOIN ws_orders o ON o.id = i.order_id WHERE {delivered} GROUP BY i.unit
    """, span)}
    total_units = sum(u["value"] or 0 for u in units.values()) or 0
    by_unit = [
        {"unit": u, "label": UNIT_LABELS[u], "qty": int((units.get(u) or {}).get("qty") or 0),
         "value": float((units.get(u) or {}).get("value") or 0),
         "share": round(float((units.get(u) or {}).get("value") or 0) / total_units * 100, 1) if total_units else 0.0}
        for u in ("piece", "dozen", "carton")
    ]
    by_method = [
        {"method": row["payment_method"], "label": METHOD_LABEL.get(row["payment_method"], row["payment_method"]),
         "orders": row["n"], "value": round(row["v"] or 0, 2)}
        for row in conn.execute(f"""
            SELECT o.payment_method, COUNT(*) AS n, SUM(o.final_total) AS v FROM ws_orders o
            WHERE {delivered} GROUP BY o.payment_method ORDER BY v DESC
        """, span)
    ]
    top_shortages = [dict(x) for x in conn.execute("""
        SELECT name, COUNT(*) AS times, COUNT(DISTINCT customer_id) AS traders, MAX(source = 'request') AS is_request
        FROM ws_shortages WHERE created_at >= ? AND created_at < ?
        GROUP BY COALESCE(product_id, name) ORDER BY times DESC LIMIT 10
    """, span)]
    compare = {k: {"now": cur[k], "before": prev[k], "change": _change(cur[k], prev[k])}
               for k in ("sales", "orders", "average", "profit", "returns", "net")}
    return {
        "range": {k: r[k] for k in ("period", "key", "title", "prev_key", "next_key")},
        "metrics": cur,
        "previous": prev,
        "compare": compare,
        "chart": chart,
        "top_products": top_products,
        "top_traders": top_traders,
        "by_unit": by_unit,
        "by_method": by_method,
        "top_shortages": top_shortages,
    }


def supplier_report(conn: sqlite3.Connection, period: str, ref: str = "", supplier_id: Optional[int] = None) -> Dict[str, Any]:
    """Per supplier: what we sold of theirs, what it cost, what they could not supply, and damaged returns."""
    r = period_range(period, ref)
    span = (r["start"], r["end"])
    suppliers = []
    for s in conn.execute("SELECT id, alias, phone, is_active FROM ws_suppliers ORDER BY alias"):
        sid = s["id"]
        sold = conn.execute(f"""
            SELECT COUNT(DISTINCT o.id), COALESCE(SUM({FINAL_QTY}), 0),
                   COALESCE(SUM({FINAL_QTY} * i.unit_cost), 0), COALESCE(SUM({FINAL_QTY} * i.unit_price), 0)
            FROM ws_order_items i JOIN ws_orders o ON o.id = i.order_id
            WHERE i.supplier_id = ? AND o.status = 'delivered' AND o.delivered_at >= ? AND o.delivered_at < ?
        """, (sid,) + span).fetchone()
        asked = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(i.review_status = 'unavailable'), 0), COALESCE(SUM(i.review_status = 'partial'), 0),
                   COALESCE(SUM(i.received_qty IS NOT NULL AND i.received_qty < COALESCE(i.confirmed_qty, i.qty)), 0)
            FROM ws_order_items i JOIN ws_orders o ON o.id = i.order_id
            WHERE i.supplier_id = ? AND o.created_at >= ? AND o.created_at < ? AND i.review_status != 'pending'
        """, (sid,) + span).fetchone()
        damaged = conn.execute("""
            SELECT COALESCE(SUM(ri.damaged_qty), 0), COALESCE(SUM(ri.damaged_qty * oi.unit_cost), 0),
                   COALESCE(SUM(CASE WHEN ri.reason = 'near_expiry' THEN ri.qty ELSE 0 END), 0)
            FROM ws_return_items ri JOIN ws_returns rt ON rt.id = ri.return_id JOIN ws_order_items oi ON oi.id = ri.order_item_id
            WHERE oi.supplier_id = ? AND rt.inspected_at >= ? AND rt.inspected_at < ?
        """, (sid,) + span).fetchone()
        lines = int(asked[0])
        cost, value = round(float(sold[2]), 2), round(float(sold[3]), 2)
        suppliers.append({
            "id": sid,
            "alias": s["alias"],
            "phone": s["phone"],
            "is_active": bool(s["is_active"]),
            "orders": int(sold[0]),
            "qty": int(sold[1]),
            "cost": cost,
            "sales": value,
            "profit": round(value - cost, 2),
            "margin": round((value - cost) / value * 100, 1) if value else 0.0,
            "lines_reviewed": lines,
            "unavailable": int(asked[1]),
            "partial": int(asked[2]),
            "short_on_receipt": int(asked[3]),
            "fill_rate": round((lines - int(asked[1])) / lines * 100, 1) if lines else None,
            "damaged_qty": int(damaged[0]),
            "damaged_cost": round(float(damaged[1]), 2),
            "near_expiry_qty": int(damaged[2]),
        })
    detail = None
    if supplier_id:
        sup = next((s for s in suppliers if s["id"] == supplier_id), None)
        if not sup:
            raise WholesaleError("المورد مش موجود.", 404)
        products = [dict(x) for x in conn.execute(f"""
            SELECT i.name, i.supplier_item_name, i.supplier_code, i.unit, SUM({FINAL_QTY}) AS qty,
                   ROUND(SUM({FINAL_QTY} * i.unit_cost), 2) AS cost, ROUND(SUM({FINAL_QTY} * i.unit_price), 2) AS value
            FROM ws_order_items i JOIN ws_orders o ON o.id = i.order_id
            WHERE i.supplier_id = ? AND o.status = 'delivered' AND o.delivered_at >= ? AND o.delivered_at < ? AND {FINAL_QTY} > 0
            GROUP BY COALESCE(i.product_id, i.name), i.unit ORDER BY value DESC LIMIT 50
        """, (supplier_id,) + span)]
        for p in products:
            p["unit_label"] = UNIT_LABELS.get(p["unit"], p["unit"])
        missing = [dict(x) for x in conn.execute("""
            SELECT i.name, i.unit, COUNT(*) AS times, SUM(i.qty - COALESCE(i.confirmed_qty, 0)) AS qty
            FROM ws_order_items i JOIN ws_orders o ON o.id = i.order_id
            WHERE i.supplier_id = ? AND o.created_at >= ? AND o.created_at < ? AND i.review_status IN ('unavailable', 'partial')
            GROUP BY COALESCE(i.product_id, i.name), i.unit ORDER BY times DESC LIMIT 30
        """, (supplier_id,) + span)]
        for p in missing:
            p["unit_label"] = UNIT_LABELS.get(p["unit"], p["unit"])
        damaged_rows = []
        for x in conn.execute("""
            SELECT rt.order_id, oi.name, oi.unit, ri.damaged_qty, oi.unit_cost, rt.inspected_at
            FROM ws_return_items ri JOIN ws_returns rt ON rt.id = ri.return_id JOIN ws_order_items oi ON oi.id = ri.order_item_id
            WHERE oi.supplier_id = ? AND rt.inspected_at >= ? AND rt.inspected_at < ? AND ri.damaged_qty > 0
            ORDER BY rt.inspected_at DESC
        """, (supplier_id,) + span):
            damaged_rows.append({"order_number": order_number(x["order_id"]), "name": x["name"], "unit_label": UNIT_LABELS.get(x["unit"], x["unit"]),
                                 "qty": x["damaged_qty"], "cost": round(x["damaged_qty"] * x["unit_cost"], 2), "at": x["inspected_at"]})
        detail = {"supplier": sup, "products": products, "missing": missing, "damaged": damaged_rows}
    totals = {k: round(sum(s[k] for s in suppliers), 2) for k in ("cost", "sales", "profit", "damaged_cost")}
    totals.update({k: sum(s[k] for s in suppliers) for k in ("unavailable", "damaged_qty")})
    return {"range": {k: r[k] for k in ("period", "key", "title", "prev_key", "next_key")},
            "suppliers": suppliers, "totals": totals, "detail": detail}


# ------------------------------------------------------------------- Excel


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def store_report_rows(rep: Dict[str, Any]) -> List[Tuple[str, List[List[Any]], List[int]]]:
    m, c = rep["metrics"], rep["compare"]
    summary: List[List[Any]] = [
        [f"تقرير الجملة: {rep['range']['title']}"],
        [],
        ["البند", "الفترة دي", "الفترة اللي قبلها", "التغيير"],
    ]
    for key, label in (("sales", "المبيعات"), ("orders", "الطلبات المتسلّمة"), ("average", "متوسط الطلب"),
                       ("profit", "الربح (بيع - شراء)"), ("returns", "المرتجعات"), ("net", "الصافي")):
        summary.append([label, c[key]["now"], c[key]["before"], _pct(c[key]["change"])])
    summary += [[], ["تفاصيل", "القيمة"],
                ["طلبات جديدة اتبعتت", m["new_orders"]], ["طلبات اتلغت", m["cancelled"]],
                ["عدد المرتجعات", m["returns_count"]], ["رسوم توصيل", m["delivery_fees"]],
                ["عمولات دفع على التجار", m["payment_fees"]], ["اشتراكات روز بلس", m["plus_revenue"]],
                ["طلبات روز بلس", m["plus_orders"]], ["نقاط اتصرفت", m["points"]],
                ["نواقص اتسجّلت", m["shortages"]], ["طلبات توفير", m["requests"]], ["تجار جداد", m["new_traders"]]]
    chart = [["الوقت", "الطلبات", "المبيعات"]] + [[x["label"], x["orders"], x["sales"]] for x in rep["chart"]]
    products = [["الصنف", "الوحدة", "الكمية", "المبيعات", "الربح", "عدد الطلبات"]] + [
        [p["name"], p["unit_label"], p["qty"], p["value"], p["profit"], p["orders"]] for p in rep["top_products"]]
    traders = [["التاجر", "المحل", "الموبايل", "الطلبات", "المبيعات"]] + [
        [t["name"], t["shop_name"] or "", t["phone"], t["orders"], t["value"]] for t in rep["top_traders"]]
    units = [["الوحدة", "الكمية", "المبيعات", "النسبة %"]] + [[u["label"], u["qty"], u["value"], u["share"]] for u in rep["by_unit"]]
    units += [[], ["طريقة الدفع", "الطلبات", "المبيعات"]] + [[x["label"], x["orders"], x["value"]] for x in rep["by_method"]]
    return [("الملخص", summary, [0, 2, 9]), ("حسب الوقت", chart, [0]), ("أكتر الأصناف", products, [0]),
            ("أكتر التجار", traders, [0]), ("الوحدات والدفع", units, [0, len(rep["by_unit"]) + 2])]


def supplier_report_rows(rep: Dict[str, Any]) -> List[Tuple[str, List[List[Any]], List[int]]]:
    head = ["المورد", "طلبات", "كمية", "تكلفة الشراء", "المبيعات", "الربح", "الهامش %", "سطور اتراجعت",
            "مش متوفر", "متوفر جزئي", "ناقص في الاستلام", "نسبة التوفير %", "تالف رجع", "قيمة التالف بسعر الشراء", "قرب ينتهي"]
    rows: List[List[Any]] = [[f"تقرير الموردين: {rep['range']['title']}"], [], head]
    for s in rep["suppliers"]:
        rows.append([s["alias"], s["orders"], s["qty"], s["cost"], s["sales"], s["profit"], s["margin"], s["lines_reviewed"],
                     s["unavailable"], s["partial"], s["short_on_receipt"], "—" if s["fill_rate"] is None else s["fill_rate"],
                     s["damaged_qty"], s["damaged_cost"], s["near_expiry_qty"]])
    sheets = [("الموردين", rows, [0, 2])]
    d = rep.get("detail")
    if d:
        prod = [["الصنف", "اسمه عند المورد", "كود المورد", "الوحدة", "الكمية", "تكلفة", "مبيعات"]] + [
            [p["name"], p["supplier_item_name"], p["supplier_code"], p["unit_label"], p["qty"], p["cost"], p["value"]] for p in d["products"]]
        miss = [["الصنف", "الوحدة", "مرات", "الكمية الناقصة"]] + [[p["name"], p["unit_label"], p["times"], p["qty"]] for p in d["missing"]]
        dmg = [["طلب", "الصنف", "الوحدة", "الكمية", "التكلفة", "اتفحص"]] + [
            [x["order_number"], x["name"], x["unit_label"], x["qty"], x["cost"], x["at"][:10]] for x in d["damaged"]]
        sheets += [(f"أصناف {d['supplier']['alias']}", prod, [0]), ("مش متوفر", miss, [0]), ("تالف للمورد", dmg, [0])]
    return sheets
