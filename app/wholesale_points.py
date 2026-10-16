"""Wholesale points (نقاط الجملة): a points balance and rewards that belong to the wholesale store only.

    earn     after an order is delivered, one point per N EGP of the final products
             total (setting points_egp_per_point; 0 = off)
    return   a refunded return takes back its share of the order's points
    redeem   the trader swaps points for a reward and gets a 6-digit code
    refund   a cancelled code gives the points back
    adjust   a manual correction by the admin

The balance may go below zero after a return; the trader then can't redeem
until new orders bring it back up.
"""

from __future__ import annotations

import secrets
import sqlite3
from typing import Any, Dict, List, Optional

from .db import utc_now
from .wholesale_service import WholesaleError, clean_text

KIND_LABEL = {"earn": "نقاط طلب", "return": "مرتجع", "redeem": "استبدال", "refund": "رجوع نقاط استبدال", "adjust": "تعديل من الإدارة"}
STATUS_LABEL = {"pending": "مستني الاستخدام", "used": "اتستخدم", "cancelled": "اتلغى"}


def balance(conn: sqlite3.Connection, customer_id: int) -> int:
    row = conn.execute("SELECT balance FROM ws_point_accounts WHERE customer_id = ?", (customer_id,)).fetchone()
    return int(row["balance"]) if row else 0


def _entry(conn: sqlite3.Connection, customer_id: int, kind: str, points: int, description: str,
           source_type: str = "", source_id: Optional[int] = None) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO ws_point_accounts(customer_id, balance, lifetime_earned, updated_at) VALUES(?, 0, 0, ?)
        ON CONFLICT(customer_id) DO NOTHING
        """,
        (customer_id, now),
    )
    conn.execute(
        "UPDATE ws_point_accounts SET balance = balance + ?, lifetime_earned = lifetime_earned + ?, updated_at = ? WHERE customer_id = ?",
        (points, points if kind == "earn" else 0, now, customer_id),
    )
    conn.execute(
        "INSERT INTO ws_point_entries(customer_id, kind, points, description, source_type, source_id, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (customer_id, kind, points, description[:200], source_type, source_id, now),
    )


def earn_for_order(conn: sqlite3.Connection, customer_id: int, order_id: int, points: int, description: str) -> bool:
    """Credit an order's points once. Returns False if already credited."""
    if points <= 0:
        return False
    if conn.execute(
        "SELECT 1 FROM ws_point_entries WHERE source_type = 'order' AND source_id = ? AND kind = 'earn'", (order_id,)
    ).fetchone():
        return False
    _entry(conn, customer_id, "earn", int(points), description, "order", order_id)
    return True


def take_back(conn: sqlite3.Connection, customer_id: int, points: int, description: str, return_id: int) -> None:
    if points > 0:
        _entry(conn, customer_id, "return", -int(points), description, "return", return_id)


def adjust(conn: sqlite3.Connection, customer_id: int, points: int, note: str) -> Dict[str, Any]:
    note = clean_text(note, 150)
    if not points:
        raise WholesaleError("اكتب عدد النقاط.")
    if not note:
        raise WholesaleError("اكتب سبب التعديل.")
    if not conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
        raise WholesaleError("الحساب مش موجود.", 404)
    _entry(conn, customer_id, "adjust", int(points), note, "admin")
    return account(conn, customer_id)


def account(conn: sqlite3.Connection, customer_id: int, limit: int = 30) -> Dict[str, Any]:
    entries = [
        {**dict(r), "label": KIND_LABEL.get(r["kind"], r["kind"])}
        for r in conn.execute(
            "SELECT kind, points, description, created_at FROM ws_point_entries WHERE customer_id = ? ORDER BY id DESC LIMIT ?",
            (customer_id, limit),
        )
    ]
    redemptions = [
        {**dict(r), "status_label": STATUS_LABEL[r["status"]]}
        for r in conn.execute(
            """
            SELECT d.id, d.code, d.points, d.status, d.created_at, d.used_at, w.name AS reward_name
            FROM ws_redemptions d JOIN ws_rewards w ON w.id = d.reward_id
            WHERE d.customer_id = ? ORDER BY d.id DESC LIMIT 20
            """,
            (customer_id,),
        )
    ]
    return {"balance": balance(conn, customer_id), "entries": entries, "redemptions": redemptions}


# ------------------------------------------------------------------- rewards


def _reward_row(r: sqlite3.Row) -> Dict[str, Any]:
    item = dict(r)
    item["is_active"] = bool(item["is_active"])
    return item


