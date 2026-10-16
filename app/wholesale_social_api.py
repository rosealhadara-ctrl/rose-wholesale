"""Routes for wholesale phase 10: chats between traders, carton splits, nearby traders, and chat moderation."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from . import wholesale_service as ws
from . import wholesale_social as wsoc
from .wholesale_schema import ws_connect
from .wholesale_api import _admin, _audit, _fail
from .wholesale_orders_api import _trader

router = APIRouter()


class ProfileIn(BaseModel):
    visible: Optional[bool] = None
    show_phone: Optional[bool] = None
    bio: Optional[str] = Field(default=None, max_length=200)


class StartIn(BaseModel):
    customer_id: int


class MessageIn(BaseModel):
    body: str = Field(max_length=1200)


class BlockIn(BaseModel):
    on: bool = True


class ReportIn(BaseModel):
    message_id: Optional[int] = None
    reason: str = Field(max_length=300)


class SplitIn(BaseModel):
    product_id: int
    pieces: int = Field(ge=1, le=10000)
    note: str = Field(default="", max_length=200)


class JoinIn(BaseModel):
    pieces: int = Field(ge=1, le=10000)


class CloseReportIn(BaseModel):
    note: str = Field(default="", max_length=300)


class SuspendIn(BaseModel):
    on: bool


def _call(fn, *args):
    try:
        with ws_connect() as conn:
            return fn(conn, *args)
    except ws.WholesaleError as exc:
        raise _fail(exc)


# ---------------------------------------------------------------- trader


@router.get("/api/wholesale/nearby")
def trader_nearby(request: Request):
    return _call(wsoc.nearby, _trader(request))


@router.put("/api/wholesale/social-profile")
def trader_profile(payload: ProfileIn, request: Request):
    return _call(wsoc.set_profile, _trader(request), payload.visible, payload.show_phone, payload.bio)


@router.get("/api/wholesale/chats")
def trader_chats(request: Request):
    me = _trader(request)
    with ws_connect() as conn:
        return {"threads": wsoc.threads(conn, me), "profile": wsoc.profile(conn, me)}


@router.post("/api/wholesale/chats")
def trader_start_chat(payload: StartIn, request: Request):
    return {"id": _call(wsoc.start_thread, _trader(request), payload.customer_id)}


@router.get("/api/wholesale/chats/{thread_id}")
def trader_chat(thread_id: int, request: Request, after: int = 0):
    return _call(wsoc.messages, _trader(request), thread_id, max(0, after))


@router.post("/api/wholesale/chats/{thread_id}/messages")
def trader_send(thread_id: int, payload: MessageIn, request: Request):
    return _call(wsoc.send, _trader(request), thread_id, payload.body)


@router.post("/api/wholesale/chats/{thread_id}/block")
def trader_block(thread_id: int, payload: BlockIn, request: Request):
    return _call(wsoc.block, _trader(request), thread_id, payload.on)


@router.post("/api/wholesale/chats/{thread_id}/report")
def trader_report(thread_id: int, payload: ReportIn, request: Request):
    _call(wsoc.report, _trader(request), thread_id, payload.message_id, payload.reason)
    return {"ok": True}


@router.post("/api/wholesale/splits")
def trader_create_split(payload: SplitIn, request: Request):
    return _call(wsoc.create_split, _trader(request), payload.product_id, payload.pieces, payload.note)


@router.post("/api/wholesale/splits/{split_id}/join")
def trader_join_split(split_id: int, payload: JoinIn, request: Request):
    return _call(wsoc.join_split, _trader(request), split_id, payload.pieces)


@router.post("/api/wholesale/splits/{split_id}/leave")
def trader_leave_split(split_id: int, request: Request):
    return _call(wsoc.leave_split, _trader(request), split_id)


@router.post("/api/wholesale/splits/{split_id}/cancel")
def trader_cancel_split(split_id: int, request: Request):
    return _call(wsoc.cancel_split, _trader(request), split_id)


@router.post("/api/wholesale/splits/{split_id}/order")
def trader_order_split(split_id: int, request: Request):
    return _call(wsoc.order_split, _trader(request), split_id)


# ----------------------------------------------------------------- admin


@router.get("/api/admin/wholesale/chat-reports")
def admin_chat_reports(request: Request, status: str = "open"):
    _admin(request)
    return _call(wsoc.admin_reports, status[:10] if status in ("open", "closed", "") else "open")


@router.post("/api/admin/wholesale/chat-reports/{report_id}/close")
def admin_close_chat_report(report_id: int, payload: CloseReportIn, request: Request):
    user = _admin(request)
    _call(wsoc.close_report, report_id, payload.note)
    _audit(request, user, "chat_report_close", f"report={report_id}")
    return {"ok": True}


@router.post("/api/admin/wholesale/chat-suspend/{customer_id}")
def admin_chat_suspend(customer_id: int, payload: SuspendIn, request: Request):
    user = _admin(request)
    _call(wsoc.suspend_chat, customer_id, payload.on)
    _audit(request, user, "chat_suspend" if payload.on else "chat_unsuspend", f"customer={customer_id}")
    return {"ok": True}
