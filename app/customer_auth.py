"""Trader accounts for روز جملة: self signup with phone and password, sessions, password changes.

Separate from the retail Rose customer accounts. A new account also files the
trader's join request (ws_traders, status pending); the admin approves it.
Wrong passwords are counted per account and lock it for 15 minutes after 5
tries; the counter is committed before the error so the request rollback can't
undo it.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, Response

from .auth import hash_password, verify_password
from .db import connect, utc_now

SESSION_COOKIE = "rose_ws_session"
SESSION_DAYS = max(1, int(os.getenv("WHOLESALE_SESSION_DAYS", "30")))
MAX_FAILED = 5
LOCK_MINUTES = 15


class AccountError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def normalize_phone(phone: str) -> str:
    from .wholesale_picking import normalize_phone as _norm
    from .wholesale_service import WholesaleError

    try:
        return _norm(phone)
    except WholesaleError as exc:
        raise AccountError(str(exc)) from None


def _password(password: str) -> str:
    try:
        return hash_password(password)
    except ValueError as exc:
        raise AccountError(str(exc)) from None


def set_customer_password(conn: sqlite3.Connection, customer_id: int, password: str, must_change: bool = False) -> None:
    conn.execute(
        "UPDATE customers SET password_hash = ?, must_change_password = ?, failed_logins = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
        (_password(password), 1 if must_change else 0, utc_now(), int(customer_id)),
    )
    conn.execute("DELETE FROM customer_sessions WHERE customer_id = ?", (int(customer_id),))


def signup(conn: sqlite3.Connection, name: str, phone: str, password: str, shop: Dict[str, Any]) -> int:
    """New trader account plus its join request. Returns the customer id."""
    from .wholesale_orders import apply_as_trader
    from .wholesale_service import WholesaleError, clean_text

    name = clean_text(name, 80)
    if len(name) < 2:
        raise AccountError("اكتب اسمك.")
    phone = normalize_phone(phone)
    if conn.execute("SELECT 1 FROM customers WHERE phone = ?", (phone,)).fetchone():
        raise AccountError("الرقم ده متسجّل قبل كده. ادخل بكلمة السر، أو كلّمنا لو نسيتها.", 409)
    password_hash = _password(password)
    now = utc_now()
    customer_id = conn.execute(
        "INSERT INTO customers(name, phone, password_hash, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
        (name, phone, password_hash, now, now),
    ).lastrowid
    try:
        apply_as_trader(conn, customer_id, shop)
    except WholesaleError as exc:
        raise AccountError(str(exc), exc.status) from None
    return int(customer_id)


def authenticate(conn: sqlite3.Connection, phone: str, password: str) -> Dict[str, Any]:
    wrong = AccountError("رقم الموبايل أو كلمة السر غلط.", 401)
    try:
        phone = normalize_phone(phone)
    except AccountError:
        raise wrong from None
    row = conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()
    if not row or row["status"] != "active" or not row["password_hash"]:
        raise wrong
    now = _now()
    if row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > now:
        raise AccountError("محاولات كتير غلط. استنى ربع ساعة وجرّب تاني.", 429)
    if not verify_password(str(password or ""), row["password_hash"]):
        failed = row["failed_logins"] + 1
        locked = _iso(now + timedelta(minutes=LOCK_MINUTES)) if failed >= MAX_FAILED else None
        conn.execute("UPDATE customers SET failed_logins = ?, locked_until = ? WHERE id = ?", (0 if locked else failed, locked, row["id"]))
        conn.commit()  # keep the failed try: the error below rolls back the rest of the request
        raise wrong
    conn.execute("UPDATE customers SET failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE id = ?", (_iso(now), row["id"]))
    return dict(row)


def create_session(conn: sqlite3.Connection, customer_id: int) -> str:
    raw = secrets.token_urlsafe(32)
    now = _now()
    conn.execute("DELETE FROM customer_sessions WHERE expires_at <= ?", (_iso(now),))
    conn.execute(
        "INSERT INTO customer_sessions(customer_id, token_hash, expires_at, created_at, last_seen_at) VALUES(?, ?, ?, ?, ?)",
        (int(customer_id), hashlib.sha256(raw.encode()).hexdigest(), _iso(now + timedelta(days=SESSION_DAYS)), _iso(now), _iso(now)),
    )
    return raw


def set_session_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, raw, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=os.getenv("WHOLESALE_COOKIE_SECURE", "0") == "1", path="/",
    )


def clear_session(request: Request, response: Response) -> None:
    raw = request.cookies.get(SESSION_COOKIE, "")
    if raw:
        with connect() as conn:
            conn.execute("DELETE FROM customer_sessions WHERE token_hash = ?", (hashlib.sha256(raw.encode()).hexdigest(),))
    response.delete_cookie(SESSION_COOKIE, path="/")


def current_customer(request: Request) -> Optional[Dict[str, Any]]:
    raw = request.cookies.get(SESSION_COOKIE, "")
    if not raw:
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.name, c.phone, c.must_change_password, s.id AS session_id FROM customer_sessions s
            JOIN customers c ON c.id = s.customer_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND c.status = 'active'
            """,
            (hashlib.sha256(raw.encode()).hexdigest(), utc_now()),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE customer_sessions SET last_seen_at = ? WHERE id = ?", (utc_now(), row["session_id"]))
    return {"id": row["id"], "name": row["name"], "phone": row["phone"], "must_change_password": bool(row["must_change_password"])}


def require_customer(request: Request) -> Dict[str, Any]:
    customer = current_customer(request)
    if not customer:
        raise HTTPException(401, "سجّل دخول الأول")
    return customer


def change_password(conn: sqlite3.Connection, customer_id: int, current: str, new: str) -> None:
    row = conn.execute("SELECT password_hash FROM customers WHERE id = ?", (int(customer_id),)).fetchone()
    if not row or not verify_password(str(current or ""), row["password_hash"]):
        raise AccountError("كلمة السر الحالية غلط.", 403)
    set_customer_password(conn, customer_id, new)


def admin_reset_password(conn: sqlite3.Connection, customer_id: int) -> str:
    """A temporary password the admin sends to the trader; they must change it after signing in."""
    if not conn.execute("SELECT 1 FROM customers WHERE id = ?", (int(customer_id),)).fetchone():
        raise AccountError("الحساب مش موجود.", 404)
    temp = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))
    set_customer_password(conn, customer_id, temp, must_change=True)
    return temp
