"""Routes for wholesale phase 9: the shortages notebook, its reminder, voice notes, and offline support."""

from __future__ import annotations

import base64
import binascii
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import wholesale_notebook as wn
from . import wholesale_service as ws
from .wholesale_schema import ws_connect
from .wholesale_api import _fail
from .wholesale_orders_api import STATIC, _trader

router = APIRouter()

VOICE_TYPES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/aac": "aac", "audio/mpeg": "mp3", "audio/x-m4a": "m4a"}
VOICE_MEDIA = {v: k for k, v in VOICE_TYPES.items()}
MAX_VOICE = 1_500_000  # about a minute of compressed speech


class NotebookOp(BaseModel):
    op: str = Field(max_length=10)
    client_id: str = Field(max_length=64)
    name: Optional[str] = Field(default=None, max_length=200)
    product_id: Optional[int] = None
    unit: Optional[str] = Field(default=None, max_length=10)
    qty: Optional[int] = None
    note: Optional[str] = Field(default=None, max_length=300)
    voice: Optional[str] = Field(default=None, max_length=2_100_000)


class SyncIn(BaseModel):
    ops: List[NotebookOp] = Field(default_factory=list, max_length=500)


class ReminderIn(BaseModel):
    day: Optional[int] = None
    hour: int = 10


def _voice_dir() -> Path:
    from .db import db_path

    path = db_path().parent / "wholesale_voice"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_voice(customer_id: int, data_url: str) -> str:
    head, _, raw = data_url.partition(",")
    media = head[5:].split(";")[0].strip().lower() if head.startswith("data:") else ""
    ext = VOICE_TYPES.get(media)
    if not ext:
        raise ws.WholesaleError("نوع التسجيل مش مدعوم.")
    try:
        content = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ws.WholesaleError("التسجيل مش سليم.") from None
    if not content or len(content) > MAX_VOICE:
        raise ws.WholesaleError("التسجيل طويل أوي. خليه أقل من دقيقة.")
    name = f"c{customer_id}-{secrets.token_hex(8)}.{ext}"
    (_voice_dir() / name).write_bytes(content)
    return name


@router.get("/api/wholesale/notebook")
def trader_notebook(request: Request):
    customer_id = _trader(request)
    with ws_connect() as conn:
        return wn.notebook(conn, customer_id)


@router.get("/api/wholesale/notebook/index")
def trader_notebook_index(request: Request):
    """Names and prices to search the catalog while offline."""
    customer_id = _trader(request)
    with ws_connect() as conn:
        return {"items": wn.catalog_index(conn, customer_id)}


@router.post("/api/wholesale/notebook/sync")
def trader_notebook_sync(payload: SyncIn, request: Request):
    customer_id = _trader(request)
    ops: List[Dict[str, Any]] = [op.model_dump() if hasattr(op, "model_dump") else op.dict() for op in payload.ops]
    try:
        with ws_connect() as conn:
            return wn.sync(conn, customer_id, ops, _save_voice)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/wholesale/notebook/items/{item_id}/voice")
def trader_notebook_voice(item_id: int, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            name = wn.voice_path(conn, customer_id, item_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    path = _voice_dir() / name
    if not path.is_file():
        raise HTTPException(404, "التسجيل مش موجود.")
    return FileResponse(path, media_type=VOICE_MEDIA.get(path.suffix[1:], "audio/webm"), headers={"Cache-Control": "private, max-age=86400"})


@router.put("/api/wholesale/notebook/reminder")
def trader_notebook_reminder(payload: ReminderIn, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            return wn.set_reminder(conn, customer_id, payload.day, payload.hour)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/wholesale/notebook/reminder.ics")
def trader_notebook_ics(request: Request):
    customer_id = _trader(request)
    base = str(request.base_url).rstrip("/") if hasattr(request, "base_url") else ""
    try:
        with ws_connect() as conn:
            text = wn.reminder_ics(conn, customer_id, f"{base}/wholesale/#shortages")
    except ws.WholesaleError as exc:
        raise _fail(exc)
    return Response(
        content=text.encode("utf-8"),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="rose-notebook-reminder.ics"'},
    )


@router.get("/wholesale/sw.js", include_in_schema=False)
def trader_service_worker():
    """Served from /wholesale/ so it can keep the trader page working offline."""
    return FileResponse(
        STATIC / "trader" / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/wholesale/"},
    )
