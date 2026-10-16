"""Wholesale phase 10: traders talking to each other, splitting a carton, and nearby traders.

Privacy first:
    - A trader is hidden until they turn on "show me to nearby traders".
      Their phone number stays hidden unless they also choose to show it.
    - "Nearby" means the same delivery zone, or the same area name. No GPS.
    - To start a conversation both traders must be visible. A conversation that
      already exists (for example from a carton split) keeps working.
    - Either side can block the other. A report sends the admin the reported
      message with a few messages around it, never the whole inbox.
    - The admin can stop a trader from chatting.

Carton split («اطلبوا مع بعض»): a visible trader offers a carton of a product
and keeps some pieces. Nearby traders reserve the rest. When the pieces add up
to the carton, everyone is told, and the organizer adds the carton to their
own order. Money between the traders is settled between them; Rose sells the
carton to the organizer as usual.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db import utc_now
from .wholesale_orders import catalog, notify, require_trader
from .wholesale_service import WholesaleError, clean_text, normalize_name

SPLIT_DAYS = 3
MESSAGE_MAX = 1000
RATE_PER_MINUTE = 20
REPORT_CONTEXT = 4


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------- profiles


def _profile_row(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_social_profiles WHERE customer_id = ?", (customer_id,)).fetchone()
    return dict(row) if row else {"customer_id": customer_id, "visible": 0, "show_phone": 0, "bio": "", "chat_suspended": 0}


def _trader_info(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT c.id, c.name, c.phone, t.status, t.shop_name, t.area, t.zone_id, z.name AS zone_name
        FROM customers c LEFT JOIN ws_traders t ON t.customer_id = c.id
        LEFT JOIN ws_delivery_zones z ON z.id = t.zone_id WHERE c.id = ?
        """,
        (customer_id,),
    ).fetchone()
    return dict(row) if row else {}


def _display_name(info: Dict[str, Any]) -> str:
    return info.get("shop_name") or (info.get("name") or "تاجر").split(" ")[0]


def _near(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    if a.get("zone_id") and b.get("zone_id"):
        return a["zone_id"] == b["zone_id"]
    ka, kb = normalize_name(a.get("area") or ""), normalize_name(b.get("area") or "")
    return bool(ka) and ka == kb


def profile(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    p = _profile_row(conn, customer_id)
    info = _trader_info(conn, customer_id)
    return {
        "visible": bool(p["visible"]),
        "show_phone": bool(p["show_phone"]),
        "bio": p["bio"],
        "chat_suspended": bool(p["chat_suspended"]),
        "display_name": _display_name(info),
        "area": info.get("zone_name") or info.get("area") or "",
    }


def set_profile(conn: sqlite3.Connection, customer_id: int, visible: Optional[bool], show_phone: Optional[bool], bio: Optional[str]) -> Dict[str, Any]:
    require_trader(conn, customer_id)
    p = _profile_row(conn, customer_id)
    values = {
        "visible": int(bool(visible)) if visible is not None else p["visible"],
        "show_phone": int(bool(show_phone)) if show_phone is not None else p["show_phone"],
        "bio": clean_text(bio, 160) if bio is not None else p["bio"],
    }
    if not values["visible"]:
        values["show_phone"] = 0
    conn.execute(
        """
        INSERT INTO ws_social_profiles(customer_id, visible, show_phone, bio, updated_at) VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET visible = excluded.visible, show_phone = excluded.show_phone,
            bio = excluded.bio, updated_at = excluded.updated_at
        """,
        (customer_id, values["visible"], values["show_phone"], values["bio"], utc_now()),
    )
    return profile(conn, customer_id)


def _blocked(conn: sqlite3.Connection, a: int, b: int) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM ws_chat_blocks WHERE (blocker_id = ? AND blocked_id = ?) OR (blocker_id = ? AND blocked_id = ?)", (a, b, b, a)
    ).fetchone())


