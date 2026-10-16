"""HTTP routes for the wholesale admin (phases 1 and 2) and the trader hooks.

Admin pages: /admin/wholesale/  (owner and manager only)
Admin API:   /api/admin/wholesale/...
Trader API:  /api/wholesale/...   (Rose customer session; trader screens come in phase 3)
"""

from __future__ import annotations

import base64
import binascii
import re
import secrets
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .auth import current_staff_user, require_staff, write_staff_audit
from .db import db_path
from . import wholesale_service as ws
from .wholesale_pricing import list_tiers
from .wholesale_schema import ws_connect

STATIC = Path(__file__).resolve().parent / "static" / "wholesale"
ADMIN_ROLES = {"owner", "manager"}
router = APIRouter()


def _dump(payload: BaseModel) -> Dict[str, Any]:
    """Only the fields the client actually sent (Pydantic v2 and v1)."""
    modern = getattr(payload, "model_dump", None)
    return modern(exclude_unset=True) if modern else payload.dict(exclude_unset=True)


def _admin(request: Request) -> Dict[str, Any]:
    return require_staff(request, ADMIN_ROLES)


def _fail(exc: ws.WholesaleError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _audit(request: Request, user: Dict[str, Any], action: str, details: str = "") -> None:
    # Called after the wholesale connection is closed, so SQLite is not locked.
    write_staff_audit(
        "wholesale_" + action,
        user_id=user["id"],
        details=details[:500],
        ip_address=request.client.host if request.client else None,
    )


# ---------------------------------------------------------------------- models


class SupplierIn(BaseModel):
    alias: Optional[str] = Field(default=None, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=30)
    notes: Optional[str] = Field(default=None, max_length=500)
    is_active: Optional[bool] = None


class SectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ImportStartIn(BaseModel):
    supplier_id: int
    filename: str = Field(min_length=4, max_length=180)
    content_base64: str = Field(min_length=1, max_length=14_000_000)


class ImportColumnsIn(BaseModel):
    sheet_index: int = 0
    header_row: int = 1


class ImportPreviewIn(BaseModel):
    sheet_index: int = 0
    header_row: int = 1
    mapping: Dict[str, Optional[int]]


class ImportApplyIn(BaseModel):
    decisions: Dict[str, int] = Field(default_factory=dict)
    save_template: bool = True


class ProductIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    code: Optional[str] = Field(default=None, max_length=80)
    section: Optional[str] = Field(default=None, max_length=80)
    pieces_per_dozen: Optional[int] = None
    pieces_per_carton: Optional[int] = None
    is_visible: Optional[bool] = None
    source_mode: Optional[str] = None
    manual_supplier_id: Optional[int] = None
    margin_percent: Optional[float] = None
    fixed_price_piece: Optional[float] = None
    fixed_price_dozen: Optional[float] = None
    fixed_price_carton: Optional[float] = None


class ManualProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(default="", max_length=80)
    section: str = Field(default="", max_length=80)
    supplier_id: int
    price_piece: Optional[float] = None
    price_dozen: Optional[float] = None
    price_carton: Optional[float] = None
    pieces_per_dozen: Optional[int] = None
    pieces_per_carton: Optional[int] = None
    margin_percent: Optional[float] = None
    is_visible: bool = True


class OfferIn(BaseModel):
    supplier_id: int
    price_piece: Optional[float] = None
    price_dozen: Optional[float] = None
    price_carton: Optional[float] = None
    is_active: bool = True


class MergeIn(BaseModel):
    into_product_id: int


class TierPricesIn(BaseModel):
    tier_id: int
    piece: Optional[float] = None
    dozen: Optional[float] = None
    carton: Optional[float] = None


class BulkIn(BaseModel):
    product_ids: List[int]
    is_visible: Optional[bool] = None
    source_mode: Optional[str] = None
    section: Optional[str] = Field(default=None, max_length=80)
    margin_percent: Optional[float] = None


class ImageIn(BaseModel):
    image: str = Field(min_length=32, max_length=6_000_000)


class SettingsIn(BaseModel):
    global_margin_percent: Optional[float] = None
    default_source_mode: Optional[str] = None
    announcement_enabled: Optional[bool] = None
    announcement_title: Optional[str] = Field(default=None, max_length=120)
    announcement_text: Optional[str] = Field(default=None, max_length=600)
    min_order_total: Optional[float] = None
    delivery_fee: Optional[float] = None
    points_egp_per_point: Optional[float] = None
    credit_period_days: Optional[int] = None
    credit_block_overdue: Optional[bool] = None
    return_days: Optional[int] = None
    plus_enabled: Optional[bool] = None
    plus_price_month: Optional[float] = None
    plus_price_quarter: Optional[float] = None
    plus_free_delivery: Optional[bool] = None
    plus_fee_share: Optional[float] = None


class TierIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=60)
    margin_percent: Optional[float] = None
    is_default: Optional[bool] = None
    is_subscriber: Optional[bool] = None


