"""Routes for wholesale phase 6: driver trips on /pick/, admin delivery tab, zones, trader address."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from . import wholesale_delivery as wd
from . import wholesale_service as ws
from .customer_auth import require_customer
from .wholesale_schema import ws_connect
from .wholesale_api import _admin, _audit, _fail
from .wholesale_picking_api import _staff

router = APIRouter()


def _driver(request: Request) -> int:
    return _staff(request, wd.DRIVER_ROLES)


class DeliverIn(BaseModel):
    code: str = Field(min_length=1, max_length=8)
    cash: Optional[float] = None


class FailIn(BaseModel):
    reason: str = Field(default="", max_length=200)


class AssignIn(BaseModel):
    driver_id: int
    eta: str = Field(default="", max_length=60)


class SettleIn(BaseModel):
    note: str = Field(default="", max_length=200)


class ZoneIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    fee: Optional[float] = None
    min_order: Optional[float] = None
    is_active: Optional[bool] = None


class TraderDeliveryIn(BaseModel):
    zone_id: Optional[int] = None
    address: Optional[str] = Field(default=None, max_length=300)


class AddressIn(BaseModel):
    address: str = Field(min_length=3, max_length=300)


def _dump(payload: BaseModel):
    return payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)


# ------------------------------------------------------------------ driver


@router.get("/api/pick/trips")
def driver_trips(request: Request):
    did = _driver(request)
    with ws_connect() as conn:
        return wd.driver_trips(conn, did)


@router.post("/api/pick/trips/{order_id}/out")
def driver_out(order_id: int, request: Request):
    did = _driver(request)
    try:
        with ws_connect() as conn:
            return wd.go_out(conn, order_id, did)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/pick/trips/{order_id}/arrived")
def driver_arrived(order_id: int, request: Request):
    did = _driver(request)
    try:
        with ws_connect() as conn:
            return wd.arrived(conn, order_id, did)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/pick/trips/{order_id}/deliver")
def driver_deliver(order_id: int, payload: DeliverIn, request: Request):
    did = _driver(request)
    try:
        with ws_connect() as conn:
            return wd.deliver(conn, order_id, did, payload.code, payload.cash)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/pick/trips/{order_id}/fail")
def driver_fail(order_id: int, payload: FailIn, request: Request):
    did = _driver(request)
    try:
        with ws_connect() as conn:
            return wd.fail(conn, order_id, did, payload.reason)
    except ws.WholesaleError as exc:
        raise _fail(exc)


# ------------------------------------------------------------------- trader


@router.put("/api/wholesale/address")
def trader_address(payload: AddressIn, request: Request):
    customer = require_customer(request)
    try:
        with ws_connect() as conn:
            return wd.set_trader_delivery(conn, customer["id"], None, payload.address)
    except ws.WholesaleError as exc:
        raise _fail(exc)


# -------------------------------------------------------------------- admin


@router.get("/api/admin/wholesale/delivery")
def admin_delivery(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wd.board(conn)


@router.post("/api/admin/wholesale/delivery/{order_id}/assign")
def admin_assign(order_id: int, payload: AssignIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wd.assign(conn, order_id, payload.driver_id, payload.eta)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "delivery_assign", f"order={order_id} driver={payload.driver_id}")
    return result


@router.post("/api/admin/wholesale/delivery/{order_id}/unassign")
def admin_unassign(order_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            wd.unassign(conn, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "delivery_unassign", f"order={order_id}")
    return {"ok": True}


@router.post("/api/admin/wholesale/drivers/{driver_id}/settle")
def admin_settle(driver_id: int, payload: SettleIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wd.settle_cash(conn, driver_id, user["id"], payload.note)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "driver_cash", f"driver={driver_id} amount={result['amount']}")
    return result


@router.get("/api/admin/wholesale/zones")
def admin_zones(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wd.list_zones(conn)


@router.post("/api/admin/wholesale/zones")
def admin_create_zone(payload: ZoneIn, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            return wd.save_zone(conn, _dump(payload))
    except (ws.WholesaleError, ValueError) as exc:
        raise _fail(exc if isinstance(exc, ws.WholesaleError) else ws.WholesaleError(str(exc)))


@router.patch("/api/admin/wholesale/zones/{zone_id}")
def admin_update_zone(zone_id: int, payload: ZoneIn, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            return wd.save_zone(conn, _dump(payload), zone_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.put("/api/admin/wholesale/trader-accounts/{customer_id}/delivery")
def admin_trader_delivery(customer_id: int, payload: TraderDeliveryIn, request: Request):
    _admin(request)
    data = _dump(payload)
    try:
        with ws_connect() as conn:
            return wd.set_trader_delivery(conn, customer_id, data.get("zone_id", None) if "zone_id" in data else None, data.get("address"))
    except ws.WholesaleError as exc:
        raise _fail(exc)
