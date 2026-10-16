"""Phase 5: prep employees, picking with lock and timer, receiving from the phone."""

import pytest

from app.db import init_db
from app.wholesale_schema import ws_connect
from app import wholesale_orders as wo
from app import wholesale_picking as wk
from app import wholesale_service as ws


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-pick.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


@pytest.fixture()
def received_order(conn):
    ws.update_settings(conn, {"global_margin_percent": 0})
    supplier = ws.save_supplier(conn, {"alias": "أ"})
    started = ws.start_import(conn, supplier["id"], "s.csv", csv_bytes([["كود", "الصنف", "قطعة"], ["A1", "عصير", "10"], ["A2", "بسكويت", "5"], ["A3", "شاي", "20"]]), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    ws.apply_import(conn, started["import_id"], {})
    p = {x["code"]: x["id"] for x in ws.list_products(conn)["items"]}
    customer = conn.execute("SELECT id FROM customers ORDER BY id LIMIT 1").fetchone()[0]
    wo.decide_trader(conn, customer, "approved", None, None)
    order = wo.submit_order(conn, customer, [
        {"product_id": p["A1"], "unit": "piece", "qty": 10, "note": "برتقال"},
        {"product_id": p["A2"], "unit": "piece", "qty": 4},
        {"product_id": p["A3"], "unit": "piece", "qty": 2},
    ])
    data = wo.admin_order(conn, order["id"])
    for item in data["purchase_orders"][0]["items"]:
        decision = "unavailable" if item["name"] == "شاي" else "available"
        wo.set_item_decision(conn, order["id"], item["id"], decision)
    wo.confirm_review(conn, order["id"])
    return {"order": order, "customer": customer, "po": data["purchase_orders"][0]}


def test_picker_login_and_lockout(conn):
    created = wk.create_picker(conn, "منى", "0100 123 4567")
    assert len(created["pin"]) == 4 and created["phone"] == "01001234567"
    with pytest.raises(ws.WholesaleError):
        wk.create_picker(conn, "تانية", "01001234567")
    token = wk.login(conn, "201001234567", created["pin"])
    assert wk.current_picker(conn, token)["name"] == "منى"
    wrong = "0000" if created["pin"] != "0000" else "1111"
    for _ in range(wk.MAX_FAILED):
        with pytest.raises(ws.WholesaleError):
            wk.login(conn, "01001234567", wrong)
    with pytest.raises(ws.WholesaleError) as err:
        wk.login(conn, "01001234567", created["pin"])
    assert err.value.status == 429
    fresh = wk.reset_pin(conn, created["id"])
    assert wk.current_picker(conn, token) is None  # new code signs out old sessions
    assert wk.login(conn, "01001234567", fresh["pin"])
    wk.set_active(conn, created["id"], False)
    with pytest.raises(ws.WholesaleError):
        wk.login(conn, "01001234567", fresh["pin"])


def test_receive_from_phone_then_pick(conn, received_order):
    order_id, customer = received_order["order"]["id"], received_order["customer"]
    a = wk.create_picker(conn, "منى", "01001234567")
    b = wk.create_picker(conn, "سارة", "01111111111")

    receiving = wk.receiving_list(conn)
    assert len(receiving) == 1 and all("price" not in str(k) for k in receiving[0]["items"][0])
    assert [i["name"] for i in receiving[0]["items"]] == ["عصير", "بسكويت"]  # tea was unavailable
    juice = receiving[0]["items"][0]
    wk.receive_from_phone(conn, receiving[0]["id"], {str(juice["id"]): 9}, a["id"])
    assert wo.admin_order(conn, order_id)["status"] == "received"
    titles = [n["title"] for n in wo.notifications(conn, customer)["items"]]
    assert not any("بيتجهز" in t for t in titles)

    q = wk.queue(conn, a["id"])
    assert q["orders"][0]["number"] == wo.order_number(order_id) and q["orders"][0]["has_notes"]
    view = wk.start_pick(conn, order_id, a["id"])
    assert view["mine"] and [i["qty"] for i in view["items"]] == [9, 4]
    with pytest.raises(ws.WholesaleError):
        wk.start_pick(conn, order_id, b["id"])  # locked to منى
    assert wk.queue(conn, b["id"])["orders"][0]["locked_by"] == "منى"
    assert wo.customer_order(conn, customer, order_id)["status_label"] == "جاري تجهيز طلبك"
    assert "بدأنا نجهّز" in wo.notifications(conn, customer)["items"][0]["title"]

    with pytest.raises(ws.WholesaleError):
        wk.finish_pick(conn, order_id, a["id"])
    items = {i["name"]: i for i in view["items"]}
    with pytest.raises(ws.WholesaleError):
        wk.mark_item(conn, order_id, items["عصير"]["id"], a["id"], "short", 9)
    wk.mark_item(conn, order_id, items["عصير"]["id"], a["id"], "ready")
    wk.mark_item(conn, order_id, items["بسكويت"]["id"], a["id"], "short", 3)
    done = wk.finish_pick(conn, order_id, a["id"])
    assert done["status"] == "ready" and done["total"] == 9 * 10 + 3 * 5

    message = wo.notifications(conn, customer)["items"][0]
    assert "جاهز" in message["title"] and "1 صنف جاهز" in message["body"] and "1 بعدد أقل" in message["body"]
    shortages = {(s["name"], s["source"], s["qty"]) for s in wo.customer_shortages(conn, customer)}
    assert ("بسكويت", "picking", 1) in shortages and ("عصير", "receipt", 1) in shortages
    stats = next(p for p in wk.list_pickers(conn) if p["id"] == a["id"])
    assert stats["today"] == 1 and stats["avg_seconds"] is not None
    from app import wholesale_payments as wp

    wp.mark_delivered(conn, order_id, None)
    assert wo.customer_order(conn, customer, order_id)["status"] == "delivered"


def test_release_and_suspend_free_the_order(conn, received_order):
    order_id = received_order["order"]["id"]
    for po in wo.admin_order(conn, order_id)["purchase_orders"]:
        wo.receive_po(conn, po["id"], {})
    a = wk.create_picker(conn, "منى", "01001234567")
    b = wk.create_picker(conn, "سارة", "01111111111")
    wk.start_pick(conn, order_id, a["id"])
    wk.release_pick(conn, order_id)
    wk.start_pick(conn, order_id, b["id"])
    wk.set_active(conn, b["id"], False)
    assert wo.admin_order(conn, order_id)["status"] == "received"


def test_failed_pins_survive_the_request_rollback(tmp_path, monkeypatch):
    """Each request runs in ws_connect(), which rolls back on errors; the lockout must still count."""
    from app.db import init_db
    from app.wholesale_schema import ws_connect
    from app import wholesale_picking as wk
    from app import wholesale_service as ws

    path = tmp_path / "lock.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as conn:
        p = wk.create_picker(conn, "منى", "01099998888", "picker")
    wrong = "1111" if p["pin"] != "1111" else "2222"
    for _ in range(wk.MAX_FAILED):
        with pytest.raises(ws.WholesaleError):
            with ws_connect() as conn:
                wk.login(conn, "01099998888", wrong)
    with pytest.raises(ws.WholesaleError) as err:
        with ws_connect() as conn:
            wk.login(conn, "01099998888", p["pin"])  # right PIN, but locked now
    assert err.value.status == 429