def _public(conn: sqlite3.Connection, customer_id: int, info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    info = info or _trader_info(conn, customer_id)
    p = _profile_row(conn, customer_id)
    return {
        "customer_id": customer_id,
        "name": _display_name(info),
        "area": info.get("zone_name") or info.get("area") or "",
        "phone": info.get("phone") if p["visible"] and p["show_phone"] else "",
        "bio": p["bio"] if p["visible"] else "",
    }


def nearby(conn: sqlite3.Connection, customer_id: int) -> Dict[str, Any]:
    require_trader(conn, customer_id)
    me = _trader_info(conn, customer_id)
    traders = []
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.phone, t.shop_name, t.area, t.zone_id, z.name AS zone_name
        FROM ws_social_profiles p JOIN ws_traders t ON t.customer_id = p.customer_id AND t.status = 'approved'
        JOIN customers c ON c.id = p.customer_id LEFT JOIN ws_delivery_zones z ON z.id = t.zone_id
        WHERE p.visible = 1 AND p.chat_suspended = 0 AND p.customer_id != ?
        """,
        (customer_id,),
    ).fetchall()
    for r in rows:
        info = dict(r)
        if not _near(me, info) or _blocked(conn, customer_id, r["id"]):
            continue
        item = _public(conn, r["id"], info)
        item["thread_id"] = _thread_id(conn, customer_id, r["id"])
        traders.append(item)
    traders.sort(key=lambda x: x["name"])
    return {"me": profile(conn, customer_id), "traders": traders, "splits": open_splits(conn, customer_id)}


# ----------------------------------------------------------------- threads


def _pair(a: int, b: int):
    return (a, b) if a < b else (b, a)


def _thread_id(conn: sqlite3.Connection, a: int, b: int) -> Optional[int]:
    x, y = _pair(a, b)
    row = conn.execute("SELECT id FROM ws_chat_threads WHERE a_id = ? AND b_id = ?", (x, y)).fetchone()
    return row["id"] if row else None


def _ensure_thread(conn: sqlite3.Connection, a: int, b: int) -> int:
    existing = _thread_id(conn, a, b)
    if existing:
        return existing
    x, y = _pair(a, b)
    return conn.execute("INSERT INTO ws_chat_threads(a_id, b_id, created_at) VALUES(?, ?, ?)", (x, y, utc_now())).lastrowid


def _check_can_chat(conn: sqlite3.Connection, me: int, other: int) -> None:
    require_trader(conn, me)
    if me == other:
        raise WholesaleError("مينفعش تكلّم نفسك.")
    if _profile_row(conn, me)["chat_suspended"]:
        raise WholesaleError("المحادثات واقفة عندك. كلّمنا لو فيه حاجة.", 403)
    if _blocked(conn, me, other):
        raise WholesaleError("المحادثة دي مقفولة.", 403)
    other_trader = conn.execute("SELECT status FROM ws_traders WHERE customer_id = ?", (other,)).fetchone()
    if not other_trader or other_trader["status"] != "approved" or _profile_row(conn, other)["chat_suspended"]:
        raise WholesaleError("التاجر ده مش متاح للمحادثة.", 403)


def start_thread(conn: sqlite3.Connection, me: int, other: int) -> int:
    _check_can_chat(conn, me, other)
    existing = _thread_id(conn, me, other)
    if existing:
        return existing
    if not _profile_row(conn, me)["visible"]:
        raise WholesaleError("علشان تبدأ محادثة، شغّل «ظهوري للتجار القريبين» الأول.", 403)
    if not _profile_row(conn, other)["visible"] or not _near(_trader_info(conn, me), _trader_info(conn, other)):
        raise WholesaleError("التاجر ده مش ظاهر للتجار القريبين.", 403)
    return _ensure_thread(conn, me, other)


def _thread(conn: sqlite3.Connection, me: int, thread_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_chat_threads WHERE id = ? AND (a_id = ? OR b_id = ?)", (thread_id, me, me)).fetchone()
    if not row:
        raise WholesaleError("المحادثة مش موجودة.", 404)
    t = dict(row)
    t["other_id"] = t["b_id"] if t["a_id"] == me else t["a_id"]
    return t


def _post(conn: sqlite3.Connection, thread_id: int, sender: Optional[int], kind: str, body: str, split_id: Optional[int] = None) -> int:
    now = utc_now()
    mid = conn.execute(
        "INSERT INTO ws_chat_messages(thread_id, sender_id, kind, body, split_id, created_at) VALUES(?, ?, ?, ?, ?, ?)",
        (thread_id, sender, kind, body[:MESSAGE_MAX], split_id, now),
    ).lastrowid
    conn.execute("UPDATE ws_chat_threads SET last_message_at = ? WHERE id = ?", (now, thread_id))
    if sender:
        _mark_read(conn, thread_id, sender, mid)
    return mid


def _mark_read(conn: sqlite3.Connection, thread_id: int, customer_id: int, message_id: int) -> None:
    conn.execute(
        """
        INSERT INTO ws_chat_reads(thread_id, customer_id, last_read_id) VALUES(?, ?, ?)
        ON CONFLICT(thread_id, customer_id) DO UPDATE SET last_read_id = MAX(last_read_id, excluded.last_read_id)
        """,
        (thread_id, customer_id, message_id),
    )


def threads(conn: sqlite3.Connection, me: int) -> List[Dict[str, Any]]:
    require_trader(conn, me)
    out = []
    for t in conn.execute(
        "SELECT * FROM ws_chat_threads WHERE (a_id = ? OR b_id = ?) AND last_message_at IS NOT NULL ORDER BY last_message_at DESC LIMIT 100",
        (me, me),
    ):
        other = t["b_id"] if t["a_id"] == me else t["a_id"]
        last = conn.execute("SELECT * FROM ws_chat_messages WHERE thread_id = ? ORDER BY id DESC LIMIT 1", (t["id"],)).fetchone()
        read = conn.execute("SELECT last_read_id FROM ws_chat_reads WHERE thread_id = ? AND customer_id = ?", (t["id"], me)).fetchone()
        unread = conn.execute(
            "SELECT COUNT(*) FROM ws_chat_messages WHERE thread_id = ? AND id > ? AND COALESCE(sender_id, 0) != ?",
            (t["id"], read["last_read_id"] if read else 0, me),
        ).fetchone()[0]
        out.append({
            "id": t["id"],
            "other": _public(conn, other),
            "last": {"body": last["body"], "kind": last["kind"], "mine": last["sender_id"] == me, "at": last["created_at"]} if last else None,
            "unread": unread,
            "blocked": _blocked(conn, me, other),
        })
    return out


def unread_total(conn: sqlite3.Connection, me: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM ws_chat_messages m JOIN ws_chat_threads t ON t.id = m.thread_id
        LEFT JOIN ws_chat_reads r ON r.thread_id = t.id AND r.customer_id = ?
        WHERE (t.a_id = ? OR t.b_id = ?) AND COALESCE(m.sender_id, 0) != ? AND m.id > COALESCE(r.last_read_id, 0)
        """,
        (me, me, me, me),
    ).fetchone()[0]


