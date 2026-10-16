"""Routes for trader team accounts: the manager's team screens and the employee screen on /wholesale/team/."""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import wholesale_notebook as wn
from . import wholesale_service as ws
from . import wholesale_team as wt
from .wholesale_schema import ws_connect
from .wholesale_api import _fail
from .wholesale_orders_api import STATIC, _trader

router = APIRouter()
_ip_failures: Dict[str, List[float]] = {}


class StaffIn(BaseModel):
    name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=30)
    can_see_prices: Optional[bool] = None
    can_send_direct: Optional[bool] = None
    can_use_notebook: Optional[bool] = None
    direct_method: Optional[str] = Field(default=None, max_length=10)
    is_active: Optional[bool] = None


class ChangeIn(BaseModel):
    item_id: int
    qty: Optional[int] = None
    remove: bool = False


class DecideIn(BaseModel):
    approve: bool
    changes: List[ChangeIn] = Field(default_factory=list, max_length=300)
    note: str = Field(default="", max_length=300)


class TeamLoginIn(BaseModel):
    phone: str = Field(max_length=30)
    pin: str = Field(max_length=10)


class TeamLineIn(BaseModel):
    product_id: int
    unit: str = Field(max_length=10)
    qty: int = Field(ge=1, le=9999)
    note: str = Field(default="", max_length=200)


class TeamRequestIn(BaseModel):
    items: List[TeamLineIn] = Field(min_length=1, max_length=300)
    note: str = Field(default="", max_length=300)


class TeamSyncIn(BaseModel):
    ops: List[Dict] = Field(default_factory=list, max_length=200)


def _dump(model) -> Dict:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _call(fn, *args):
    try:
        with ws_connect() as conn:
            return fn(conn, *args)
    except ws.WholesaleError as exc:
        raise _fail(exc)


# ---------------------------------------------------------- manager screens


@router.get("/api/wholesale/team/staff")
def team_staff(request: Request):
    return _call(wt.list_staff, _trader(request))


@router.post("/api/wholesale/team/staff")
def team_add_staff(payload: StaffIn, request: Request):
    data = _dump(payload)
    return _call(wt.add_staff, _trader(request), data["name"], data["phone"], data)


@router.patch("/api/wholesale/team/staff/{staff_id}")
def team_update_staff(staff_id: int, payload: StaffIn, request: Request):
    return _call(wt.update_staff, _trader(request), staff_id, _dump(payload))


@router.post("/api/wholesale/team/staff/{staff_id}/pin")
def team_reset_pin(staff_id: int, request: Request):
    return _call(wt.reset_pin, _trader(request), staff_id)


@router.get("/api/wholesale/team/requests")
def team_requests(request: Request, status: str = "pending"):
    return _call(wt.owner_requests, _trader(request), status[:10])


@router.post("/api/wholesale/team/requests/{req_id}/decide")
def team_decide(req_id: int, payload: DecideIn, request: Request):
    return _call(wt.decide_request, _trader(request), req_id, payload.approve, [_dump(c) for c in payload.changes], payload.note)


# ----------------------------------------------------------- employee screen


def _employee(request: Request) -> Dict:
    with ws_connect() as conn:
        staff = wt.current_staff(conn, request.cookies.get(wt.SESSION_COOKIE, ""))
    if not staff:
        raise HTTPException(401, "سجّل دخول الأول.")
    return staff


@router.get("/wholesale/team/", include_in_schema=False)
def team_page():
    return FileResponse(STATIC / "team" / "index.html")


@router.get("/wholesale/team", include_in_schema=False)
def team_page_redirect():
    return Response(status_code=307, headers={"Location": "/wholesale/team/"})


@router.post("/api/team/login")
def team_login(payload: TeamLoginIn, request: Request, response: Response):
    host = request.client.host if getattr(request, "client", None) else "?"
    now = time.time()
    recent = [t for t in _ip_failures.get(host, []) if now - t < 600]
    if len(recent) >= 20:
        raise HTTPException(429, "محاولات كتير. استنى شوية وجرّب تاني.")
    try:
        with ws_connect() as conn:
            token = wt.login(conn, payload.phone, payload.pin)
    except ws.WholesaleError as exc:
        recent.append(now)
        _ip_failures[host] = recent
        raise _fail(exc)
    _ip_failures.pop(host, None)
    response.set_cookie(wt.SESSION_COOKIE, token, max_age=wt.SESSION_HOURS * 3600, httponly=True, samesite="lax",
                        secure=os.getenv("WHOLESALE_COOKIE_SECURE", "0") == "1", path="/")
    return {"ok": True}


@router.post("/api/team/logout")
def team_logout(request: Request, response: Response):
    with ws_connect() as conn:
        wt.logout(conn, request.cookies.get(wt.SESSION_COOKIE, ""))
    response.delete_cookie(wt.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/team/me")
def team_me(request: Request):
    with ws_connect() as conn:
        staff = wt.current_staff(conn, request.cookies.get(wt.SESSION_COOKIE, ""))
        if not staff:
            return {"logged_in": False}
        shop = conn.execute("SELECT shop_name FROM ws_traders WHERE customer_id = ?", (staff["owner_id"],)).fetchone()
        from .wholesale_orders import trader_sections

        return {
            "logged_in": True,
            **{k: staff[k] for k in ("name", "can_see_prices", "can_send_direct", "can_use_notebook", "direct_method")},
            "shop_name": shop["shop_name"] if shop else "",
            "sections": trader_sections(conn),
            "unread": wt.staff_notices(conn, staff["id"])["unread"],
        }


@router.get("/api/team/catalog")
def team_catalog(request: Request, q: str = "", section_id: Optional[int] = None, page: int = 1):
    staff = _employee(request)
    return _call(wt.staff_catalog, staff, q[:100], section_id, page)


@router.get("/api/team/requests")
def team_my_requests(request: Request):
    return _call(wt.staff_requests, _employee(request))


@router.post("/api/team/requests")
def team_send(payload: TeamRequestIn, request: Request):
    staff = _employee(request)
    return _call(wt.submit_request, staff, [_dump(i) for i in payload.items], payload.note)


@router.get("/api/team/notices")
def team_notices(request: Request):
    staff = _employee(request)
    return _call(wt.staff_notices, staff["id"], True)


def _notebook_view(staff: Dict, data: Dict) -> Dict:
    items = [{k: v for k, v in i.items() if k not in ("product",) and (staff["can_see_prices"] or k not in ("price", "units"))} for i in data["items"]]
    return {"items": items}


@router.get("/api/team/notebook")
def team_notebook(request: Request):
    staff = _employee(request)
    if not staff["can_use_notebook"]:
        raise HTTPException(403, "المدير ماسمحلكش تستخدم الدفتر.")
    with ws_connect() as conn:
        return _notebook_view(staff, wn.notebook(conn, staff["owner_id"]))


@router.post("/api/team/notebook/sync")
def team_notebook_sync(payload: TeamSyncIn, request: Request):
    staff = _employee(request)
    if not staff["can_use_notebook"]:
        raise HTTPException(403, "المدير ماسمحلكش تستخدم الدفتر.")
    ops = [o for o in payload.ops if isinstance(o, dict) and o.get("op") in ("add", "update", "remove")]
    for o in ops:
        o.pop("voice", None)
        if o.get("op") == "add" and o.get("note") is None:
            o["note"] = f"سجّله {staff['name']}"
    try:
        with ws_connect() as conn:
            return _notebook_view(staff, wn.sync(conn, staff["owner_id"], ops))
    except ws.WholesaleError as exc:
        raise _fail(exc)