def list_rewards(conn: sqlite3.Connection, active_only: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM ws_rewards" + (" WHERE is_active = 1 AND (stock IS NULL OR stock > 0)" if active_only else "") + " ORDER BY is_active DESC, cost_points, id"
    return [_reward_row(r) for r in conn.execute(sql)]


def save_reward(conn: sqlite3.Connection, data: Dict[str, Any], reward_id: Optional[int] = None) -> Dict[str, Any]:
    current = None
    if reward_id:
        current = conn.execute("SELECT * FROM ws_rewards WHERE id = ?", (reward_id,)).fetchone()
        if not current:
            raise WholesaleError("المكافأة مش موجودة.", 404)
    values = dict(current) if current else {"name": "", "description": "", "cost_points": 0, "stock": None, "is_active": 1}
    if data.get("name") is not None:
        values["name"] = clean_text(data["name"], 80)
    if data.get("description") is not None:
        values["description"] = clean_text(data["description"], 300)
    if data.get("cost_points") is not None:
        values["cost_points"] = int(data["cost_points"])
    if "stock" in data:
        values["stock"] = None if data["stock"] in (None, "") else max(0, int(data["stock"]))
    if data.get("is_active") is not None:
        values["is_active"] = 1 if data["is_active"] else 0
    if len(values["name"]) < 2:
        raise WholesaleError("اكتب اسم المكافأة.")
    if values["cost_points"] < 1 or values["cost_points"] > 1_000_000:
        raise WholesaleError("عدد النقاط لازم يكون رقم موجب.")
    now = utc_now()
    if current:
        conn.execute(
            "UPDATE ws_rewards SET name = ?, description = ?, cost_points = ?, stock = ?, is_active = ?, updated_at = ? WHERE id = ?",
            (values["name"], values["description"], values["cost_points"], values["stock"], values["is_active"], now, reward_id),
        )
    else:
        reward_id = conn.execute(
            "INSERT INTO ws_rewards(name, description, cost_points, stock, is_active, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (values["name"], values["description"], values["cost_points"], values["stock"], values["is_active"], now, now),
        ).lastrowid
    return _reward_row(conn.execute("SELECT * FROM ws_rewards WHERE id = ?", (reward_id,)).fetchone())


def redeem(conn: sqlite3.Connection, customer_id: int, reward_id: int) -> Dict[str, Any]:
    reward = conn.execute("SELECT * FROM ws_rewards WHERE id = ? AND is_active = 1", (reward_id,)).fetchone()
    if not reward:
        raise WholesaleError("المكافأة دي مش متاحة.", 404)
    if reward["stock"] is not None and reward["stock"] <= 0:
        raise WholesaleError("المكافأة دي خلصت.")
    if balance(conn, customer_id) < reward["cost_points"]:
        raise WholesaleError("نقاطك مش كفاية للمكافأة دي.")
    if conn.execute("SELECT COUNT(*) FROM ws_redemptions WHERE customer_id = ? AND status = 'pending'", (customer_id,)).fetchone()[0] >= 5:
        raise WholesaleError("عندك أكواد كتير لسه ما اتستخدمتش.")
    while True:
        code = f"{secrets.randbelow(900000) + 100000}"
        if not conn.execute("SELECT 1 FROM ws_redemptions WHERE code = ?", (code,)).fetchone():
            break
    rid = conn.execute(
        "INSERT INTO ws_redemptions(customer_id, reward_id, points, code, status, created_at) VALUES(?, ?, ?, ?, 'pending', ?)",
        (customer_id, reward_id, reward["cost_points"], code, utc_now()),
    ).lastrowid
    _entry(conn, customer_id, "redeem", -int(reward["cost_points"]), f"استبدال: {reward['name']}", "redemption", rid)
    if reward["stock"] is not None:
        conn.execute("UPDATE ws_rewards SET stock = stock - 1 WHERE id = ?", (reward_id,))
    return {"id": rid, "code": code, "reward": reward["name"], "points": reward["cost_points"], "balance": balance(conn, customer_id)}


def admin_redemptions(conn: sqlite3.Connection, status: str = "pending", code: str = "") -> List[Dict[str, Any]]:
    sql = """
        SELECT d.*, w.name AS reward_name, c.name, c.phone, t.shop_name FROM ws_redemptions d
        JOIN ws_rewards w ON w.id = d.reward_id JOIN customers c ON c.id = d.customer_id
        LEFT JOIN ws_traders t ON t.customer_id = d.customer_id WHERE 1 = 1
    """
    params: List[Any] = []
    if status in STATUS_LABEL:
        sql += " AND d.status = ?"
        params.append(status)
    if code:
        sql += " AND d.code = ?"
        params.append(code.strip())
    return [{**dict(r), "status_label": STATUS_LABEL[r["status"]]} for r in conn.execute(sql + " ORDER BY d.id DESC LIMIT 200", params)]


def close_redemption(conn: sqlite3.Connection, redemption_id: int, used: bool, user_id: Optional[int]) -> Dict[str, Any]:
    row = conn.execute("SELECT d.*, w.name AS reward_name FROM ws_redemptions d JOIN ws_rewards w ON w.id = d.reward_id WHERE d.id = ?", (redemption_id,)).fetchone()
    if not row:
        raise WholesaleError("الكود مش موجود.", 404)
    if row["status"] != "pending":
        raise WholesaleError("الكود ده اتقفل قبل كده.")
    now = utc_now()
    conn.execute(
        "UPDATE ws_redemptions SET status = ?, used_at = ?, used_by = ? WHERE id = ?",
        ("used" if used else "cancelled", now, user_id, redemption_id),
    )
    if not used:
        _entry(conn, row["customer_id"], "refund", int(row["points"]), f"إلغاء كود {row['code']}: {row['reward_name']}", "redemption", redemption_id)
        conn.execute("UPDATE ws_rewards SET stock = stock + 1 WHERE id = ? AND stock IS NOT NULL", (row["reward_id"],))
    return {**dict(conn.execute("SELECT * FROM ws_redemptions WHERE id = ?", (redemption_id,)).fetchone()), "reward_name": row["reward_name"]}