def messages(conn: sqlite3.Connection, me: int, thread_id: int, after_id: int = 0) -> Dict[str, Any]:
    require_trader(conn, me)
    t = _thread(conn, me, thread_id)
    if after_id:
        rows = conn.execute("SELECT * FROM ws_chat_messages WHERE thread_id = ? AND id > ? ORDER BY id LIMIT 200", (thread_id, after_id)).fetchall()
    else:
        rows = list(reversed(conn.execute("SELECT * FROM ws_chat_messages WHERE thread_id = ? ORDER BY id DESC LIMIT 100", (thread_id,)).fetchall()))
    items = [{"id": r["id"], "kind": r["kind"], "body": r["body"], "mine": r["sender_id"] == me, "split_id": r["split_id"], "at": r["created_at"]} for r in rows]
    if items:
        _mark_read(conn, thread_id, me, items[-1]["id"])
    split_ids = sorted({i["split_id"] for i in items if i["split_id"]})
    other = t["other_id"]
    i_blocked = bool(conn.execute("SELECT 1 FROM ws_chat_blocks WHERE blocker_id = ? AND blocked_id = ?", (me, other)).fetchone())
    return {
        "id": thread_id,
        "other": _public(conn, other),
        "messages": items,
        "splits": {str(s): split_view(conn, s, me) for s in split_ids},
        "i_blocked": i_blocked,
        "can_send": not _blocked(conn, me, other) and not _profile_row(conn, me)["chat_suspended"],
        "suspended": bool(_profile_row(conn, me)["chat_suspended"]),
    }


