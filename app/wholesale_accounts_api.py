"""Routes for accounts in the standalone wholesale app.

Traders:  signup, login, logout, change password; wholesale points and rewards.
Admin:    staff login/logout; staff accounts (owner only); trader password reset;
          rewards, redemption codes and point adjustments.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import auth
from . import customer_auth as ca
from . import wholesale_points as wpts
from . import wholesale_service as ws
from .db import connect
from .wholesale_api import _admin, _audit, _fail
from .wholesale_schema import ws_connect

router = APIRouter()
_ip_failures: Dict[str, List[float]] = {}


def _client(request: Request) -> str:
    return request.client.host if getattr(request, "client", None) else "?"


def _throttle(request: Request) -> None:
    now = time.time()
    recent = [t for t in _ip_failures.get(_client(request), []) if now - t < 600]
    _ip_failures[_client(request)] = recent
    if len(recent) >= 20:
        raise HTTPException(429, "محاولات كتير. استنى شوية وجرّب تاني.")


def _failed(request: Request) -> None:
    _ip_failures.setdefault(_client(request), []).append(time.time())


def _account_fail(exc: ca.AccountError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


class SignupIn(BaseModel):
    name: str = Field(max_length=80)
    phone: str = Field(max_length=30)
    password: str = Field(max_length=128)
    shop_name: str = Field(max_length=120)
    area: str = Field(max_length=120)
    address: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=400)


class LoginIn(BaseModel):
    phone: str = Field(max_length=30)
    password: str = Field(max_length=128)


class PasswordIn(BaseModel):
    current: str = Field(max_length=128)
    new: str = Field(max_length=128)


class StaffLoginIn(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=128)


class StaffIn(BaseModel):
    username: str = Field(default="", max_length=40)
    display_name: str = Field(default="", max_length=80)
    role: str = Field(default="manager", max_length=10)
    password: str = Field(default="", max_length=128)
    active: Optional[bool] = None


class RewardIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=300)
    cost_points: Optional[int] = None
    stock: Optional[int] = None
    is_active: Optional[bool] = None


class RedeemIn(BaseModel):
    reward_id: int


class CloseCodeIn(BaseModel):
    used: bool


class AdjustIn(BaseModel):
    points: int = Field(ge=-1_000_000, le=1_000_000)
    note: str = Field(max_length=150)


# ------------------------------------------------------------ trader account


@router.post("/api/wholesale/auth/signup")
def trader_signup(payload: SignupIn, request: Request, response: Response):
    _throttle(request)
    shop = {"shop_name": payload.shop_name, "area": payload.area, "address": payload.address, "note": payload.note}
    try:
        with connect() as conn:
            customer_id = ca.signup(conn, payload.name, payload.phone, payload.password, shop)
            raw = ca.create_session(conn, customer_id)
    except ca.AccountError as exc:
        _failed(request)
        raise _account_fail(exc)
    ca.set_session_cookie(response, raw)
    return {"ok": True}


@router.post("/api/wholesale/auth/login")
def trader_login(payload: LoginIn, request: Request, response: Response):
    _throttle(request)
    try:
        with connect() as conn:
            customer = ca.authenticate(conn, payload.phone, payload.password)
            raw = ca.create_session(conn, customer["id"])
    except ca.AccountError as exc:
        _failed(request)
        raise _account_fail(exc)
    ca.set_session_cookie(response, raw)
    return {"ok": True, "must_change_password": bool(customer["must_change_password"])}


@router.post("/api/wholesale/auth/logout")
def trader_logout(request: Request, response: Response):
    ca.clear_session(request, response)
    return {"ok": True}


@router.post("/api/wholesale/auth/password")
def trader_password(payload: PasswordIn, request: Request, response: Response):
    customer = ca.require_customer(request)
    try:
        with connect() as conn:
            ca.change_password(conn, customer["id"], payload.current, payload.new)
            raw = ca.create_session(conn, customer["id"])
    except ca.AccountError as exc:
        raise _account_fail(exc)
    ca.set_session_cookie(response, raw)
    return {"ok": True}


# ---------------------------------------------------------- trader points


@router.get("/api/wholesale/points")
def trader_points(request: Request):
    customer = ca.require_customer(request)
    with ws_connect() as conn:
        return {**wpts.account(conn, customer["id"]), "rewards": wpts.list_rewards(conn, active_only=True)}


@router.post("/api/wholesale/points/redeem")
def trader_redeem(payload: RedeemIn, request: Request):
    customer = ca.require_customer(request)
    try:
        with ws_connect() as conn:
            from .wholesale_orders import require_trader

            require_trader(conn, customer["id"])
            return wpts.redeem(conn, customer["id"], payload.reward_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


# --------------------------------------------------------------- staff login


@router.post("/api/staff/login")
def staff_login(payload: StaffLoginIn, request: Request, response: Response):
    _throttle(request)
    user = auth.authenticate_staff(payload.username, payload.password)
    if not user:
        _failed(request)
        raise HTTPException(401, "اسم المستخدم أو كلمة السر غلط.")
    raw, _ = auth.create_staff_session(user["id"])
    auth.set_staff_cookie(response, raw)
    auth.write_staff_audit("login", user_id=user["id"], ip_address=_client(request))
    return {"ok": True, "display_name": user["display_name"], "role": user["role"]}


@router.post("/api/staff/logout")
def staff_logout(request: Request, response: Response):
    auth.delete_staff_session(request)
    auth.clear_staff_cookie(response)
    return {"ok": True}


@router.get("/api/staff/me")
def staff_me(request: Request):
    user = auth.current_staff_user(request)
    return {"logged_in": bool(user), **({k: user[k] for k in ("username", "display_name", "role")} if user else {})}


# ------------------------------------------------------------- admin: staff


def _owner(request: Request) -> Dict:
    return auth.require_staff(request, {"owner"})


@router.get("/api/admin/wholesale/staff")
def admin_staff(request: Request):
    _owner(request)
    return auth.list_staff_users()


@router.post("/api/admin/wholesale/staff")
def admin_add_staff(payload: StaffIn, request: Request):
    user = _owner(request)
    try:
        created = auth.create_staff_user(payload.username, payload.display_name, payload.role, payload.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, user, "staff_add", f"user={created['id']} role={created['role']}")
    return created


@router.patch("/api/admin/wholesale/staff/{user_id}")
def admin_update_staff(user_id: int, payload: StaffIn, request: Request):
    user = _owner(request)
    try:
        if payload.password:
            auth.set_staff_password(user_id, payload.password)
        if payload.active is not None:
            auth.set_staff_active(user_id, payload.active)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, user, "staff_update", f"user={user_id}")
    return auth.list_staff_users()


# ------------------------------------------------- admin: traders and points


@router.post("/api/admin/wholesale/trader-accounts/{customer_id}/password-reset")
def admin_trader_password_reset(customer_id: int, request: Request):
    user = _admin(request)
    try:
        with connect() as conn:
            temp = ca.admin_reset_password(conn, customer_id)
    except ca.AccountError as exc:
        raise _account_fail(exc)
    _audit(request, user, "trader_password_reset", f"customer={customer_id}")
    return {"temporary_password": temp}


@router.get("/api/admin/wholesale/rewards")
def admin_rewards(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wpts.list_rewards(conn)


@router.post("/api/admin/wholesale/rewards")
def admin_add_reward(payload: RewardIn, request: Request):
    user = _admin(request)
    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    try:
        with ws_connect() as conn:
            result = wpts.save_reward(conn, data)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "reward_add", f"reward={result['id']}")
    return result


@router.patch("/api/admin/wholesale/rewards/{reward_id}")
def admin_update_reward(reward_id: int, payload: RewardIn, request: Request):
    user = _admin(request)
    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    try:
        with ws_connect() as conn:
            result = wpts.save_reward(conn, data, reward_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "reward_update", f"reward={reward_id}")
    return result


@router.get("/api/admin/wholesale/redemptions")
def admin_redemptions(request: Request, status: str = "pending", code: str = ""):
    _admin(request)
    with ws_connect() as conn:
        return wpts.admin_redemptions(conn, status[:10], code[:10])


@router.post("/api/admin/wholesale/redemptions/{redemption_id}")
def admin_close_redemption(redemption_id: int, payload: CloseCodeIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wpts.close_redemption(conn, redemption_id, payload.used, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "redemption_" + ("used" if payload.used else "cancel"), f"redemption={redemption_id}")
    return result


@router.get("/api/admin/wholesale/trader-accounts/{customer_id}/points")
def admin_trader_points(customer_id: int, request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wpts.account(conn, customer_id, limit=100)


@router.post("/api/admin/wholesale/trader-accounts/{customer_id}/points")
def admin_adjust_points(customer_id: int, payload: AdjustIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wpts.adjust(conn, customer_id, payload.points, payload.note)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "points_adjust", f"customer={customer_id} points={payload.points}")
    return result
