"""Routes for wholesale phase 8: Rose Plus (trader and admin) and the reports."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import wholesale_plus as wplus
from . import wholesale_reports as wrep
from . import wholesale_service as ws
from .wholesale_schema import ws_connect
from .wholesale_api import _admin, _audit, _fail
from .wholesale_orders_api import _drop_proof, _payments_dir, _save_proof, _trader
from .wholesale_xlsx import build_xlsx

router = APIRouter()


class PlusRequestIn(BaseModel):
    plan: str = Field(max_length=20)
    method: str = Field(max_length=20)
    proof: Optional[str] = Field(default=None, max_length=8_000_000)
    sender: str = Field(default="", max_length=80)


class PlusDecisionIn(BaseModel):
    approve: bool
    reason: str = Field(default="", max_length=300)


class PlusGrantIn(BaseModel):
    customer_id: int
    plan: str = Field(max_length=20)
    paid: bool = True


# ---------------------------------------------------------------- trader


@router.get("/api/wholesale/plus")
def trader_plus(request: Request):
    customer_id = _trader(request)
    with ws_connect() as conn:
        return wplus.plus_status(conn, customer_id)


@router.post("/api/wholesale/plus")
def trader_plus_request(payload: PlusRequestIn, request: Request):
    customer_id = _trader(request)
    proof = _save_proof(customer_id, payload.proof) if payload.proof and payload.method != "cash" else ""
    try:
        with ws_connect() as conn:
            wplus.request_plus(conn, customer_id, payload.plan, payload.method, proof, payload.sender)
            return wplus.plus_status(conn, customer_id)
    except ws.WholesaleError as exc:
        if proof:
            _drop_proof(proof)
        raise _fail(exc)


# ----------------------------------------------------------------- admin


@router.get("/api/admin/wholesale/plus")
def admin_plus(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wplus.admin_plus(conn)


@router.get("/api/admin/wholesale/plus/{sub_id}/proof")
def admin_plus_proof(sub_id: int, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            name = wplus.proof_path(conn, sub_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    path = _payments_dir() / name
    if not path.is_file():
        raise HTTPException(404, "الصورة مش موجودة.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/api/admin/wholesale/plus/{sub_id}/decide")
def admin_plus_decide(sub_id: int, payload: PlusDecisionIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wplus.decide_plus(conn, sub_id, payload.approve, payload.reason, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "plus_decide", f"sub={sub_id} approve={payload.approve}")
    return result


@router.post("/api/admin/wholesale/plus/grant")
def admin_plus_grant(payload: PlusGrantIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wplus.grant_plus(conn, payload.customer_id, payload.plan, user["id"], payload.paid)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "plus_grant", f"customer={payload.customer_id} plan={payload.plan} paid={payload.paid}")
    return result


@router.post("/api/admin/wholesale/plus/{sub_id}/cancel")
def admin_plus_cancel(sub_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wplus.cancel_plus(conn, sub_id, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "plus_cancel", f"sub={sub_id}")
    return result


# --------------------------------------------------------------- reports


def _xlsx(sheets, name: str) -> Response:
    return Response(
        content=build_xlsx(sheets),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"', "Cache-Control": "no-store"},
    )


@router.get("/api/admin/wholesale/reports")
def admin_report(request: Request, period: str = "month", ref: str = ""):
    _admin(request)
    try:
        with ws_connect() as conn:
            return wrep.store_report(conn, period[:10], ref[:10])
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/admin/wholesale/reports/export")
def admin_report_export(request: Request, period: str = "month", ref: str = ""):
    _admin(request)
    try:
        with ws_connect() as conn:
            rep = wrep.store_report(conn, period[:10], ref[:10])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    return _xlsx(wrep.store_report_rows(rep), f"rose-wholesale-{rep['range']['key']}")


@router.get("/api/admin/wholesale/reports/suppliers")
def admin_supplier_report(request: Request, period: str = "month", ref: str = "", supplier_id: Optional[int] = None):
    _admin(request)
    try:
        with ws_connect() as conn:
            return wrep.supplier_report(conn, period[:10], ref[:10], supplier_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/admin/wholesale/reports/suppliers/export")
def admin_supplier_report_export(request: Request, period: str = "month", ref: str = "", supplier_id: Optional[int] = None):
    _admin(request)
    try:
        with ws_connect() as conn:
            rep = wrep.supplier_report(conn, period[:10], ref[:10], supplier_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    return _xlsx(wrep.supplier_report_rows(rep), f"rose-suppliers-{rep['range']['key']}" + (f"-{supplier_id}" if supplier_id else ""))