def send(conn: sqlite3.Connection, me: int, thread_id: int, body: str) -> Dict[str, Any]:
    t = _thread(conn, me, thread_id)
    _check_can_chat(conn, me, t["other_id"])
    text = str(body or "").strip()
    if not text:
        raise WholesaleError("اكتب رسالة.")
    if len(text) > MESSAGE_MAX:
        raise WholesaleError("الرسالة طويلة أوي.")
    minute_ago = _iso(_now_dt() - timedelta(minutes=1))
    recent = conn.execute("SELECT COUNT(*) FROM ws_chat_messages WHERE sender_id = ? AND created_at > ?", (me, minute_ago)).fetchone()[0]
    if recent >= RATE_PER_MINUTE:
        raise WholesaleError("رسايل كتير ورا بعض. استنى دقيقة.", 429)
    _post(conn, thread_id, me, "text", text)
    return messages(conn, me, thread_id)


def block(conn: sqlite3.Connection, me: int, thread_id: int, on: bool) -> Dict[str, Any]:
    t = _thread(conn, me, thread_id)
    if on:
        conn.execute("INSERT OR IGNORE INTO ws_chat_blocks(blocker_id, blocked_id, created_at) VALUES(?, ?, ?)", (me, t["other_id"], utc_now()))
    else:
        conn.execute("DELETE FROM ws_chat_blocks WHERE blocker_id = ? AND blocked_id = ?", (me, t["other_id"]))
    return messages(conn, me, thread_id)


def report(conn: sqlite3.Connection, me: int, thread_id: int, message_id: Optional[int], reason: str) -> None:
    t = _thread(conn, me, thread_id)
    if message_id:
        msg = conn.execute("SELECT sender_id FROM ws_chat_messages WHERE id = ? AND thread_id = ?", (message_id, thread_id)).fetchone()
        if not msg or msg["sender_id"] != t["other_id"]:
            raise WholesaleError("اختار رسالة من التاجر التاني.")
    reason = clean_text(reason, 300)
    if not reason:
        raise WholesaleError("اكتب سبب الإبلاغ.")
    conn.execute(
        "INSERT INTO ws_chat_reports(reporter_id, reported_id, thread_id, message_id, reason, created_at) VALUES(?, ?, ?, ?, ?, ?)",
        (me, t["other_id"], thread_id, message_id, reason, utc_now()),
    )


# ------------------------------------------------------------------ splits


def _expire_splits(conn: sqlite3.Connection) -> None:
    now = utc_now()
    for s in conn.execute("SELECT id FROM ws_splits WHERE status = 'open' AND expires_at < ?", (now,)).fetchall():
        conn.execute("UPDATE ws_splits SET status = 'cancelled', closed_at = ? WHERE id = ?", (now, s["id"]))
        for m in conn.execute("SELECT customer_id FROM ws_split_shares WHERE split_id = ?", (s["id"],)).fetchall():
            notify(conn, m["customer_id"], "split", "تقسيم كرتونة خلص وقته", "الكرتونة ماكملتش في الوقت، فالتقسيم اتلغى.")


