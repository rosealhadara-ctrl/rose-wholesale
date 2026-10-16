"""روز جملة: the wholesale store as its own app (separate from the retail Rose site)."""

from __future__ import annotations

import base64
import binascii
import os
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import bootstrap_owner_from_environment, require_staff
from .db import db_path, init_db
from .wholesale_api import router as wholesale_router

STATIC = Path(__file__).resolve().parent / "static"
IS_PRODUCTION = os.getenv("WHOLESALE_ENV", "").lower() == "production"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    bootstrap_owner_from_environment()
    yield


app = FastAPI(title="روز جملة", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
    if IS_PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "rose-wholesale"}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/wholesale/", status_code=307)


@app.get("/staff-login/", include_in_schema=False)
def staff_login_page():
    return FileResponse(STATIC / "wholesale" / "login" / "index.html")


# ---------------------------------------------------------------- branding


def _logo_path() -> Path:
    path = db_path().parent / "branding"
    path.mkdir(parents=True, exist_ok=True)
    return path / "logo.png"


@app.get("/api/branding/logo", include_in_schema=False)
def branding_logo():
    path = _logo_path()
    if path.is_file():
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})
    return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")


class LogoIn(BaseModel):
    image: str = Field(min_length=32, max_length=4_000_000)


@app.post("/api/admin/wholesale/branding/logo")
def upload_logo(payload: LogoIn, request: Request):
    require_staff(request, {"owner", "manager"})
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        content = base64.b64decode(payload.image.split(",", 1)[-1], validate=True)
        image = ImageOps.exif_transpose(Image.open(BytesIO(content))).convert("RGBA")
    except (binascii.Error, ValueError, UnidentifiedImageError, OSError):
        raise HTTPException(400, "الصورة مش سليمة.")
    image.thumbnail((512, 512))
    image.save(_logo_path(), "PNG", optimize=True)
    return {"ok": True}


# Wholesale routes come before the static mount so /wholesale/ etc. resolve to the pages.
app.include_router(wholesale_router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

