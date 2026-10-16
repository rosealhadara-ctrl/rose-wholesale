"""Phase 3: traders, orders split into supplier purchase orders, review, receipt, shortages."""

import pytest

from app.db import init_db
from app.wholesale_schema import ws_connect
from app import wholesale_orders as wo
from app import wholesale_service as ws


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-orders.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


def load(conn, alias, rows):
    supplier = ws.save_supplier(conn, {"alias": alias, "phone": "01000000001"})
    started = ws.start_import(conn, supplier["id"], "s.csv", csv_bytes(rows), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], started["header_row"], 0)
    ws.apply_import(conn, started["import_id"], {})
    return supplier


@pytest.fixture()
def shop(conn):
    ws.update_settings(conn, {"global_margin_percent": 10})
    a = load(conn, "أ", [["كود", "الصنف", "قطعة", "دستة"], ["A1", "عصير", "10", "100"], ["A2", "بسكويت", "5", ""]])
    b = load(conn, "ب", [["كود", "الصنف", "قطعة", "كرتونة", "عدد كرتونة"], ["B1", "زيت", "50", "1100", "24"]])
    products = {p["code"]: p["id"] for p in ws.list_products(conn)["items"]}
    customers = [r[0] for r in conn.execute("SELECT id FROM customers ORDER BY id")]
    return {"a": a, "b": b, "p": products, "c1": customers[0], "c2": customers[1]}


def test_trader_must_be_approved(conn, shop):
    c = shop["c1"]
    with pytest.raises(ws.WholesaleError) as err:
        wo.submit_order(conn, c, [{"product_id": shop["p"]["A1"], "unit": "piece", "qty": 1}])
    assert err.value.status == 403
    with pytest.raises(ws.WholesaleError):
        wo.apply_as_trader(conn, c, {"shop_name": "", "area": "x"})
    assert wo.apply_as_trader(conn, c, {"shop_name": "محل النور", "area": "شبرا"})["status"] == "pending"
    assert [t["name"] for t in wo.list_trader_accounts(conn, "pending")]
    wo.decide_trader(conn, c, "approved", None, None)
    assert wo.trader_status(conn, c)["status"] == "approved"
    assert wo.unread_count(conn, c) == 1
    # search finds any Rose customer, and the admin can add one directly
    found = wo.list_trader_accounts(conn, "search", "سارة")
    assert found and found[0]["status"] == "none"
    wo.decide_trader(conn, shop["c2"], "approved", None, None)
    wo.decide_trader(conn, shop["c2"], "suspended", None, None)
    with pytest.raises(ws.WholesaleError):
        wo.submit_order(conn, shop["c2"], [{"product_id": shop["p"]["A1"], "unit": "piece", "qty": 1}])


def test_catalog_hides_supplier_data(conn, shop):
    c = shop["c1"]
    wo.decide_trader(conn, c, "approved", None, None)
    data = wo.catalog(conn, c)
    assert data["total"] == 3
    juice = next(i for i in data["items"] if i["name"] == "عصير")
    assert juice["units"]["piece"]["price"] == 11.0 and juice["units"]["carton"] is None
    assert set(juice) == {"id", "name", "section_id", "section_name", "image_url", "units"}
    assert set(juice["units"]["piece"]) == {"price", "pieces", "per_piece", "saving_percent"}
    assert wo.catalog(conn, c, q="زيت")["total"] == 1