def _carton(conn: sqlite3.Connection, customer_id: int, product_id: int) -> Optional[Dict[str, Any]]:
    found = catalog(conn, customer_id, ids=[product_id], per_page=10)["items"]
    if not found:
        return None
    unit = found[0]["units"].get("carton")
    if not unit or not unit.get("pieces") or unit["pieces"] < 2:
        return None
    return {"product": found[0], "price": unit["price"], "pieces": int(unit["pieces"])}


def split_view(conn: sqlite3.Connection, split_id: int, viewer: int) -> Dict[str, Any]:
    s = conn.execute(
        "SELECT sp.*, p.name AS product_name FROM ws_splits sp JOIN ws_products p ON p.id = sp.product_id WHERE sp.id = ?", (split_id,)
    ).fetchone()
    if not s:
        raise WholesaleError("التقسيم مش موجود.", 404)
    shares = [dict(r) for r in conn.execute("SELECT * FROM ws_split_shares WHERE split_id = ? ORDER BY created_at", (split_id,))]
    taken = sum(r["pieces"] for r in shares)
    carton = _carton(conn, s["organizer_id"], s["product_id"])
    piece_price = round(carton["price"] / s["pieces_total"], 2) if carton else None
    mine = next((r["pieces"] for r in shares if r["customer_id"] == viewer), 0)
    return {
        "id": s["id"],
        "product_id": s["product_id"],
        "product_name": s["product_name"],
        "pieces_total": s["pieces_total"],
        "taken": taken,
        "remaining": s["pieces_total"] - taken,
        "carton_price": carton["price"] if carton else None,
        "piece_price": piece_price,
        "status": s["status"],
        "note": s["note"],
        "expires_at": s["expires_at"],
        "organizer": _public(conn, s["organizer_id"]),
        "is_organizer": s["organizer_id"] == viewer,
        "my_pieces": mine,
        "my_cost": round(mine * piece_price, 2) if piece_price else None,
        "members": [{"name": _public(conn, r["customer_id"])["name"], "pieces": r["pieces"], "is_organizer": r["customer_id"] == s["organizer_id"]} for r in shares],
        "product": carton["product"] if carton and s["organizer_id"] == viewer else None,
    }


def open_splits(conn: sqlite3.Connection, viewer: int) -> List[Dict[str, Any]]:
    _expire_splits(conn)
    me = _trader_info(conn, viewer)
    out = []
    for s in conn.execute(
        """
        SELECT sp.id, sp.organizer_id FROM ws_splits sp WHERE sp.status IN ('open', 'full')
        AND (sp.status = 'open' OR sp.organizer_id = ? OR EXISTS (SELECT 1 FROM ws_split_shares x WHERE x.split_id = sp.id AND x.customer_id = ?))
        ORDER BY sp.id DESC LIMIT 50
        """,
        (viewer, viewer),
    ):
        if s["organizer_id"] != viewer:
            if _blocked(conn, viewer, s["organizer_id"]) or not _near(me, _trader_info(conn, s["organizer_id"])):
                continue
        out.append(split_view(conn, s["id"], viewer))
    return out


