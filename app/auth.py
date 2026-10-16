"""Admin staff accounts for روز جملة (owner and managers), separate from the retail staff."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response

from .db import connect, utc_now


SESSION_COOKIE = "rose_ws_staff_session"
SESSION_HOURS = max(1, int(os.getenv("WHOLESALE_STAFF_SESSION_HOURS", "12")))
ALLOWED_ROLES = {"owner", "manager"}


def normalize_username(value: str) -> str:
    username = str(value or "").strip().casefold()
    if not 3 <= len(username) <= 40:
        raise ValueError("اسم المستخدم يجب أن يكون من 3 إلى 40 حرفًا")
    if not all(character.isalnum() or character in "._-" for character in username):
        raise ValueError("اسم المستخدم يقبل الحروف والأرقام والنقطة والشرطة فقط")
    return username


def validate_password(password: str) -> str:
    value = str(password or "")
    if len(value) < 8:
        raise ValueError("كلمة المرور يجب ألا تقل عن 8 أحرف")
    if len(value) > 128:
        raise ValueError("كلمة المرور طويلة جدًا")
    return value


def hash_password(password: str) -> str:
    password = validate_password(password)
    iterations = 310_000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, record: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = record.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations_text),
        )
        return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except (AttributeError, TypeError, ValueError):
        return False


def staff_count() -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0])


def create_staff_user(
    username: str,
    display_name: str,
    role: str,
    password: str,
) -> dict:
    username = normalize_username(username)
    display_name = str(display_name or "").strip()
    role = str(role or "").strip().lower()
    if not 2 <= len(display_name) <= 80:
        raise ValueError("اسم الموظف يجب أن يكون من حرفين إلى 80 حرفًا")
    if role not in ALLOWED_ROLES:
        raise ValueError("الصلاحية غير صحيحة")
    password_hash = hash_password(password)
    now = utc_now()
    try:
        with connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO staff_users(
                    username, display_name, role, password_hash,
                    active, created_at, updated_at
                ) VALUES(?, ?, ?, ?, 1, ?, ?)
                """,
                (username, display_name, role, password_hash, now, now),
            )
            user_id = int(cursor.lastrowid)
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise ValueError("اسم المستخدم مستخدم بالفعل") from exc
        raise
    return {
        "id": user_id,
        "username": username,
        "display_name": display_name,
        "role": role,
        "active": True,
        "created_at": now,
    }


def bootstrap_owner_from_environment() -> dict | None:
    """Create the first owner once from deployment secrets.

    Railway seeds the first owner from secret environment variables
    (WHOLESALE_OWNER_USERNAME, WHOLESALE_OWNER_DISPLAY_NAME, WHOLESALE_OWNER_PASSWORD).
    Idempotent; never logs or returns the password.
    """
    if staff_count() != 0:
        return None
    values = {
        "username": os.getenv("WHOLESALE_OWNER_USERNAME", "").strip(),
        "display_name": os.getenv(
            "WHOLESALE_OWNER_DISPLAY_NAME", ""
        ).strip(),
        "password": os.getenv("WHOLESALE_OWNER_PASSWORD", ""),
    }
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise RuntimeError(
            "Cloud owner bootstrap requires username, display name and password"
        )
    return create_staff_user(
        values["username"],
        values["display_name"],
        "owner",
        values["password"],
    )


def list_staff_users() -> list[dict]:
    with connect() as conn:
        records = conn.execute(
            """
            SELECT id, username, display_name, role, active, created_at, updated_at
            FROM staff_users ORDER BY CASE role
              WHEN 'owner' THEN 1 WHEN 'manager' THEN 2 ELSE 3 END, id
            """
        ).fetchall()
    return [{**dict(row), "active": bool(row["active"])} for row in records]


def list_staff_audit(limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit), 500))
    with connect() as conn:
        records = conn.execute(
            """
            SELECT a.id, a.action, a.details, a.ip_address, a.created_at,
                   u.id AS staff_user_id, u.username,
                   COALESCE(u.display_name, 'النظام') AS display_name,
                   u.role
            FROM staff_audit_log a
            LEFT JOIN staff_users u ON u.id = a.user_id
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in records]


def authenticate_staff(username: str, password: str) -> dict | None:
    try:
        username = normalize_username(username)
    except ValueError:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM staff_users WHERE username = ? AND active = 1",
            (username,),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    result = dict(row)
    result.pop("password_hash", None)
    result["active"] = bool(result["active"])
    return result


def create_staff_session(user_id: int) -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=SESSION_HOURS)
    now_text = now.isoformat(timespec="seconds")
    expires_text = expires.isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("DELETE FROM staff_sessions WHERE expires_at <= ?", (now_text,))
        conn.execute(
            """
            INSERT INTO staff_sessions(
                user_id, token_hash, expires_at, created_at, last_seen_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (int(user_id), token_hash, expires_text, now_text, now_text),
        )
    return raw_token, expires_text


def set_staff_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        samesite="strict",
        secure=os.getenv("WHOLESALE_COOKIE_SECURE", "0") == "1",
        path="/",
    )


def clear_staff_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")


def current_staff_user(request: Request) -> dict | None:
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = utc_now()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role, u.active,
                   s.id AS session_id, s.expires_at
            FROM staff_sessions s
            JOIN staff_users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1
            """,
            (token_hash, now),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE staff_sessions SET last_seen_at = ? WHERE id = ?",
            (now, row["session_id"]),
        )
    result = dict(row)
    result.pop("session_id", None)
    result["active"] = bool(result["active"])
    return result


def require_staff(request: Request, roles: set[str] | None = None) -> dict:
    user = current_staff_user(request)
    if not user:
        raise HTTPException(401, "يجب تسجيل دخول الموظف أولًا")
    if roles and user["role"] not in roles:
        raise HTTPException(403, "ليس لديك صلاحية لتنفيذ هذا الإجراء")
    return user


def delete_staff_session(request: Request) -> None:
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    if not raw_token:
        return
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with connect() as conn:
        conn.execute("DELETE FROM staff_sessions WHERE token_hash = ?", (token_hash,))


def write_staff_audit(
    action: str,
    *,
    user_id: int | None = None,
    details: str | None = None,
    ip_address: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO staff_audit_log(
                user_id, action, details, ip_address, created_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (user_id, action, details, ip_address, utc_now()),
        )


def set_staff_password(user_id: int, password: str) -> None:
    password_hash = hash_password(password)
    with connect() as conn:
        conn.execute("UPDATE staff_users SET password_hash = ?, updated_at = ? WHERE id = ?", (password_hash, utc_now(), int(user_id)))
        conn.execute("DELETE FROM staff_sessions WHERE user_id = ?", (int(user_id),))


def set_staff_active(user_id: int, active: bool) -> None:
    with connect() as conn:
        owners = conn.execute("SELECT COUNT(*) FROM staff_users WHERE role = 'owner' AND active = 1 AND id != ?", (int(user_id),)).fetchone()[0]
        row = conn.execute("SELECT role FROM staff_users WHERE id = ?", (int(user_id),)).fetchone()
        if not row:
            raise ValueError("الحساب مش موجود")
        if not active and row["role"] == "owner" and owners == 0:
            raise ValueError("لازم يفضل فيه مالك واحد على الأقل")
        conn.execute("UPDATE staff_users SET active = ?, updated_at = ? WHERE id = ?", (1 if active else 0, utc_now(), int(user_id)))
        if not active:
            conn.execute("DELETE FROM staff_sessions WHERE user_id = ?", (int(user_id),))
