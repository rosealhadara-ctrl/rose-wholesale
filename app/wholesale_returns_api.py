"""Routes for wholesale phase 7: returns, ratings, complaints and suggestions."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import wholesale_returns as wr
from . import wholesale_service as ws
from .customer_auth import require_customer
from .wholesale_schema import ws_connect
from .wholesale_api import _admin, _audit, _fail
from .wholesale_orders_api import _trader
from .wholesale_picking_api import _staff

router = APIRouter()


class ReturnLineIn(BaseModel):
    item_id: int
    qty: int = Field(ge=0, le=9999)
    reason: str = Field(max_length=20)


class ReturnIn(BaseModel):
    items: List[ReturnLineIn] = Field(min_length=1, max_length=300)
    note: str = Field(default="", max_length=400)
    photo: Optional[str] = Field(default=None, max_length=8_000_000)


class RatingIn(BaseModel):
    stars: int = Field(ge=1, le=5)
    liked: List[str] = Field(default_factory=list, max_length=10)
    improve: str = Field(default="", max_length=600)


class FeedbackIn(BaseModel):
    kind: str = Field(max_length=20)
    text: str = Field(min_length=3, max_length=1000)
    order_id: Optional[int] = None


class InspectIn(BaseModel):
    results: Dict[str, Dict[str, int]] = Field(default_factory=dict)


class DecideIn(BaseModel):
    approve: bool
    pickup: str = Field(default="driver", max_length=10)
    driver_id: Optional[int] = None
    reason: str = Field(default="", max_length=300)


class RefundIn(BaseModel):
    amount: float = Field(ge=0)


class ReplyIn(BaseModel):
    reply: Optional[str] = Field(default=None, max_length=1000)
    resolve: bool = False


def _photos_dir() -> Path:
    from .db import db_path

    path = db_path().parent / "wholesale_returns"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_photo(customer_id: int, data_url: str) -> str:
    import base64
    import binascii
    import secrets
    from io import BytesIO

    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        content = base64.b64decode(data_url.split(",", 1)[-1], validate=True)
        if len(content) > 6 * 1024 * 1024:
            raise HTTPException(413, "الصورة أكبر من 6 ميجا.")
        image = ImageOps.exif_transpose(Image.open(BytesIO(content))).convert("RGB")
    except (binascii.Error, ValueError, UnidentifiedImageError, OSError):
        raise HTTPException(400, "الصورة مش سليمة.")
    image.thumbnail((1600, 1600))
    name = f"r{customer_id}-{secrets.token_hex(8)}.jpg"
    image.save(_photos_dir() / name, "JPEG", quality=85, optimize=True)
    return name


# ------------------------------------------------------------------- trader


@router.get("/api/wholesale/orders/{order_id}/returnable")
def trader_returnable(order_id: int, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            return wr.returnable(conn, customer_id, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/wholesale/orders/{order_id}/returns")
def trader_request_return(order_id: int, payload: ReturnIn, request: Request):
    customer_id = _trader(request)
    photo = _save_photo(customer_id, payload.photo) if payload.photo else ""
    lines = [line.model_dump() if hasattr(line, "model_dump") else line.dict() for line in payload.items]
    try:
        with ws_connect() as conn:
            return wr.request_return(conn, customer_id, order_id, lines, payload.note, photo)
    except ws.WholesaleError as exc:
        if photo:
            (_photos_dir() / photo).unlink(missing_ok=True)
        raise _fail(exc)


@router.post("/api/wholesale/orders/{order_id}/rating")
def trader_rate(order_id: int, payload: RatingIn, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            return wr.rate(conn, customer_id, order_id, payload.stars, payload.liked, payload.improve)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/wholesale/feedback")
def trader_feedback(request: Request):
    customer_id = _trader(request)
    with ws_connect() as conn:
        return wr.my_feedback(conn, customer_id)


@router.post("/api/wholesale/feedback")
def trader_send_feedback(payload: FeedbackIn, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            return wr.send_feedback(conn, customer_id, payload.kind, payload.text, payload.order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


# -------------------------------------------------------------------- staff


@router.get("/api/pick/returns")
def staff_returns(request: Request):
    _staff(request, ("picker", "both"))
    with ws_connect() as conn:
        return wr.to_inspect(conn)


@router.post("/api/pick/returns/{return_id}/inspect")
def staff_inspect(return_id: int, payload: InspectIn, request: Request):
    pid = _staff(request, ("picker", "both"))
    try:
        with ws_connect() as conn:
            return wr.inspect(conn, return_id, payload.results, pid)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/pick/return-pickups")
def driver_return_pickups(request: Request):
    did = _staff(request, ("driver", "both"))
    with ws_connect() as conn:
        return wr.driver_pickups(conn, did)


@router.post("/api/pick/return-pickups/{return_id}/done")
def driver_return_done(return_id: int, request: Request):
    did = _staff(request, ("driver", "both"))
    try:
        with ws_connect() as conn:
            wr.picked_up(conn, return_id, did)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    return {"ok": True}


# -------------------------------------------------------------------- admin


@router.get("/api/admin/wholesale/returns")
def admin_returns(request: Request, status: str = ""):
    _admin(request)
    with ws_connect() as conn:
        return wr.admin_returns(conn, status[:20])


@router.get("/api/admin/wholesale/returns/{return_id}")
def admin_return(return_id: int, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            return wr.return_view(conn, return_id, admin=True)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/admin/wholesale/returns/{return_id}/photo")
def admin_return_photo(return_id: int, request: Request):
    _admin(request)
    with ws_connect() as conn:
        row = conn.execute("SELECT photo_path FROM ws_returns WHERE id = ?", (return_id,)).fetchone()
    if not row or not row["photo_path"] or not (_photos_dir() / row["photo_path"]).is_file():
        raise HTTPException(404, "مفيش صورة.")
    return FileResponse(_photos_dir() / row["photo_path"], media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/api/admin/wholesale/returns/{return_id}/decide")
def admin_return_decide(return_id: int, payload: DecideIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wr.decide(conn, return_id, payload.approve, payload.pickup, payload.driver_id, payload.reason, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "return_" + ("accept" if payload.approve else "reject"), f"return={return_id}")
    return result


@router.post("/api/admin/wholesale/returns/{return_id}/refund")
def admin_return_refund(return_id: int, payload: RefundIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wr.refund(conn, return_id, payload.amount, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "return_refund", f"return={return_id} amount={payload.amount}")
    return result


@router.get("/api/admin/wholesale/ratings")
def admin_ratings(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wr.ratings_report(conn)


@router.get("/api/admin/wholesale/feedback")
def admin_feedback(request: Request, kind: str = "", status: str = "open"):
    _admin(request)
    with ws_connect() as conn:
        return wr.admin_feedback(conn, kind[:20], status[:20])


@router.post("/api/admin/wholesale/feedback/{feedback_id}")
def admin_reply_feedback(feedback_id: int, payload: ReplyIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wr.reply_feedback(conn, feedback_id, payload.reply, payload.resolve)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "feedback_reply", f"id={feedback_id}")
    return result