def create_split(conn: sqlite3.Connection, me: int, product_id: int, my_pieces: int, note: str = "") -> Dict[str, Any]:
    require_trader(conn, me)
    p = _profile_row(conn, me)
    if p["chat_suspended"]:
        raise WholesaleError("المحادثات واقفة عندك. كلّمنا لو فيه حاجة.", 403)
    if not p["visible"]:
        raise WholesaleError("علشان تعمل تقسيم، شغّل «ظهوري للتجار القريبين» الأول.", 403)
    carton = _carton(conn, me, product_id)
    if not carton:
        raise WholesaleError("الصنف ده مالوش كرتونة تتقسم.")
    my_pieces = int(my_pieces or 0)
    if my_pieces < 1 or my_pieces >= carton["pieces"]:
        raise WholesaleError(f"نصيبك لازم يكون من 1 لحد {carton['pieces'] - 1} قطعة.")
    if conn.execute("SELECT COUNT(*) FROM ws_splits WHERE organizer_id = ? AND status = 'open'", (me,)).fetchone()[0] >= 5:
        raise WholesaleError("عندك تقسيمات مفتوحة كتير.")
    info = _trader_info(conn, me)
    now = _now_dt()
    split_id = conn.execute(
        """
        INSERT INTO ws_splits(organizer_id, product_id, pieces_total, note, status, zone_id, area_key, expires_at, created_at)
        VALUES(?, ?, ?, ?, 'open', ?, ?, ?, ?)
        """,
        (me, product_id, carton["pieces"], clean_text(note, 200), info.get("zone_id"), normalize_name(info.get("area") or ""),
         _iso(now + timedelta(days=SPLIT_DAYS)), _iso(now)),
    ).lastrowid
    conn.execute("INSERT INTO ws_split_shares(split_id, customer_id, pieces, created_at) VALUES(?, ?, ?, ?)", (split_id, me, my_pieces, _iso(now)))
    return split_view(conn, split_id, me)


def _split(conn: sqlite3.Connection, split_id: int) -> Dict[str, Any]:
    _expire_splits(conn)
    row = conn.execute("SELECT * FROM ws_splits WHERE id = ?", (split_id,)).fetchone()
    if not row:
        raise WholesaleError("التقسيم مش موجود.", 404)
    return dict(row)


def join_split(conn: sqlite3.Connection, me: int, split_id: int, pieces: int) -> Dict[str, Any]:
    s = _split(conn, split_id)
    if s["status"] != "open":
        raise WholesaleError("التقسيم ده مش مفتوح.")
    organizer = s["organizer_id"]
    if organizer == me:
        raise WholesaleError("ده تقسيمك.")
    _check_can_chat(conn, me, organizer)
    if not _near(_trader_info(conn, me), _trader_info(conn, organizer)):
        raise WholesaleError("التقسيم ده لتجار منطقة تانية.", 403)
    pieces = int(pieces or 0)
    current = conn.execute("SELECT pieces FROM ws_split_shares WHERE split_id = ? AND customer_id = ?", (split_id, me)).fetchone()
    taken = conn.execute("SELECT COALESCE(SUM(pieces), 0) FROM ws_split_shares WHERE split_id = ?", (split_id,)).fetchone()[0]
    free = s["pieces_total"] - taken + (current["pieces"] if current else 0)
    if pieces < 1 or pieces > free:
        raise WholesaleError(f"المتاح {free} قطعة.")
    now = utc_now()
    conn.execute(
        """
        INSERT INTO ws_split_shares(split_id, customer_id, pieces, created_at) VALUES(?, ?, ?, ?)
        ON CONFLICT(split_id, customer_id) DO UPDATE SET pieces = excluded.pieces
        """,
        (split_id, me, pieces, now),
    )
    product = conn.execute("SELECT name FROM ws_products WHERE id = ?", (s["product_id"],)).fetchone()["name"]
    thread_id = _ensure_thread(conn, me, organizer)
    who = _public(conn, me)["name"]
    _post(conn, thread_id, me, "split", f"{who} حجز {pieces} قطعة من كرتونة {product}.", split_id)
    taken = conn.execute("SELECT COALESCE(SUM(pieces), 0) FROM ws_split_shares WHERE split_id = ?", (split_id,)).fetchone()[0]
    if taken >= s["pieces_total"]:
        conn.execute("UPDATE ws_splits SET status = 'full' WHERE id = ?", (split_id,))
        for m in conn.execute("SELECT customer_id FROM ws_split_shares WHERE split_id = ?", (split_id,)).fetchall():
            if m["customer_id"] == organizer:
                notify(conn, organizer, "split", f"كرتونة {product} اكتملت", "ضيفها لطلبك من «تجار قريبين»، واتفق مع الباقيين على الحساب.")
            else:
                notify(conn, m["customer_id"], "split", f"كرتونة {product} اكتملت", "صاحب التقسيم هيطلبها. الحساب بينكم.")
    return split_view(conn, split_id, me)