class TraderTierIn(BaseModel):
    tier_id: int


class DecisionIn(BaseModel):
    approve: bool
    tier_id: Optional[int] = None


# ----------------------------------------------------------------------- pages


@router.get("/admin/wholesale/", include_in_schema=False)
def wholesale_admin_page(request: Request):
    user = current_staff_user(request)
    if not user or user["role"] not in ADMIN_ROLES:
        return Response(status_code=307, headers={"Location": "/staff-login/?next=/admin/wholesale/"})
    return FileResponse(STATIC / "admin" / "index.html")


@router.get("/admin/wholesale", include_in_schema=False)
def wholesale_admin_redirect():
    return Response(status_code=307, headers={"Location": "/admin/wholesale/"})


# ---------------------------------------------------------------- admin: base


@router.get("/api/admin/wholesale/summary")
def admin_summary(request: Request):
    _admin(request)
    with ws_connect() as conn:
        data = ws.summary(conn)
        data["tiers"] = list_tiers(conn)
        data["sections"] = ws.list_sections(conn)
        data["suppliers"] = ws.list_suppliers(conn)
        return data


@router.get("/api/admin/wholesale/suppliers")
def admin_suppliers(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return ws.list_suppliers(conn)


@router.post("/api/admin/wholesale/suppliers")
def admin_create_supplier(payload: SupplierIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            supplier = ws.save_supplier(conn, _dump(payload))
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "supplier_create", f"id={supplier['id']}")
    return supplier


@router.patch("/api/admin/wholesale/suppliers/{supplier_id}")
def admin_update_supplier(supplier_id: int, payload: SupplierIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            supplier = ws.save_supplier(conn, _dump(payload), supplier_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "supplier_update", f"id={supplier_id}")
    return supplier


@router.get("/api/admin/wholesale/suppliers/{supplier_id}/template")
def admin_supplier_template(supplier_id: int, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            return {"template": ws.get_template(conn, supplier_id), "fields": [{"key": k, "label": l} for k, l in ws.FIELDS]}
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.delete("/api/admin/wholesale/suppliers/{supplier_id}/template")
def admin_delete_template(supplier_id: int, request: Request):
    user = _admin(request)
    with ws_connect() as conn:
        ws.delete_template(conn, supplier_id)
    _audit(request, user, "template_delete", f"supplier={supplier_id}")
    return {"ok": True}


@router.get("/api/admin/wholesale/sections")
def admin_sections(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return ws.list_sections(conn)


@router.post("/api/admin/wholesale/sections")
def admin_create_section(payload: SectionIn, request: Request):
    _admin(request)
    with ws_connect() as conn:
        ws.section_id_for(conn, payload.name)
        return ws.list_sections(conn)


# ---------------------------------------------------------------- admin: import


@router.post("/api/admin/wholesale/imports")
def admin_import_start(payload: ImportStartIn, request: Request):
    user = _admin(request)
    raw = payload.content_base64.split(",", 1)[-1]
    try:
        content = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "الملف وصل بشكل غلط. جرّب ترفعه تاني.")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "الملف أكبر من 10 ميجا.")
    try:
        with ws_connect() as conn:
            return ws.start_import(conn, payload.supplier_id, payload.filename, content, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/admin/wholesale/imports/{import_id}/columns")
def admin_import_columns(import_id: int, payload: ImportColumnsIn, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            return ws.import_columns(conn, import_id, payload.sheet_index, payload.header_row)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/admin/wholesale/imports/{import_id}/preview")
def admin_import_preview(import_id: int, payload: ImportPreviewIn, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            return ws.preview_import(conn, import_id, payload.mapping, payload.header_row, payload.sheet_index)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/admin/wholesale/imports/{import_id}/apply")
def admin_import_apply(import_id: int, payload: ImportApplyIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = ws.apply_import(conn, import_id, payload.decisions, payload.save_template)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    applied = result["summary"].get("applied", {})
    _audit(request, user, "import_apply", f"import={import_id} " + " ".join(f"{k}={v}" for k, v in applied.items()))
    return result


@router.post("/api/admin/wholesale/imports/{import_id}/cancel")
def admin_import_cancel(import_id: int, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            ws.cancel_import(conn, import_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    return {"ok": True}


@router.get("/api/admin/wholesale/imports")
def admin_imports(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return ws.list_imports(conn)


# -------------------------------------------------------------- admin: products


@router.get("/api/admin/wholesale/products")
def admin_products(
    request: Request,
    q: str = "",
    supplier_id: Optional[int] = None,
    section_id: Optional[int] = None,
    status: str = "",
    tier_id: Optional[int] = None,
    page: int = 1,
):
    _admin(request)
    try:
        with ws_connect() as conn:
            return ws.list_products(
                conn, q=q[:100], supplier_id=supplier_id, section_id=section_id, status=status, tier_id=tier_id, page=page
            )
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.post("/api/admin/wholesale/products")
def admin_create_product(payload: ManualProductIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            product = ws.create_manual_product(conn, _dump(payload))
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "product_create", f"id={product['id']}")
    return product


@router.post("/api/admin/wholesale/products/bulk")
def admin_bulk_products(payload: BulkIn, request: Request):
    user = _admin(request)
    data = _dump(payload)
    ids = data.pop("product_ids", [])
    try:
        with ws_connect() as conn:
            count = ws.bulk_update_products(conn, ids, data)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "product_bulk", f"count={count} fields={','.join(data)}")
    return {"updated": count}


@router.get("/api/admin/wholesale/products/{product_id}")
def admin_product(product_id: int, request: Request):
    _admin(request)
    try:
        with ws_connect() as conn:
            return ws.product_detail(conn, product_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)


@router.patch("/api/admin/wholesale/products/{product_id}")
def admin_update_product(product_id: int, payload: ProductIn, request: Request):
    user = _admin(request)
    data = _dump(payload)
    try:
        with ws_connect() as conn:
            product = ws.update_product(conn, product_id, data)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "product_update", f"id={product_id} fields={','.join(data)}")
    return product


@router.put("/api/admin/wholesale/products/{product_id}/offers")
def admin_save_offer(product_id: int, payload: OfferIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            product = ws.save_offer(conn, product_id, _dump(payload))
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "offer_save", f"product={product_id} supplier={payload.supplier_id}")
    return product


@router.post("/api/admin/wholesale/products/{product_id}/merge")
def admin_merge_product(product_id: int, payload: MergeIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            product = ws.merge_products(conn, product_id, payload.into_product_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "product_merge", f"{product_id}->{payload.into_product_id}")
    return product


@router.post("/api/admin/wholesale/offers/{offer_id}/split")
def admin_split_offer(offer_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            product = ws.split_offer(conn, offer_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "offer_split", f"offer={offer_id}")
    return product


@router.put("/api/admin/wholesale/products/{product_id}/tier-prices")
def admin_tier_prices(product_id: int, payload: TierPricesIn, request: Request):
    user = _admin(request)
    data = _dump(payload)
    tier_id = data.pop("tier_id")
    try:
        with ws_connect() as conn:
            product = ws.set_tier_prices(conn, product_id, tier_id, data)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "tier_prices", f"product={product_id} tier={tier_id}")
    return product


def _images_dir() -> Path:
    path = db_path().parent / "wholesale_images"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/api/admin/wholesale/products/{product_id}/image")
def admin_product_image(product_id: int, payload: ImageIn, request: Request):
    user = _admin(request)
    from PIL import Image, ImageOps, UnidentifiedImageError

    raw = payload.image.split(",", 1)[-1]
    try:
        data = base64.b64decode(raw, validate=True)
        image = Image.open(BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (binascii.Error, ValueError, UnidentifiedImageError, OSError):
        raise HTTPException(400, "الصورة مش سليمة.")
    image.thumbnail((900, 900))
    name = f"p{product_id}-{secrets.token_hex(6)}.jpg"
    image.save(_images_dir() / name, "JPEG", quality=85, optimize=True)
    try:
        with ws_connect() as conn:
            old = conn.execute("SELECT image_path FROM ws_products WHERE id = ?", (product_id,)).fetchone()
            product = ws.update_product(conn, product_id, {"image_path": name})
    except ws.WholesaleError as exc:
        try:
            (_images_dir() / name).unlink()
        except OSError:
            pass
        raise _fail(exc)
    if old and old["image_path"] and re.fullmatch(r"p\d+-[0-9a-f]{12}\.jpg", old["image_path"]):
        try:
            (_images_dir() / old["image_path"]).unlink()
        except OSError:
            pass
    _audit(request, user, "product_image", f"id={product_id}")
    return product


@router.get("/api/wholesale/images/{name}")
def wholesale_image(name: str):
    if not re.fullmatch(r"p\d+-[0-9a-f]{12}\.jpg", name):
        raise HTTPException(404, "Not found")
    path = _images_dir() / name
    if not path.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


# ------------------------------------------------------- admin: pricing & tiers


@router.get("/api/admin/wholesale/settings")
def admin_settings(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return ws.get_settings(conn)


@router.put("/api/admin/wholesale/settings")
def admin_update_settings(payload: SettingsIn, request: Request):
    user = _admin(request)
    data = _dump(payload)
    try:
        with ws_connect() as conn:
            settings = ws.update_settings(conn, data)
    except (ws.WholesaleError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    _audit(request, user, "settings", ",".join(f"{k}={v}" for k, v in data.items() if not k.startswith("announcement_t")))
    return settings


@router.get("/api/admin/wholesale/tiers")
def admin_tiers(request: Request):
    _admin(request)
    with ws_connect() as conn:
        return list_tiers(conn)


@router.post("/api/admin/wholesale/tiers")
def admin_create_tier(payload: TierIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            tiers = ws.save_tier(conn, _dump(payload))
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "tier_create", payload.name or "")
    return tiers


@router.patch("/api/admin/wholesale/tiers/{tier_id}")
def admin_update_tier(tier_id: int, payload: TierIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            tiers = ws.save_tier(conn, _dump(payload), tier_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "tier_update", f"id={tier_id}")
    return tiers


@router.delete("/api/admin/wholesale/tiers/{tier_id}")
def admin_delete_tier(tier_id: int, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            tiers = ws.delete_tier(conn, tier_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "tier_delete", f"id={tier_id}")
    return tiers


@router.get("/api/admin/wholesale/traders")
def admin_traders(request: Request, q: str = "", tier_id: Optional[int] = None):
    _admin(request)
    with ws_connect() as conn:
        return ws.list_traders(conn, q[:80], tier_id)


@router.put("/api/admin/wholesale/traders/{customer_id}/tier")
def admin_trader_tier(customer_id: int, payload: TraderTierIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = ws.set_trader_tier(conn, customer_id, payload.tier_id)
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "trader_tier", f"customer={customer_id} tier={payload.tier_id}")
    return result


@router.get("/api/admin/wholesale/subscription-requests")
def admin_subscription_requests(request: Request, status: str = "pending"):
    _admin(request)
    with ws_connect() as conn:
        return ws.list_subscription_requests(conn, status)


@router.post("/api/admin/wholesale/subscription-requests/{request_id}/decide")
def admin_decide_subscription(request_id: int, payload: DecisionIn, request: Request):
    user = _admin(request)
    try:
        with ws_connect() as conn:
            result = ws.decide_subscription(conn, request_id, payload.approve, payload.tier_id, user["id"])
    except ws.WholesaleError as exc:
        raise _fail(exc)
    _audit(request, user, "subscription_decide", f"id={request_id} {result['status']}")
    return result


# ------------------------------------------------------------------ phase 3
# Trader screen, orders, purchase orders, shortages: see wholesale_orders_api.py.

from .wholesale_orders_api import router as _orders_router  # noqa: E402

router.include_router(_orders_router)

# Phase 5: prep employees (picking and receiving from the phone).
from .wholesale_picking_api import router as _picking_router  # noqa: E402

router.include_router(_picking_router)

# Phase 6: delivery (zones, drivers, trips).
from .wholesale_delivery_api import router as _delivery_router  # noqa: E402

router.include_router(_delivery_router)

# Phase 7: returns, ratings, complaints and suggestions.
from .wholesale_returns_api import router as _returns_router  # noqa: E402

router.include_router(_returns_router)

# Phase 8: Rose Plus and reports.
from .wholesale_insights_api import router as _insights_router  # noqa: E402

router.include_router(_insights_router)

# Phase 9: the shortages notebook.
from .wholesale_notebook_api import router as _notebook_router  # noqa: E402

router.include_router(_notebook_router)

# Phase 10: chats, carton splits and nearby traders.
from .wholesale_social_api import router as _social_router  # noqa: E402

router.include_router(_social_router)

# Trader team accounts (manager and employees).
from .wholesale_team_api import router as _team_router  # noqa: E402

router.include_router(_team_router)

# Standalone app: trader accounts, staff accounts, wholesale points and rewards.
from .wholesale_accounts_api import router as _accounts_router  # noqa: E402

router.include_router(_accounts_router)
