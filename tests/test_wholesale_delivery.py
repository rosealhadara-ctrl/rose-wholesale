"""Phase 6: delivery zones, drivers, trip with pickup code, cash from drivers."""

import pytest

from app.db import init_db
from app.wholesale_schema import ws_connect
from app import wholesale_delivery as wd
from app import wholesale_orders as wo
from app import wholesale_payments as wp
from app import wholesale_picking as wk
from app import wholesale_service as ws


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-delivery.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


@pytest.fixture()
def setup(conn):
    ws.update_settings(conn, {"global_margin_percent": 0, "delivery_fee": 50, "min_order_total": 100})
    supplier = ws.save_supplier(conn, {"alias": "أ"})
    started = ws.start_import(conn, supplier["id"], "s.csv", csv_bytes([["كود", "الصنف", "قطعة"], ["A1", "عصير", "10"]]), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    ws.apply_import(conn, started["import_id"], {})
    product = ws.list_products(conn)["items"][0]["id"]
    customer = conn.execute("SELECT id FROM customers ORDER BY id LIMIT 1").fetchone()[0]
    wo.apply_as_trader(conn, customer, {"shop_name": "محل النور", "area": "شبرا", "address": "شارع 10، جنب الجامع"})
    wo.decide_trader(conn, customer, "approved", None, None)
    return {"product": product, "customer": customer}


def ready_order(conn, setup, qty=10, method="cash"):
    order = wo.submit_order(conn, setup["customer"], [{"product_id": setup["product"], "unit": "piece", "qty": qty}], payment_method=method)
    data = wo.admin_order(conn, order["id"])
    for po in data["purchase_orders"]:
        wo.po_all_available(conn, order["id"], po["id"])
    wo.confirm_review(conn, order["id"])
    for po in wo.admin_order(conn, order["id"])["purchase_orders"]:
        wo.receive_po(conn, po["id"], {})
    picker = wk.create_picker(conn, f"م{order['id']}", f"0109999{order['id']:04d}")
    view = wk.start_pick(conn, order["id"], picker["id"])
    for item in view["items"]:
        wk.mark_item(conn, order["id"], item["id"], picker["id"], "ready")
    wk.finish_pick(conn, order["id"], picker["id"])
    return order["id"]


def test_zone_fee_and_minimum(conn, setup):
    c = setup["customer"]
    assert wp.checkout(conn, c, 150)["delivery"] == 50  # general setting
    zones = wd.save_zone(conn, {"name": "شبرا", "fee": 30, "min_order": 300})
    wd.set_trader_delivery(conn, c, zones[0]["id"])
    info = wp.checkout(conn, c, 150)
    assert info["delivery"] == 30 and info["zone"] == "شبرا" and info["below_min"]
    order = wo.submit_order(conn, c, [{"product_id": setup["product"], "unit": "piece", "qty": 30}])
    row = conn.execute("SELECT delivery_fee, delivery_address, zone_id FROM ws_orders WHERE id = ?", (order["id"],)).fetchone()
    assert row["delivery_fee"] == 30 and "جنب الجامع" in row["delivery_address"] and row["zone_id"] == zones[0]["id"]
    wd.save_zone(conn, {"is_active": False}, zones[0]["id"])
    assert wp.checkout(conn, c, 150)["delivery"] == 50


def test_trip_with_code_cash_and_settlement(conn, setup):
    order_id = ready_order(conn, setup)
    driver = wk.create_picker(conn, "كريم", "01222222222", "driver")
    picker_only = wk.create_picker(conn, "منى", "01011111111", "picker")
    with pytest.raises(ws.WholesaleError):
        wd.assign(conn, order_id, picker_only["id"])
    board = wd.board(conn)
    assert board["waiting"][0]["id"] == order_id and board["waiting"][0]["cash_due"] == 150
    code = wd.assign(conn, order_id, driver["id"], "خلال ساعة")["code"]
    assert len(code) == 4
    trips = wd.driver_trips(conn, driver["id"])["trips"]
    assert trips[0]["cash_due"] == 150 and trips[0]["address"].startswith("شبرا")

    wd.go_out(conn, order_id, driver["id"])
    seen = wo.customer_order(conn, setup["customer"], order_id)
    assert seen["status"] == "out_for_delivery" and seen["delivery"]["code"] == code
    assert seen["delivery"]["driver_phone"] == "01222222222"
    assert code in wo.notifications(conn, setup["customer"])["items"][0]["body"]
    with pytest.raises(ws.WholesaleError):
        wd.deliver(conn, order_id, driver["id"], code, 150)  # must press arrived first
    wd.arrived(conn, order_id, driver["id"])
    wrong = "0000" if code != "0000" else "1111"
    with pytest.raises(ws.WholesaleError):
        wd.deliver(conn, order_id, driver["id"], wrong, 150)
    with pytest.raises(ws.WholesaleError):
        wd.deliver(conn, order_id, driver["id"], code, None)  # cash not confirmed
    result = wd.deliver(conn, order_id, driver["id"], code, 150)
    assert result["cash"] == 150
    assert wo.customer_order(conn, setup["customer"], order_id)["status"] == "delivered"
    me = next(d for d in wd.drivers(conn) if d["id"] == driver["id"])
    assert me["cash_on_hand"] == 150 and me["delivered_today"] == 1
    settled = wd.settle_cash(conn, driver["id"], None)
    assert settled["amount"] == 150
    assert next(d for d in wd.drivers(conn) if d["id"] == driver["id"])["cash_on_hand"] == 0
    with pytest.raises(ws.WholesaleError):
        wd.settle_cash(conn, driver["id"], None)


def test_failed_trip_code_lock_and_reassign(conn, setup):
    order_id = ready_order(conn, setup)
    a = wk.create_picker(conn, "كريم", "01222222222", "driver")
    b = wk.create_picker(conn, "علي", "01233333333", "both")
    wd.assign(conn, order_id, a["id"])
    wd.arrived(conn, order_id, a["id"])  # arriving also marks the trip as out
    wd.fail(conn, order_id, a["id"], "المحل مقفول")
    board = wd.board(conn)
    assert board["failed"][0]["note"] == "المحل مقفول"
    assert "ماقدرناش نسلّم" in wo.notifications(conn, setup["customer"])["items"][0]["title"]
    code = wd.assign(conn, order_id, b["id"])["code"]
    wd.arrived(conn, order_id, b["id"])
    for _ in range(wd.MAX_CODE_ATTEMPTS):
        with pytest.raises(ws.WholesaleError):
            wd.deliver(conn, order_id, b["id"], "9" * 4 if code != "9999" else "8888", 150)
    with pytest.raises(ws.WholesaleError) as err:
        wd.deliver(conn, order_id, b["id"], code, 150)
    assert err.value.status == 423
    wk.set_active(conn, b["id"], False)  # suspending a driver frees the trip
    assert wd.board(conn)["waiting"][0]["id"] == order_id


def test_transfer_must_be_paid_before_going_out(conn, setup):
    wp.update_method(conn, "wallet", {"is_enabled": True, "account": "010"})
    order_id = ready_order(conn, setup, method="wallet")
    driver = wk.create_picker(conn, "كريم", "01222222222", "driver")
    wd.assign(conn, order_id, driver["id"])
    assert wd.board(conn)["active"][0]["blocked"]
    with pytest.raises(ws.WholesaleError):
        wd.go_out(conn, order_id, driver["id"])
    wp.submit_transfer(conn, setup["customer"], "order", "wallet", None, "x.jpg", "", order_id)
    wp.decide_payment(conn, wp.list_payments(conn)[0]["id"], True, "", None)
    trip = wd.go_out(conn, order_id, driver["id"])
    assert trip["cash_due"] == 0
    wd.arrived(conn, order_id, driver["id"])
    code = conn.execute("SELECT delivery_code FROM ws_orders WHERE id = ?", (order_id,)).fetchone()[0]
    assert wd.deliver(conn, order_id, driver["id"], code, None)["cash"] == 0


def test_wrong_codes_survive_the_request_rollback(conn, setup):
    """The pickup code is 4 digits: wrong tries must count even though each request rolls back on error."""
    order_id = ready_order(conn, setup)
    driver = wk.create_picker(conn, "كريم", "01222222222", "driver")
    code = wd.assign(conn, order_id, driver["id"])["code"]
    wd.arrived(conn, order_id, driver["id"])
    conn.commit()
    wrong = "0000" if code != "0000" else "1111"
    for _ in range(wd.MAX_CODE_ATTEMPTS):
        with pytest.raises(ws.WholesaleError):
            with ws_connect() as c:
                wd.deliver(c, order_id, driver["id"], wrong, 150)
    with pytest.raises(ws.WholesaleError) as err:
        with ws_connect() as c:
            wd.deliver(c, order_id, driver["id"], code, 150)
    assert err.value.status == 423
