"""Routes for wholesale phase 5: the prep employee's phone screen and the admin employees tab.

Employee (phone + 4-digit code, own session cookie):
    /pick/                       the mobile screen
    /api/pick/...                login, queue, picking, receiving from suppliers
Admin (owner / manager):
    /api/admin/wholesale/pickers ...
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import wholesale_picking as wk
from . import wholesale_service as ws
from .wholesale_schema import ws_connect
from .wholesale_api import _admin, _audit, _fail

STATIC = Path(__file__).resolve().parent / "static" / "wholesale"
router = APIRouter()
_ip_failures: Dict[str, list] = {}


class PickLoginIn(BaseModel):
    phone: str = Field(min_length=6, max_length=30)
    pin: str = Field(min_length=4, max_length=8)


class PickItemIn(BaseModel):
    status: str = Field(max_length=10)
    qty: Optional[int] = None


class ReceiveIn(BaseModel):
    received: Dict[str, int] = Field(default_factory=dict)


class PickerIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    phone: str = Field(min_length=6, max_length=30)
    role: str = Field(default="picker", max_length=10)


class RoleIn(BaseModel):
    role: str = Field(max_length=10)


class ActiveIn(BaseModel):
    active: bool


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _staff(request: Request, roles=("picker", "both")) -> int:
    token = request.cookies.get(wk.SESSION_COOKIE, "")
    with ws_connect() as conn:
        picker = wk.current_picker(conn, token)
    if not picker:
        raise HTTPException(401, "سجّل دخول الأول")
    if picker["role"] not in roles:
        raise HTTPException(403, "الصفحة دي مش لدورك.")
    return int(picker["id"])


def _picker_id(request: Request) -> int:
    return _staff(request, ("picker", "both"))


# ---------------------------------------------------------------- employee


@router.get("/pick/", include_in_schema=False)
def pick_page():
    return FileResponse(STATIC / "pick" / "index.html")


@router.get("/pick", include_in_schema=False)
def pick_redirect():
    return Response(status_code=307, headers={"Location": "/pick/"})


@router.post("/api/pick/login")
def pick_login(payload: PickLoginIn, request: Request, response: Response):
    host = _client(request)
    now = time.time()
    recent = [t for t in _ip_failures.get(host, []) if now - t < 600]
    if len(recent) >= 20:
        raise HTTPException(429, "محاولات كتير. استنى شوية وجرّب تاني.")
    try:
        with ws_connect() as conn:
            token = wk.login(conn, payload.phone, payload.pin)
    except ws.WholesaleError as exc:
        recent.append(now)
        _ip_failures[host] = recent
        raise _fail(exc)
    _ip_failures.pop(host, None)
    response.set_cookie(
        wk.SESSION_COOKIE,
        token,
        max_age=wk.SESSION_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=os.getenv("WHOLESALE_COOKIE_SECURE", "0") == "1",
        path="/",
    )
    return {"ok": True}


@router.post("/api/pick/logout")
def pick_logout(request: Request, response: Response):
    with ws_connect() as conn:
        wk.logout(conn, request.cookies.get(wk.SESSION_COOKIE, ""))
    response.delete_cookie(wk.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/pick/me")
def pick_me(request: Request):
    with ws_connect() as conn:
        picker = wk.current_picker(conn, request.cookies.get(wk.SESSION_COOKIE, ""))
    return {"logged_in": bool(picker), **({"name": picker["name"], "role": picker["role"]} if picker else {})}


@router.get("/api/pick/queue")
def pick_queue(request: Request):
    pid = _picker_id(request)
    with ws_connect() as conn:
        return wk.queue(conn, pid)


@router.get("/api/pick/orders/{order_id}")
def pick_order(order_id: int, request: Request):
    pid = _picker_id(request)
    try:
        with ws_connect() as conn:
            return wk.pick_view(conn, order_id, pid)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/pick/orders/{order_id}/start")
def pick_start(order_id: int, request: Request):
    pid = _picker_id(request)
    try:
        with ws_connect() as conn:
            return wk.start_pick(conn, order_id, pid)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/pick/orders/{order_id}/items/{item_id}")
def pick_item(order_id: int, item_id: int, payload: PickItemIn, request: Request):
    pid = _picker_id(request)
    try:
        with ws_connect() as conn:
            return wk.mark_item(conn, order_id, item_id, pid, payload.status, payload.qty)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/pick/orders/{order_id}/finish")
def pick_finish(order_id: int, request: Request):
    pid = _picker_id(request)
    try:
        with ws_connect() as conn:
            return wk.finish_pick(conn, order_id, pid)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/pick/receiving")
def pick_receiving(request: Request):
    _picker_id(request)
    with ws_connect() as conn:
        return wk.receiving_list(conn)


@router.post("/api/pick/receiving/{po_id}")
def pick_receive(po_id: int, payload: ReceiveIn, request: Request):
    pid = _picker_id(request)
    try:
        with ws_connect() as conn:
            return wk.receive_from_phone(conn, po_id, payload.received, pid)
    except ws.WholesaleError as exc:
        raise _fail(exc)


# ------------------------------------------------------------------- admin


@router.get("/api/admin/wholesale/pickers")
def admin_pickers(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wk.list_pickers(conn)


@router.post("/api/admin/wholesale/pickers")
def admin_create_picker(payload: PickerIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wk.create_picker(conn, payload.name, payload.phone, payload.role)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "picker_create", f"id={result['id']}")
    return result


@router.post("/api/admin/wholesale/pickers/{picker_id}/reset-pin")
def admin_reset_pin(picker_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wk.reset_pin(conn, picker_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "picker_reset_pin", f"id={picker_id}")
    return result


@router.post("/api/admin/wholesale/pickers/{picker_id}/active")
def admin_picker_active(picker_id: int, payload: ActiveIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wk.set_active(conn, picker_id, payload.active)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "picker_" + ("on" if payload.active else "off"), f"id={picker_id}")
    return result


@router.post("/api/admin/wholesale/orders/{order_id}/release-pick")
def admin_release_pick(order_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            wk.release_pick(conn, order_id)
            from . import wholesale_orders as wo

            result = wo.admin_order(conn, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "pick_release", f"order={order_id}")
    return result


@router.post("/api/admin/wholesale/pickers/{picker_id}/role")
def admin_picker_role(picker_id: int, payload: RoleIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wk.set_role(conn, picker_id, payload.role)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "picker_role", f"id={picker_id} role={payload.role}")
    return result
