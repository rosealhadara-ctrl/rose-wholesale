"""HTTP-level checks for the wholesale admin routes (phases 1 and 2)."""

import base64

from fastapi.testclient import TestClient


def _login(client, username, role):
    from app.auth import create_staff_user

    create_staff_user(username, username, role, "Wholesale-test-123")
    response = client.post("/api/staff/login", json={"username": username, "password": "Wholesale-test-123"})
    assert response.status_code == 200, response.text


def test_wholesale_admin_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(tmp_path / "ws-api.db"))
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/admin/wholesale/summary").status_code == 401
        _login(client, "owner1", "owner")

        assert client.get("/admin/wholesale/").status_code == 200
        supplier = client.post("/api/admin/wholesale/suppliers", json={"alias": "مورد تجربة"}).json()
        sheet = "الكود,الصنف,سعر القطعة,سعر الدستة\nA1,عصير,10,117.3\nA2,بسكويت,,\n".encode("utf-8")
        started = client.post(
            "/api/admin/wholesale/imports",
            json={"supplier_id": supplier["id"], "filename": "a.csv", "content_base64": base64.b64encode(sheet).decode()},
        )
        assert started.status_code == 200, started.text
        data = started.json()
        preview = client.post(
            f"/api/admin/wholesale/imports/{data['import_id']}/preview",
            json={"sheet_index": 0, "header_row": data["header_row"], "mapping": data["mapping"]},
        ).json()
        assert preview["summary"]["new"] == 1 and preview["summary"]["problems"] == 1
        applied = client.post(f"/api/admin/wholesale/imports/{data['import_id']}/apply", json={}).json()
        assert applied["summary"]["applied"]["created_products"] == 1

        assert client.put("/api/admin/wholesale/settings", json={"global_margin_percent": 0}).status_code == 200
        products = client.get("/api/admin/wholesale/products").json()
        assert products["items"][0]["units"]["dozen"]["price"] == 117.5

        product_id = products["items"][0]["id"]
        updated = client.patch(f"/api/admin/wholesale/products/{product_id}", json={"margin_percent": 20})
        assert updated.status_code == 200
        assert updated.json()["pricing"][0]["units"]["piece"]["price"] == 12.0

        # The example sheet on the upload page downloads and reads back with every column linked.
        sample = client.get("/api/admin/wholesale/imports/sample.xlsx")
        assert sample.status_code == 200 and sample.content[:2] == b"PK"
        started = client.post(
            "/api/admin/wholesale/imports",
            json={"supplier_id": supplier["id"], "filename": "sample.xlsx", "content_base64": base64.b64encode(sample.content).decode()},
        ).json()
        assert all(started["mapping"][k] is not None for k in ("name", "code", "section", "price_piece", "price_dozen", "price_carton", "pieces_per_carton"))
        preview = client.post(
            f"/api/admin/wholesale/imports/{started['import_id']}/preview",
            json={"sheet_index": 0, "header_row": started["header_row"], "mapping": started["mapping"]},
        ).json()
        assert preview["summary"]["rows"] == 3 and preview["summary"]["problems"] == 0
        client.post(f"/api/admin/wholesale/imports/{started['import_id']}/cancel")

        tiers = client.post("/api/admin/wholesale/tiers", json={"name": "تاجر كبير", "margin_percent": 8}).json()
        assert any(t["name"] == "تاجر كبير" for t in tiers)

        client.post("/api/staff/logout")
        client.cookies.clear()

        # Visitors browse at the default tier's prices and can price a cart, but can't order.
        me = client.get("/api/wholesale/me").json()
        assert me["logged_in"] is False and "sections" in me
        catalog = client.get("/api/wholesale/catalog")
        assert catalog.status_code == 200, catalog.text
        juice = next(i for i in catalog.json()["items"] if i["name"] == "عصير")
        assert set(juice) == {"id", "name", "section_id", "section_name", "image_url", "units"}
        assert juice["units"]["piece"]["price"] > 0
        cart = {"items": [{"product_id": juice["id"], "unit": "piece", "qty": 2}]}
        checked = client.post("/api/wholesale/cart/check", json=cart).json()
        assert checked["checkout"] is None
        assert checked["items"][str(juice["id"])]["units"]["piece"]["price"] == juice["units"]["piece"]["price"]
        assert client.post("/api/wholesale/orders", json=cart).status_code == 401
        assert client.get("/api/wholesale/orders").status_code == 401

        # A new account waiting for approval browses the same way and still can't order.
        signup = client.post("/api/wholesale/auth/signup", json={
            "name": "تاجر جديد", "phone": "01011112222", "password": "Trader-pass-1",
            "shop_name": "محل تجربة", "area": "المعادي", "address": "",
        })
        assert signup.status_code == 200, signup.text
        me = client.get("/api/wholesale/me").json()
        assert me["trader_status"] == "pending" and "sections" in me
        assert client.get("/api/wholesale/catalog").status_code == 200
        assert client.post("/api/wholesale/cart/check", json=cart).json()["checkout"] is None
        assert client.post("/api/wholesale/orders", json=cart).status_code == 403


def test_trader_login_remember_me(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(tmp_path / "ws-remember.db"))
    from app.main import app

    with TestClient(app) as client:
        assert client.post("/api/wholesale/auth/signup", json={
            "name": "تاجر", "phone": "01022223333", "password": "Trader-pass-1",
            "shop_name": "محل", "area": "المعادي", "address": "",
        }).status_code == 200
        client.post("/api/wholesale/auth/logout")

        kept = client.post("/api/wholesale/auth/login", json={"phone": "01022223333", "password": "Trader-pass-1"})
        assert kept.status_code == 200, kept.text
        assert "max-age=" in kept.headers["set-cookie"].lower()

        short = client.post("/api/wholesale/auth/login", json={"phone": "01022223333", "password": "Trader-pass-1", "remember": False})
        assert short.status_code == 200, short.text
        assert "max-age=" not in short.headers["set-cookie"].lower()
        assert client.get("/api/wholesale/me").json()["logged_in"] is True