def leave_split(conn: sqlite3.Connection, me: int, split_id: int) -> Dict[str, Any]:
    s = _split(conn, split_id)
    if s["organizer_id"] == me:
        raise WholesaleError("إنت صاحب التقسيم. تقدر تلغيه.")
    if s["status"] != "open":
        raise WholesaleError("الكرتونة اكتملت. اتفق مع صاحب التقسيم.")
    cur = conn.execute("DELETE FROM ws_split_shares WHERE split_id = ? AND customer_id = ?", (split_id, me))
    if not cur.rowcount:
        raise WholesaleError("إنت مش في التقسيم ده.")
    thread_id = _thread_id(conn, me, s["organizer_id"])
    if thread_id:
        _post(conn, thread_id, me, "split", f"{_public(conn, me)['name']} خرج من التقسيم.", split_id)
    return split_view(conn, split_id, me)


def cancel_split(conn: sqlite3.Connection, me: int, split_id: int) -> Dict[str, Any]:
    s = _split(conn, split_id)
    if s["organizer_id"] != me:
        raise WholesaleError("صاحب التقسيم بس يقدر يلغيه.", 403)
    if s["status"] not in ("open", "full"):
        raise WholesaleError("التقسيم ده مقفول.")
    conn.execute("UPDATE ws_splits SET status = 'cancelled', closed_at = ? WHERE id = ?", (utc_now(), split_id))
    product = conn.execute("SELECT name FROM ws_products WHERE id = ?", (s["product_id"],)).fetchone()["name"]
    for m in conn.execute("SELECT customer_id FROM ws_split_shares WHERE split_id = ? AND customer_id != ?", (split_id, me)).fetchall():
        notify(conn, m["customer_id"], "split", f"تقسيم كرتونة {product} اتلغى", "صاحب التقسيم لغاه.")
    return split_view(conn, split_id, me)


def order_split(conn: sqlite3.Connection, me: int, split_id: int) -> Dict[str, Any]:
    """The organizer takes the full carton into their cart."""
    s = _split(conn, split_id)
    if s["organizer_id"] != me:
        raise WholesaleError("صاحب التقسيم بس يقدر يطلبه.", 403)
    if s["status"] != "full":
        raise WholesaleError("الكرتونة لسه ماكملتش.")
    view = split_view(conn, split_id, me)
    if not view["product"]:
        raise WholesaleError("الكرتونة مش متاحة دلوقتي.")
    conn.execute("UPDATE ws_splits SET status = 'ordered', closed_at = ? WHERE id = ?", (utc_now(), split_id))
    for m in conn.execute("SELECT customer_id FROM ws_split_shares WHERE split_id = ? AND customer_id != ?", (split_id, me)).fetchall():
        notify(conn, m["customer_id"], "split", f"كرتونة {view['product_name']} اتطلبت", "صاحب التقسيم ضافها لطلبه.")
    return split_view(conn, split_id, me)


# ------------------------------------------------------------------- admin


