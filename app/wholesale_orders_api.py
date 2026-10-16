"""Routes for wholesale phase 3: the trader screen and order handling in the admin.

Trader (Rose customer session, approved trader only):
    /wholesale/                     the mobile screen
    /api/wholesale/...              account, catalog, orders, shortages, notifications
Admin (owner / manager):
    /api/admin/wholesale/orders, purchase-orders, shortages, trader-accounts
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .customer_auth import current_customer, require_customer
from . import wholesale_orders as wo
from . import wholesale_service as ws
from .wholesale_schema import ws_connect
from .wholesale_api import _admin, _audit, _fail
from . import wholesale_payments as wp

STATIC = Path(__file__).resolve().parent / "static" / "wholesale"
router = APIRouter()


# ---------------------------------------------------------------------- models


class JoinIn(BaseModel):
    shop_name: str = Field(min_length=1, max_length=120)
    area: str = Field(min_length=1, max_length=120)
    address: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=400)


class CartLineIn(BaseModel):
    product_id: int
    unit: str = Field(max_length=10)
    qty: int = Field(ge=1, le=9999)
    note: str = Field(default="", max_length=200)


class OrderIn(BaseModel):
    items: List[CartLineIn] = Field(min_length=1, max_length=300)
    note: str = Field(default="", max_length=500)
    payment_method: str = Field(default="cash", max_length=20)
    team_request_ids: List[int] = Field(default_factory=list, max_length=50)


class MethodIn(BaseModel):
    method: str = Field(max_length=20)


class ProofIn(BaseModel):
    method: str = Field(max_length=20)
    image: str = Field(min_length=32, max_length=8_000_000)
    sender: str = Field(default="", max_length=80)
    amount: Optional[float] = None


class PaymentMethodIn(BaseModel):
    label: Optional[str] = Field(default=None, max_length=60)
    is_enabled: Optional[bool] = None
    account: Optional[str] = Field(default=None, max_length=120)
    instructions: Optional[str] = Field(default=None, max_length=600)
    fee_type: Optional[str] = None
    fee_value: Optional[float] = None
    fee_payer: Optional[str] = None
    max_amount: Optional[float] = None


class PaymentDecisionIn(BaseModel):
    approve: bool
    reason: str = Field(default="", max_length=300)


class AmountIn(BaseModel):
    amount: float


class CreditAccountIn(BaseModel):
    credit_limit: float = Field(ge=0)
    period_days: Optional[int] = None


class CreditEntryIn(BaseModel):
    kind: str = Field(max_length=20)
    amount: float
    note: str = Field(default="", max_length=200)


class ProvisionIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    note: str = Field(default="", max_length=300)
    unit: str = Field(default="piece", max_length=10)
    qty: int = Field(default=1, ge=1, le=9999)


class SubscriptionIn(BaseModel):
    note: str = Field(default="", max_length=300)


class DecisionIn(BaseModel):
    decision: str = Field(max_length=20)
    qty: Optional[int] = None


class ReceiveIn(BaseModel):
    received: Dict[str, int] = Field(default_factory=dict)


class CancelIn(BaseModel):
    reason: str = Field(default="", max_length=300)


class TraderDecisionIn(BaseModel):
    status: str = Field(max_length=20)
    tier_id: Optional[int] = None


class CloseRequestIn(BaseModel):
    product_id: Optional[int] = None
    message: str = Field(default="", max_length=400)


def _trader(request: Request) -> int:
    customer = require_customer(request)
    with ws_connect() as conn:
        try:
            wo.require_trader(conn, customer["id"])
        except ws.WholesaleError as exc:
            raise _fail(exc)
    return int(customer["id"])


# ---------------------------------------------------------------------- trader


@router.get("/wholesale/", include_in_schema=False)
def trader_page():
    return FileResponse(STATIC / "trader" / "index.html")


@router.get("/wholesale", include_in_schema=False)
def trader_page_redirect():
    from fastapi.responses import Response

    return Response(status_code=307, headers={"Location": "/wholesale/"})


@router.get("/api/wholesale/me")
def trader_me(request: Request):
    customer = current_customer(request)
    if not customer:
        return {"logged_in": False}
    with ws_connect() as conn:
        trader = wo.trader_status(conn, customer["id"])
        data = {
            "logged_in": True,
            "name": customer["name"],
            "must_change_password": bool(customer.get("must_change_password")),
            "trader_status": trader["status"],
            "shop_name": trader.get("shop_name", ""),
            "address": trader.get("address", ""),
            "area": trader.get("area", ""),
        }
        if trader["status"] == "approved":
            from .wholesale_notebook import check_reminder

            check_reminder(conn, customer["id"])  # weekly notebook reminder, before counting unread
            data["sections"] = wo.trader_sections(conn)
            data["announcement"] = ws.announcement_for_customer(conn, customer["id"])
            data["unread"] = wo.unread_count(conn, customer["id"])
            data["credit_overdue"] = wp.credit_summary(conn, customer["id"])["overdue"]
            from .wholesale_plus import active_plus

            plus = active_plus(conn, customer["id"])
            data["plus"] = {
                "enabled": ws.get_settings(conn).get("plus_enabled") == "1",
                "active": bool(plus),
                "ends_at": plus["ends_at"] if plus else None,
            }
            from .wholesale_social import unread_total

            data["chat_unread"] = unread_total(conn, customer["id"])
            from .wholesale_team import pending_count

            data["team_pending"] = pending_count(conn, customer["id"])
            data["available_shortages"] = conn.execute(
                "SELECT COUNT(*) FROM ws_shortages WHERE customer_id = ? AND status = 'available'", (customer["id"],)
            ).fetchone()[0]
    return data


@router.post("/api/wholesale/join")
def trader_join(payload: JoinIn, request: Request):
    customer = require_customer(request)
    try:
        with ws_connect() as conn:
            return wo.apply_as_trader(conn, customer["id"], payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/wholesale/catalog")
def trader_catalog(request: Request, q: str = "", section_id: Optional[int] = None, page: int = 1):
    customer_id = _trader(request)
    with ws_connect() as conn:
        return wo.catalog(conn, customer_id, q=q[:100], section_id=section_id, page=page)


@router.post("/api/wholesale/cart/check")
def trader_cart_check(payload: OrderIn, request: Request):
    """Current prices for the products in the browser cart (prices may have changed)."""
    customer_id = _trader(request)
    ids = sorted({line.product_id for line in payload.items})
    with ws_connect() as conn:
        items = {i["id"]: i for i in wo.catalog(conn, customer_id, ids=ids, per_page=100)["items"]}
        products = 0.0
        for line in payload.items:
            info = items.get(line.product_id, {}).get("units", {}).get(line.unit)
            if info:
                products += info["price"] * line.qty
        checkout = wp.checkout(conn, customer_id, round(products, 2))
    return {"items": {str(k): v for k, v in items.items()}, "checkout": checkout}


@router.post("/api/wholesale/orders")
def trader_submit(payload: OrderIn, request: Request):
    customer_id = _trader(request)
    lines = [line.model_dump() if hasattr(line, "model_dump") else line.dict() for line in payload.items]
    try:
        with ws_connect() as conn:
            order = wo.submit_order(conn, customer_id, lines, payload.note, payload.payment_method)
            if payload.team_request_ids:
                from .wholesale_team import link_order

                link_order(conn, customer_id, payload.team_request_ids, order["id"], order["number"])
            return order
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.get("/api/wholesale/orders")
def trader_orders(request: Request, scope: str = "all"):
    customer_id = _trader(request)
    with ws_connect() as conn:
        return wo.customer_orders(conn, customer_id, scope)


@router.get("/api/wholesale/orders/{order_id}")
def trader_order(order_id: int, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            data = wo.customer_order(conn, customer_id, order_id)
            from . import wholesale_returns as wr

            data["returns"] = wr.customer_returns(conn, customer_id, order_id)
            data["rating"] = wr.rating_for(conn, order_id)
            data["liked_options"] = wr.LIKED
            if data["status"] == "delivered":
                info = wr.returnable(conn, customer_id, order_id)
                data["return_open"], data["return_deadline"], data["return_days"] = info["open"], info["deadline"], info["days"]
            if data["payment"]["can_change_method"]:
                data["payment_options"] = [
                    {"code": m["code"], "label": m["label"], "grand": m["grand"]}
                    for m in wp.checkout(conn, customer_id, data["payment"]["products"])["methods"]
                    if m["available"] or m["code"] == data["payment"]["method"]
                ]
            return data
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/wholesale/orders/{order_id}/cancel")
def trader_cancel(order_id: int, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            return wo.cancel_by_customer(conn, customer_id, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/wholesale/orders/{order_id}/payment-method")
def trader_change_method(order_id: int, payload: MethodIn, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            order = conn.execute("SELECT * FROM ws_orders WHERE id = ? AND customer_id = ?", (order_id, customer_id)).fetchone()
            if not order:
                raise ws.WholesaleError("الطلب مش موجود.", 404)
            wp.change_method(conn, customer_id, dict(order), payload.method)
            return wo.customer_order(conn, customer_id, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


def _payments_dir() -> Path:
    from .db import db_path

    path = db_path().parent / "wholesale_payments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_proof(customer_id: int, data_url: str) -> str:
    import base64
    import binascii
    import secrets
    from io import BytesIO

    from PIL import Image, ImageOps, UnidentifiedImageError

    raw = data_url.split(",", 1)[-1]
    try:
        content = base64.b64decode(raw, validate=True)
        if len(content) > 6 * 1024 * 1024:
            raise HTTPException(413, "الصورة أكبر من 6 ميجا.")
        image = ImageOps.exif_transpose(Image.open(BytesIO(content))).convert("RGB")
    except (binascii.Error, ValueError, UnidentifiedImageError, OSError):
        raise HTTPException(400, "صورة التحويل مش سليمة.")
    image.thumbnail((1600, 1600))
    name = f"c{customer_id}-{secrets.token_hex(8)}.jpg"
    image.save(_payments_dir() / name, "JPEG", quality=85, optimize=True)
    return name


def _drop_proof(name: str) -> None:
    try:
        (_payments_dir() / name).unlink()
    except OSError:
        pass


@router.post("/api/wholesale/orders/{order_id}/pay")
def trader_pay_order(order_id: int, payload: ProofIn, request: Request):
    customer_id = _trader(request)
    name = _save_proof(customer_id, payload.image)
    try:
        with ws_connect() as conn:
            wp.submit_transfer(conn, customer_id, "order", payload.method, None, name, payload.sender, order_id)
            return wo.customer_order(conn, customer_id, order_id)
    except ws.WholesaleError as exc:
        _drop_proof(name)
        raise _fail(exc)


@router.get("/api/wholesale/account")
def trader_account(request: Request):
    customer_id = _trader(request)
    with ws_connect() as conn:
        credit = wp.credit_summary(conn, customer_id, with_entries=True)
        from .wholesale_points import account as points_account, list_rewards

        points = points_account(conn, customer_id)
        rewards = list_rewards(conn, active_only=True)
        pending = [
            dict(r) for r in conn.execute(
                "SELECT amount, created_at FROM ws_payments WHERE customer_id = ? AND purpose = 'credit' AND status = 'pending' ORDER BY id DESC",
                (customer_id,),
            )
        ]
        methods = [
            {"code": m["code"], "label": m["label"], "account": m["account"], "instructions": m["instructions"]}
            for m in wp.list_methods(conn) if m["is_enabled"] and m["code"] in wp.TRANSFER_METHODS
        ]
        rate = float(wp.get_settings(conn).get("points_egp_per_point", "0") or 0)
    return {"credit": credit, "points_balance": points["balance"], "points": points["entries"], "redemptions": points["redemptions"],
            "rewards": rewards, "points_rate": rate, "pending_credit_payments": pending, "transfer_methods": methods}


@router.post("/api/wholesale/credit/pay")
def trader_pay_credit(payload: ProofIn, request: Request):
    customer_id = _trader(request)
    name = _save_proof(customer_id, payload.image)
    try:
        with ws_connect() as conn:
            return wp.submit_transfer(conn, customer_id, "credit", payload.method, payload.amount, name, payload.sender)
    except ws.WholesaleError as exc:
        _drop_proof(name)
        raise _fail(exc)


@router.get("/api/wholesale/shortages")
def trader_shortages(request: Request):
    customer_id = _trader(request)
    with ws_connect() as conn:
        return wo.customer_shortages(conn, customer_id)


@router.post("/api/wholesale/shortages")
def trader_provision(payload: ProvisionIn, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            return wo.request_provision(conn, customer_id, payload.name, payload.note, payload.unit, payload.qty)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.delete("/api/wholesale/shortages/{shortage_id}")
def trader_close_shortage(shortage_id: int, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            wo.close_own_shortage(conn, customer_id, shortage_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    return {"ok": True}


@router.get("/api/wholesale/notifications")
def trader_notifications(request: Request):
    customer_id = _trader(request)
    with ws_connect() as conn:
        return wo.notifications(conn, customer_id)


@router.post("/api/wholesale/notifications/read")
def trader_notifications_read(request: Request):
    customer_id = _trader(request)
    with ws_connect() as conn:
        wo.mark_all_read(conn, customer_id)
    return {"ok": True}


@router.post("/api/wholesale/subscription-request")
def trader_subscription_request(payload: SubscriptionIn, request: Request):
    customer_id = _trader(request)
    try:
        with ws_connect() as conn:
            return ws.request_subscription(conn, customer_id, payload.note)
    except ws.WholesaleError as exc:
        raise _fail(exc)


# ----------------------------------------------------------------------- admin


@router.get("/api/admin/wholesale/counts")
def admin_counts(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wo.admin_counts(conn)


@router.get("/api/admin/wholesale/orders")
def admin_orders(request: Request, status: str = "", q: str = "", page: int = 1):
    _admin(request)
    with ws_connect() as conn:
        return wo.admin_orders(conn, status[:20], q[:80], page)


@router.get("/api/admin/wholesale/orders/{order_id}")
def admin_order(order_id: int, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            return wo.admin_order(conn, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/admin/wholesale/orders/{order_id}/items/{item_id}/decision")
def admin_item_decision(order_id: int, item_id: int, payload: DecisionIn, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            wo.set_item_decision(conn, order_id, item_id, payload.decision, payload.qty)
            return wo.admin_order(conn, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/admin/wholesale/orders/{order_id}/purchase-orders/{po_id}/all-available")
def admin_po_all_available(order_id: int, po_id: int, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            wo.po_all_available(conn, order_id, po_id)
            return wo.admin_order(conn, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/admin/wholesale/purchase-orders/{po_id}/sent")
def admin_po_sent(po_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            wo.mark_po_sent(conn, po_id)
            order_id = conn.execute("SELECT order_id FROM ws_purchase_orders WHERE id = ?", (po_id,)).fetchone()[0]
            result = wo.admin_order(conn, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "po_sent", f"po={po_id}")
    return result


@router.post("/api/admin/wholesale/orders/{order_id}/confirm-review")
def admin_confirm_review(order_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wo.confirm_review(conn, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "order_review", f"order={order_id} total={result['final_total']}")
    return result


@router.post("/api/admin/wholesale/purchase-orders/{po_id}/receive")
def admin_po_receive(po_id: int, payload: ReceiveIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wo.receive_po(conn, po_id, payload.received)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "po_receive", f"po={po_id}")
    return result


@router.post("/api/admin/wholesale/orders/{order_id}/cancel")
def admin_cancel(order_id: int, payload: CancelIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wo.cancel_by_admin(conn, order_id, payload.reason)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "order_cancel", f"order={order_id}")
    return result


@router.get("/api/admin/wholesale/shortages")
def admin_shortages(request: Request, supplier_id: Optional[int] = None):
    _admin(request)
    with ws_connect() as conn:
        return wo.admin_shortages(conn, supplier_id)


@router.get("/api/admin/wholesale/shortages/products/{product_id}")
def admin_shortage_customers(product_id: int, request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wo.shortage_customers(conn, product_id)


@router.post("/api/admin/wholesale/shortages/products/{product_id}/available")
def admin_shortage_available(product_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            count = wo.mark_available(conn, product_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "shortage_available", f"product={product_id} notified={count}")
    return {"notified": count}


@router.post("/api/admin/wholesale/shortages/{shortage_id}/close")
def admin_close_request(shortage_id: int, payload: CloseRequestIn, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            wo.close_request(conn, shortage_id, payload.product_id, payload.message)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    return {"ok": True}


@router.get("/api/admin/wholesale/trader-accounts")
def admin_trader_accounts(request: Request, status: str = "pending", q: str = ""):
    _admin(request)
    with ws_connect() as conn:
        return wo.list_trader_accounts(conn, status[:20], q[:80])


@router.post("/api/admin/wholesale/trader-accounts/{customer_id}")
def admin_trader_decide(customer_id: int, payload: TraderDecisionIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wo.decide_trader(conn, customer_id, payload.status, payload.tier_id, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "trader_" + payload.status, f"customer={customer_id}")
    return result


# ------------------------------------------------------------ admin: phase 4


@router.get("/api/admin/wholesale/payment-methods")
def admin_payment_methods(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wp.list_methods(conn)


@router.put("/api/admin/wholesale/payment-methods/{code}")
def admin_update_payment_method(code: str, payload: PaymentMethodIn, request: Request):
    user = _admin(request)
    data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    try:
        with ws_connect() as conn:
            result = wp.update_method(conn, code, data)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "payment_method", f"{code} " + ",".join(data))
    return result


@router.get("/api/admin/wholesale/payments")
def admin_payments(request: Request, status: str = "pending"):
    _admin(request)
    with ws_connect() as conn:
        return wp.list_payments(conn, status[:20])


@router.get("/api/admin/wholesale/payments/{payment_id}/proof")
def admin_payment_proof(payment_id: int, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            name = wp.payment_proof_path(conn, payment_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    path = _payments_dir() / name
    if not path.is_file():
        raise HTTPException(404, "الصورة مش موجودة.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/api/admin/wholesale/payments/{payment_id}/decide")
def admin_decide_payment(payment_id: int, payload: PaymentDecisionIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wp.decide_payment(conn, payment_id, payload.approve, payload.reason, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "payment_" + result["status"], f"payment={payment_id}")
    return result


@router.post("/api/admin/wholesale/orders/{order_id}/delivered")
def admin_order_delivered(order_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            wp.mark_delivered(conn, order_id, user["id"])
            result = wo.admin_order(conn, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "order_delivered", f"order={order_id} points={result['points_awarded']}")
    return result


@router.post("/api/admin/wholesale/orders/{order_id}/refunded")
def admin_order_refunded(order_id: int, payload: AmountIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            wp.mark_refunded(conn, order_id, payload.amount)
            result = wo.admin_order(conn, order_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "order_refunded", f"order={order_id} amount={payload.amount}")
    return result


@router.get("/api/admin/wholesale/credit")
def admin_credit_accounts(request: Request, status: str = ""):
    _admin(request)
    with ws_connect() as conn:
        return wp.credit_accounts(conn, status[:20])


@router.get("/api/admin/wholesale/credit/{customer_id}")
def admin_credit_account(customer_id: int, request: Request):
    _admin(request)
    with ws_connect() as conn:
        return wp.credit_summary(conn, customer_id, with_entries=True)


@router.put("/api/admin/wholesale/credit/{customer_id}")
def admin_set_credit(customer_id: int, payload: CreditAccountIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            wp.set_credit_account(conn, customer_id, payload.credit_limit, payload.period_days)
            result = wp.credit_summary(conn, customer_id, with_entries=True)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "credit_limit", f"customer={customer_id} limit={payload.credit_limit} days={payload.period_days}")
    return result


@router.post("/api/admin/wholesale/credit/{customer_id}/entries")
def admin_credit_entry(customer_id: int, payload: CreditEntryIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = wp.record_credit_entry(conn, customer_id, payload.kind, payload.amount, payload.note, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "credit_" + payload.kind, f"customer={customer_id} amount={payload.amount}")
    return result
