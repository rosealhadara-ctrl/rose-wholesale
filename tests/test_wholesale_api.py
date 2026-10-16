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

        tiers = client.post("/api/admin/wholesale/tiers", json={"name": "تاجر كبير", "margin_percent": 8}).json()
        assert any(t["name"] == "تاجر كبير" for t in tiers)

        # Trader-facing routes need a Rose customer session.
        assert client.get("/api/wholesale/catalog").status_code == 401
        assert client.get("/api/wholesale/me").json() == {"logged_in": False}