def test_full_order_flow(conn, shop):
    c, p = shop["c1"], shop["p"]
    wo.decide_trader(conn, c, "approved", None, None)
    order = wo.submit_order(conn, c, [
        {"product_id": p["A1"], "unit": "piece", "qty": 10, "note": "برتقال"},
        {"product_id": p["A1"], "unit": "piece", "qty": 5},
        {"product_id": p["A2"], "unit": "piece", "qty": 20},
        {"product_id": p["B1"], "unit": "carton", "qty": 2},
    ], note="بسرعة لو سمحت")
    assert order["number"] == 1000 + order["id"]
    assert len(order["items"]) == 3  # same product + unit merged
    juice = next(i for i in order["items"] if i["name"] == "عصير")
    assert juice["qty"] == 15 and juice["note"] == "برتقال"
    assert order["items_total"] == 15 * 11 + 20 * 5.5 + 2 * 1210

    # prices are locked: a new supplier price does not change the order
    load_again = ws.start_import(conn, shop["a"]["id"], "s.csv", csv_bytes([["كود", "الصنف", "قطعة", "دستة"], ["A1", "عصير", "20", "100"], ["A2", "بسكويت", "5", ""]]), None)
    ws.preview_import(conn, load_again["import_id"], load_again["mapping"], 1, 0)
    ws.apply_import(conn, load_again["import_id"], {})
    assert wo.customer_order(conn, c, order["id"])["items_total"] == order["items_total"]

    admin = wo.admin_order(conn, order["id"])
    pos = admin["purchase_orders"]
    assert [po["number"] for po in pos] == [f"{order['number']}-أ", f"{order['number']}-ب"]
    assert "عصير" in pos[0]["message"] and "برتقال" in pos[0]["message"]
    assert pos[0]["whatsapp_phone"] == "201000000001"
    wo.mark_po_sent(conn, pos[0]["id"])

    items = {i["name"]: i for po in pos for i in po["items"]}
    with pytest.raises(ws.WholesaleError):
        wo.confirm_review(conn, order["id"])  # decisions missing
    with pytest.raises(ws.WholesaleError):
        wo.set_item_decision(conn, order["id"], items["عصير"]["id"], "partial", 15)
    wo.set_item_decision(conn, order["id"], items["عصير"]["id"], "partial", 12)
    wo.set_item_decision(conn, order["id"], items["بسكويت"]["id"], "unavailable")
    wo.po_all_available(conn, order["id"], pos[1]["id"])
    assert all(po["status"] == "confirmed" for po in wo.admin_order(conn, order["id"])["purchase_orders"])

    reviewed = wo.confirm_review(conn, order["id"])
    assert reviewed["status"] == "reviewed"
    assert reviewed["final_total"] == 12 * 11 + 2 * 1210
    mine = wo.customer_order(conn, c, order["id"])
    states = {i["name"]: i["state"] for i in mine["items"]}
    assert states == {"عصير": "partial", "بسكويت": "unavailable", "زيت": "available"}
    assert not mine["can_cancel"]
    shortages = wo.customer_shortages(conn, c)
    assert {(s["name"], s["qty"]) for s in shortages} == {("عصير", 3), ("بسكويت", 20)}

    # receiving: supplier ب delivers one carton short
    po_a, po_b = reviewed["purchase_orders"]
    oil = po_b["items"][0]
    wo.receive_po(conn, po_b["id"], {str(oil["id"]): 1})
    assert wo.customer_order(conn, c, order["id"])["status"] == "reviewed"
    done = wo.receive_po(conn, po_a["id"], {})
    assert done["status"] == "received"
    assert done["final_total"] == 12 * 11 + 1 * 1210
    assert any(s["name"] == "زيت" and s["source"] == "receipt" for s in wo.customer_shortages(conn, c))
    titles = [n["title"] for n in wo.notifications(conn, c)["items"]]
    assert any("اتراجع" in t for t in titles) and not any("بيتجهز" in t for t in titles)

    # shortages page groups by product; "available" notifies the trader
    admin_short = wo.admin_shortages(conn)
    biscuits = next(g for g in admin_short["products"] if g["name"] == "بسكويت")
    assert biscuits["customers"] == 1 and biscuits["suppliers"][0]["alias"] == "أ"
    assert wo.admin_shortages(conn, shop["b"]["id"])["products"][0]["name"] == "زيت"
    assert wo.mark_available(conn, p["A2"]) == 1
    available = next(s for s in wo.customer_shortages(conn, c) if s["name"] == "بسكويت")
    assert available["status"] == "available" and available["product"]["units"]["piece"]["price"] == 5.5

    wo.mark_all_read(conn, c)
    assert wo.unread_count(conn, c) == 0


def test_cancel_and_all_unavailable(conn, shop):
    c, p = shop["c1"], shop["p"]
    wo.decide_trader(conn, c, "approved", None, None)
    first = wo.submit_order(conn, c, [{"product_id": p["A1"], "unit": "dozen", "qty": 1}])
    assert wo.cancel_by_customer(conn, c, first["id"])["status"] == "cancelled"
    with pytest.raises(ws.WholesaleError):
        wo.cancel_by_customer(conn, shop["c2"], first["id"])

    second = wo.submit_order(conn, c, [{"product_id": p["A1"], "unit": "dozen", "qty": 1}])
    item = wo.admin_order(conn, second["id"])["purchase_orders"][0]["items"][0]
    wo.set_item_decision(conn, second["id"], item["id"], "unavailable")
    result = wo.confirm_review(conn, second["id"])
    assert result["status"] == "cancelled"
    assert wo.admin_counts(conn)["waiting_shortages"] == 1

    with pytest.raises(ws.WholesaleError) as err:
        wo.submit_order(conn, c, [{"product_id": p["B1"], "unit": "dozen", "qty": 1}])
    assert err.value.status == 409  # oil is not sold by the dozen
    ws.update_product(conn, p["A1"], {"is_visible": False})
    with pytest.raises(ws.WholesaleError):
        wo.submit_order(conn, c, [{"product_id": p["A1"], "unit": "piece", "qty": 1}])


def test_provision_requests(conn, shop):
    c = shop["c1"]
    wo.decide_trader(conn, c, "approved", None, None)
    wo.request_provision(conn, c, "صابون سائل", "ماركة معينة", "carton", 2)
    data = wo.admin_shortages(conn)
    assert data["requests"][0]["name"] == "صابون سائل"
    wo.close_request(conn, data["requests"][0]["id"], shop["p"]["A2"])
    mine = wo.customer_shortages(conn, c)
    assert mine[0]["status"] == "available" and mine[0]["product"]["name"] == "بسكويت"
    wo.request_provision(conn, c, "شيء نادر")
    request = wo.admin_shortages(conn)["requests"][0]
    wo.close_request(conn, request["id"], None, "مش هنقدر نوفره حاليًا")
    assert wo.notifications(conn, c)["items"][0]["body"] == "مش هنقدر نوفره حاليًا"