def admin_reports(conn: sqlite3.Connection, status: str = "open") -> Dict[str, Any]:
    out = []
    for r in conn.execute(
        "SELECT * FROM ws_chat_reports WHERE (? = '' OR status = ?) ORDER BY id DESC LIMIT 100", (status, status)
    ):
        context = []
        if r["thread_id"]:
            pivot = r["message_id"] or conn.execute("SELECT MAX(id) FROM ws_chat_messages WHERE thread_id = ?", (r["thread_id"],)).fetchone()[0] or 0
            rows = conn.execute(
                """
                SELECT * FROM (SELECT * FROM ws_chat_messages WHERE thread_id = ? AND id <= ? ORDER BY id DESC LIMIT ?)
                UNION SELECT * FROM (SELECT * FROM ws_chat_messages WHERE thread_id = ? AND id > ? ORDER BY id LIMIT ?)
                ORDER BY id
                """,
                (r["thread_id"], pivot, REPORT_CONTEXT + 1, r["thread_id"], pivot, REPORT_CONTEXT),
            ).fetchall()
            context = [{"id": m["id"], "from": "reported" if m["sender_id"] == r["reported_id"] else "reporter" if m["sender_id"] == r["reporter_id"] else "system",
                        "body": m["body"], "at": m["created_at"], "flagged": m["id"] == r["message_id"]} for m in rows]
        reported = _trader_info(conn, r["reported_id"])
        out.append({
            "id": r["id"],
            "status": r["status"],
            "reason": r["reason"],
            "created_at": r["created_at"],
            "admin_note": r["admin_note"],
            "reporter": {"id": r["reporter_id"], **{k: v for k, v in _trader_info(conn, r["reporter_id"]).items() if k in ("name", "phone", "shop_name")}},
            "reported": {"id": r["reported_id"], "name": reported.get("name"), "phone": reported.get("phone"), "shop_name": reported.get("shop_name"),
                         "suspended": bool(_profile_row(conn, r["reported_id"])["chat_suspended"]),
                         "reports": conn.execute("SELECT COUNT(*) FROM ws_chat_reports WHERE reported_id = ?", (r["reported_id"],)).fetchone()[0]},
            "context": context,
        })
    suspended = [
        {"customer_id": r["customer_id"], "name": r["name"], "phone": r["phone"], "shop_name": r["shop_name"] or ""}
        for r in conn.execute(
            """
            SELECT p.customer_id, c.name, c.phone, t.shop_name FROM ws_social_profiles p JOIN customers c ON c.id = p.customer_id
            LEFT JOIN ws_traders t ON t.customer_id = p.customer_id WHERE p.chat_suspended = 1
            """
        )
    ]
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "items": out,
        "suspended": suspended,
        "stats": {
            "visible": one("SELECT COUNT(*) FROM ws_social_profiles WHERE visible = 1"),
            "threads": one("SELECT COUNT(*) FROM ws_chat_threads WHERE last_message_at IS NOT NULL"),
            "open_splits": one("SELECT COUNT(*) FROM ws_splits WHERE status IN ('open', 'full')"),
            "ordered_splits": one("SELECT COUNT(*) FROM ws_splits WHERE status = 'ordered'"),
            "open_reports": one("SELECT COUNT(*) FROM ws_chat_reports WHERE status = 'open'"),
        },
    }


def close_report(conn: sqlite3.Connection, report_id: int, note: str) -> None:
    cur = conn.execute(
        "UPDATE ws_chat_reports SET status = 'closed', admin_note = ?, closed_at = ? WHERE id = ?", (clean_text(note, 300), utc_now(), report_id)
    )
    if not cur.rowcount:
        raise WholesaleError("البلاغ مش موجود.", 404)


def suspend_chat(conn: sqlite3.Connection, customer_id: int, on: bool) -> None:
    conn.execute(
        """
        INSERT INTO ws_social_profiles(customer_id, chat_suspended, updated_at) VALUES(?, ?, ?)
        ON CONFLICT(customer_id) DO UPDATE SET chat_suspended = excluded.chat_suspended, updated_at = excluded.updated_at
        """,
        (customer_id, 1 if on else 0, utc_now()),
    )
    if on:
        notify(conn, customer_id, "chat", "المحادثات اتوقفت عندك", "بسبب بلاغات من تجار. كلّمنا لو شايف إن فيه غلط.")
