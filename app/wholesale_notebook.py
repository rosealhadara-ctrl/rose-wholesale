"""Wholesale phase 9: the trader's shortages notebook (دفتر النواقص).

The notebook lives on the phone first: the trader adds, edits and removes
lines with or without internet, and the page sends the changes here when the
connection is back. Every change carries a ``client_id`` made on the phone, so
sending the same change twice does nothing new.

A line is linked to a product when the trader picked it from the catalog, or
when its name matches a product the trader can order. A line that matches
nothing is also recorded as a provision request (طلب توفير), so the owner sees
what the market is asking for.

Items that were not available in an order are still recorded as shortages
(``ws_shortages``) and shown on the same page.

Reminder: once a week on the day and hour the trader picked, the first time
they open the app after that moment, they get a notification. The page also
offers a calendar file (.ics) so the phone's own calendar reminds them.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db import utc_now
from .wholesale_orders import MAX_QTY, catalog, customer_shortages, notify, require_trader
from .wholesale_pricing import UNITS
from .wholesale_service import WholesaleError, clean_text, normalize_name

MAX_OPEN = 300
MAX_OPS = 500
DAYS = ["الأحد", "الاتنين", "التلات", "الأربع", "الخميس", "الجمعة", "السبت"]  # JavaScript getDay() order
ICS_DAYS = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def _local_now() -> datetime:
    from .wholesale_reports import local_tz

    return datetime.now(local_tz())


# ------------------------------------------------------------------- view


def _orderable(conn: sqlite3.Connection, customer_id: int, product_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    ids = sorted({i for i in product_ids if i})
    found: Dict[int, Dict[str, Any]] = {}
    for start in range(0, len(ids), 100):
        chunk = ids[start:start + 100]
        for item in catalog(conn, customer_id, ids=chunk, per_page=100)["items"]:
            found[item["id"]] = item
    return found


def _line(row: Dict[str, Any], products: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    product = products.get(row["product_id"]) if row["product_id"] else None
    unit_info = product["units"].get(row["unit"]) if product else None
    return {
        "id": row["id"],
        "client_id": row["client_id"],
        "product_id": row["product_id"],
        "name": row["name"],
        "unit": row["unit"],
        "qty": row["qty"],
        "note": row["note"],
        "has_voice": bool(row["voice_path"]),
        "is_request": bool(row["request_id"]),
        "orderable": bool(unit_info),
        "price": unit_info["price"] if unit_info else None,
        "units": {u: (v["price"] if v else None) for u, v in product["units"].items()} if product else None,
        "product": product,
        "created_at": row["created_at"],
    }


def frequent(conn: sqlite3.Connection, customer_id: int, limit: int = 8) -> List[Dict[str, Any]]:
    """What this trader runs out of most: their notebook history, then what they reorder most."""
    counts: Dict[Any, Dict[str, Any]] = {}
    for r in conn.execute(
        """
        SELECT product_id, name, unit, COUNT(*) AS n, MAX(created_at) AS last FROM ws_notebook_items
        WHERE customer_id = ? GROUP BY COALESCE(product_id, name) ORDER BY n DESC, last DESC LIMIT 30
        """,
        (customer_id,),
    ):
        key = r["product_id"] or r["name"]
        counts[key] = {"product_id": r["product_id"], "name": r["name"], "unit": r["unit"], "score": r["n"] * 2}
    for r in conn.execute(
        """
        SELECT i.product_id, i.name, i.unit, COUNT(DISTINCT i.order_id) AS n FROM ws_order_items i
        JOIN ws_orders o ON o.id = i.order_id
        WHERE o.customer_id = ? AND o.status != 'cancelled' AND i.product_id IS NOT NULL
        GROUP BY i.product_id ORDER BY n DESC LIMIT 30
        """,
        (customer_id,),
    ):
        entry = counts.setdefault(r["product_id"], {"product_id": r["product_id"], "name": r["name"], "unit": r["unit"], "score": 0})
        entry["score"] += r["n"]
    for r in conn.execute(
        """
        SELECT product_id, name, unit, COUNT(*) AS n FROM ws_shortages
        WHERE customer_id = ? AND source != 'request' AND product_id IS NOT NULL GROUP BY product_id
        """,
        (customer_id,),
    ):
        entry = counts.setdefault(r["product_id"], {"product_id": r["product_id"], "name": r["name"], "unit": r["unit"], "score": 0})
        entry["score"] += r["n"]
    ranked = sorted(counts.values(), key=lambda x: -x["score"])[: limit * 2]
    products = _orderable(conn, customer_id, [x["product_id"] for x in ranked if x["product_id"]])
    out = []
    for x in ranked:
        product = products.get(x["product_id"]) if x["product_id"] else None
        if x["product_id"] and not product:
            continue  # no longer orderable
        unit = x["unit"] if not product or product["units"].get(x["unit"]) else next(u for u in UNITS if product["units"].get(u))
        out.append({"product_id": x["product_id"], "name": product["name"] if product else x["name"], "unit": unit})
        if len(out) >= limit:
            break
    return out


def reminder_view(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_notebook_settings WHERE customer_id = ?", (customer_id,)).fetchone()
    day = row["reminder_day"] if row else None
    hour = row["reminder_hour"] if row else 10
    return {"day": day, "hour": hour, "day_label": DAYS[day] if day is not None else "", "days": DAYS}


def notebook(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    # A provision request the owner answered with a product: link the notebook line to it.
    conn.execute(
        """
        UPDATE ws_notebook_items SET product_id = (SELECT s.product_id FROM ws_shortages s WHERE s.id = ws_notebook_items.request_id)
        WHERE customer_id = ? AND status = 'open' AND product_id IS NULL AND request_id IN (
            SELECT id FROM ws_shortages WHERE customer_id = ? AND product_id IS NOT NULL)
        """,
        (customer_id, customer_id),
    )
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM ws_notebook_items WHERE customer_id = ? AND status = 'open' ORDER BY id", (customer_id,)
    )]
    products = _orderable(conn, customer_id, [r["product_id"] for r in rows])
    lines = [_line(r, products) for r in rows]
    return {
        "items": lines,
        "estimate": round(sum((l["price"] or 0) * l["qty"] for l in lines), 2),
        "shortages": customer_shortages(conn, customer_id),
        "frequent": frequent(conn, customer_id),
        "reminder": reminder_view(conn, customer_id),
        "synced_at": utc_now(),
    }


def catalog_index(conn: sqlite3.Connection, customer_id: int) -> List[List[Any]]:
    """Compact list for searching offline: [id, name, name_key, {unit: price}]."""
    out: List[List[Any]] = []
    page = 1
    while page <= 50:
        data = catalog(conn, customer_id, page=page, per_page=100)
        for item in data["items"]:
            out.append([item["id"], item["name"], normalize_name(item["name"]),
                        {u: v["price"] for u, v in item["units"].items() if v}])
        if page >= data["pages"]:
            break
        page += 1
    return out


# ------------------------------------------------------------------- sync


def _match_product(conn: sqlite3.Connection, customer_id: int, name: str) -> Optional[int]:
    key = normalize_name(name)
    if not key:
        return None
    rows = conn.execute("SELECT id FROM ws_products WHERE name_key = ? AND is_visible = 1 LIMIT 5", (key,)).fetchall()
    found = _orderable(conn, customer_id, [r["id"] for r in rows])
    return next(iter(found)) if found else None


def _request(conn: sqlite3.Connection, customer_id: int, name: str, unit: str, qty: int, note: str) -> Optional[int]:
    """Record a provision request once per name while one is still waiting."""
    key = normalize_name(name)
    for r in conn.execute(
        "SELECT id, name FROM ws_shortages WHERE customer_id = ? AND source = 'request' AND status = 'waiting'", (customer_id,)
    ):
        if normalize_name(r["name"]) == key:
            return r["id"]
    return conn.execute(
        "INSERT INTO ws_shortages(customer_id, product_id, name, unit, qty, source, note, status, created_at) VALUES(?, NULL, ?, ?, ?, 'request', ?, 'waiting', ?)",
        (customer_id, name, unit, qty, clean_text(note, 300), utc_now()),
    ).lastrowid


def _clean_fields(op: Dict[str, Any]) -> Dict[str, Any]:
    unit = op.get("unit") if op.get("unit") in UNITS else "piece"
    try:
        qty = int(op.get("qty") or 1)
    except (TypeError, ValueError):
        qty = 1
    return {"unit": unit, "qty": max(1, min(qty, MAX_QTY)), "note": clean_text(op.get("note"), 200)}


def sync(conn: sqlite3.Connection, customer_id: int, ops: List[Dict[str, Any]],
         save_voice=None) -> Dict[str, Any]:
    """Apply the phone's changes in order. ``save_voice(customer_id, data_url) -> file name``."""
    require_trader(conn, customer_id)
    if len(ops) > MAX_OPS:
        raise WholesaleError("تغييرات كتير مرة واحدة.")
    now = utc_now()
    applied = 0
    for op in ops:
        kind = op.get("op")
        cid = str(op.get("client_id") or "")
        if not _CLIENT_ID.match(cid):
            continue
        row = conn.execute("SELECT * FROM ws_notebook_items WHERE customer_id = ? AND client_id = ?", (customer_id, cid)).fetchone()
        if kind == "add":
            if row:
                continue  # already here: the phone sent it twice
            open_count = conn.execute("SELECT COUNT(*) FROM ws_notebook_items WHERE customer_id = ? AND status = 'open'", (customer_id,)).fetchone()[0]
            if open_count >= MAX_OPEN:
                raise WholesaleError("الدفتر مليان. انقل اللي فيه للسلة أو امسح شوية.")
            fields = _clean_fields(op)
            voice = save_voice(customer_id, op["voice"]) if op.get("voice") and save_voice else ""
            name = clean_text(op.get("name"), 150) or ("ملاحظة صوتية" if voice else "")
            if len(name) < 2:
                continue
            product_id = None
            if op.get("product_id"):
                pid = int(op["product_id"])
                product_id = pid if _orderable(conn, customer_id, [pid]) else None
            if not product_id and not voice:
                product_id = _match_product(conn, customer_id, name)
            request_id = None
            if not product_id and not voice:
                request_id = _request(conn, customer_id, name, fields["unit"], fields["qty"], fields["note"])
            conn.execute(
                """
                INSERT INTO ws_notebook_items(customer_id, client_id, product_id, name, unit, qty, note, voice_path, request_id, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (customer_id, cid, product_id, name, fields["unit"], fields["qty"], fields["note"], voice, request_id, now, now),
            )
        elif not row:
            continue
        elif kind == "update" and row["status"] == "open":
            fields = _clean_fields({**dict(row), **{k: v for k, v in op.items() if k in ("unit", "qty", "note")}})
            name = clean_text(op.get("name"), 150) or row["name"]
            product_id = row["product_id"]
            if op.get("product_id"):
                pid = int(op["product_id"])
                product_id = pid if _orderable(conn, customer_id, [pid]) else product_id
            conn.execute(
                "UPDATE ws_notebook_items SET name = ?, product_id = ?, unit = ?, qty = ?, note = ?, updated_at = ? WHERE id = ?",
                (name, product_id, fields["unit"], fields["qty"], fields["note"], now, row["id"]),
            )
        elif kind in ("remove", "moved") and row["status"] == "open":
            conn.execute(
                "UPDATE ws_notebook_items SET status = ?, updated_at = ? WHERE id = ?",
                ("removed" if kind == "remove" else "moved", now, row["id"]),
            )
        else:
            continue
        applied += 1
    result = notebook(conn, customer_id)
    result["applied"] = applied
    return result


def voice_path(conn: sqlite3.Connection, customer_id: int, item_id: int) -> str:
    row = conn.execute(
        "SELECT voice_path FROM ws_notebook_items WHERE id = ? AND customer_id = ?", (item_id, customer_id)
    ).fetchone()
    if not row or not row["voice_path"]:
        raise WholesaleError("مفيش تسجيل.", 404)
    return row["voice_path"]


# --------------------------------------------------------------- reminder


def set_reminder(conn: sqlite3.Connection, customer_id: int, day: Optional[int], hour: int = 10) -> Dict[str, Any]:
    require_trader(conn, customer_id)
    if day is not None and not 0 <= int(day) <= 6:
        raise WholesaleError("اختار يوم.")
    if not 0 <= int(hour) <= 23:
        raise WholesaleError("الساعة مش مظبوطة.")
    now = utc_now()
    conn.execute(
        """
        INSERT INTO ws_notebook_settings(customer_id, reminder_day, reminder_hour, last_reminded_at, updated_at) VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET reminder_day = excluded.reminder_day, reminder_hour = excluded.reminder_hour,
            last_reminded_at = excluded.last_reminded_at, updated_at = excluded.updated_at
        """,
        (customer_id, day, int(hour), now, now),  # start counting from now: no reminder for a moment already passed
    )
    return reminder_view(conn, customer_id)


def last_reminder_moment(day: int, hour: int, now: Optional[datetime] = None) -> datetime:
    """The latest moment, at or before now, that falls on the reminder day and hour (local time)."""
    now = now or _local_now()
    js_today = (now.weekday() + 1) % 7  # Python Monday=0 -> JavaScript Sunday=0
    back = (js_today - day) % 7
    moment = (now - timedelta(days=back)).replace(hour=hour, minute=0, second=0, microsecond=0)
    if moment > now:
        moment -= timedelta(days=7)
    return moment


def check_reminder(conn: sqlite3.Connection, customer_id: int, now: Optional[datetime] = None) -> bool:
    """Send this week's reminder if its time came. Returns True when sent."""
    row = conn.execute("SELECT * FROM ws_notebook_settings WHERE customer_id = ?", (customer_id,)).fetchone()
    if not row or row["reminder_day"] is None:
        return False
    moment = last_reminder_moment(row["reminder_day"], row["reminder_hour"], now)
    last = row["last_reminded_at"]
    if last:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        if last_dt >= moment:
            return False
    count = conn.execute("SELECT COUNT(*) FROM ws_notebook_items WHERE customer_id = ? AND status = 'open'", (customer_id,)).fetchone()[0]
    body = f"عندك {count} صنف في الدفتر. حوّله لطلب من صفحة النواقص." if count else "شوف محلك ناقصه إيه وسجّله في الدفتر، وبعدين حوّله لطلب."
    notify(conn, customer_id, "notebook", f"النهارده {DAYS[row['reminder_day']]}: وقت مراجعة النواقص", body)
    stamp = (now or _local_now()).astimezone(timezone.utc).isoformat(timespec="seconds")
    conn.execute("UPDATE ws_notebook_settings SET last_reminded_at = ? WHERE customer_id = ?", (stamp, customer_id))
    return True


def reminder_ics(conn: sqlite3.Connection, customer_id: int, page_url: str) -> str:
    view = reminder_view(conn, customer_id)
    if view["day"] is None:
        raise WholesaleError("اختار يوم التذكير الأول.")
    start = last_reminder_moment(view["day"], view["hour"]) + timedelta(days=7)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tzid = getattr(start.tzinfo, "key", None)
    dtstart = f"DTSTART;TZID={tzid}:{start.strftime('%Y%m%dT%H%M%S')}" if tzid else f"DTSTART:{start.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Rose//Wholesale notebook//AR", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:rose-notebook-{customer_id}@rose",
        f"DTSTAMP:{stamp}",
        dtstart,
        "DURATION:PT15M",
        f"RRULE:FREQ=WEEKLY;BYDAY={ICS_DAYS[view['day']]}",
        "SUMMARY:روز جملة: راجع دفتر النواقص",
        f"DESCRIPTION:سجّل اللي ناقص المحل وحوّله لطلب: {page_url}",
        f"URL:{page_url}",
        "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:راجع دفتر النواقص", "TRIGGER:PT0M", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "".join(_fold(line) for line in lines)


def _fold(line: str) -> str:
    """iCalendar lines are at most 75 bytes; longer ones continue on the next line after a space."""
    out, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > (75 if not out else 74):
            out.append(current)
            current = b""
        current += b
    out.append(current)
    return "\r\n ".join(x.decode("utf-8") for x in out) + "\r\n"
