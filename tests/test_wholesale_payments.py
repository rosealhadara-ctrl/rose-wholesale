"""Phase 4: checkout totals, transfers after review, credit (آجل), delivery and points."""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import init_db
from app.wholesale_schema import ws_connect
from app import wholesale_orders as wo
from app import wholesale_payments as wp
from app import wholesale_points as wpts
from app import wholesale_service as ws


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-pay.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


@pytest.fixture()
def shop(conn):
    ws.update_settings(conn, {"global_margin_percent": 0})
    supplier = ws.save_supplier(conn, {"alias": "أ"})
    started = ws.start_import(conn, supplier["id"], "s.csv", csv_bytes([["كود", "الصنف", "قطعة"], ["A1", "عصير", "100"], ["A2", "بسكويت", "50"]]), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    ws.apply_import(conn, started["import_id"], {})
    products = {p["code"]: p["id"] for p in ws.list_products(conn)["items"]}
    customer = conn.execute("SELECT id FROM customers ORDER BY id LIMIT 1").fetchone()[0]
    wo.decide_trader(conn, customer, "approved", None, None)
    return {"p": products, "c": customer}


def review_all(conn, order_id, unavailable=()):
    data = wo.admin_order(conn, order_id)
    for po in data["purchase_orders"]:
        for item in po["items"]:
            wo.set_item_decision(conn, order_id, item["id"], "unavailable" if item["name"] in unavailable else "available")
    return wo.confirm_review(conn, order_id)


def receive_all(conn, order_id):
    for po in wo.admin_order(conn, order_id)["purchase_orders"]:
        if po["status"] not in ("received", "cancelled"):
            wo.receive_po(conn, po["id"], {})


def test_checkout_min_order_delivery_and_fees(conn, shop):
    c = shop["c"]
    ws.update_settings(conn, {"min_order_total": 500, "delivery_fee": 40})
    wp.update_method(conn, "wallet", {"is_enabled": True, "account": "01000000000", "fee_type": "percent", "fee_value": 1})
    wp.update_method(conn, "cash", {"max_amount": 1000})
    info = wp.checkout(conn, c, 300)
    assert info["below_min"] and info["delivery"] == 40
    with pytest.raises(ws.WholesaleError):
        wo.submit_order(conn, c, [{"product_id": shop["p"]["A1"], "unit": "piece", "qty": 3}])
    info = wp.checkout(conn, c, 1000)
    methods = {m["code"]: m for m in info["methods"]}
    assert set(methods) == {"cash", "wallet"}  # credit hidden without a limit, instapay off
    assert methods["wallet"]["fee"] == 10.4 and methods["wallet"]["grand"] == 1050.4
    assert not methods["cash"]["available"]  # 1040 > cash limit 1000
    with pytest.raises(ws.WholesaleError):
        wo.submit_order(conn, c, [{"product_id": shop["p"]["A1"], "unit": "piece", "qty": 10}], payment_method="cash")
    order = wo.submit_order(conn, c, [{"product_id": shop["p"]["A1"], "unit": "piece", "qty": 10}], payment_method="wallet")
    assert order["payment"]["grand"] == 1050.4 and order["payment"]["status"] == "unpaid"
    assert not order["payment"]["can_pay"]  # pays after review
    with pytest.raises(ws.WholesaleError):
        wp.update_method(conn, "instapay", {"is_enabled": True})  # needs an address first
    wp.update_method(conn, "wallet", {"fee_payer": "store"})
    assert wp.checkout(conn, c, 1000)["methods"][-1]["grand"] == 1040


def test_transfer_after_review_then_delivery_and_points(conn, shop):
    c, p = shop["c"], shop["p"]
    ws.update_settings(conn, {"points_egp_per_point": 50})
    wp.update_method(conn, "instapay", {"is_enabled": True, "account": "rose@instapay", "fee_type": "fixed", "fee_value": 5})
    order = wo.submit_order(conn, c, [
        {"product_id": p["A1"], "unit": "piece", "qty": 3},
        {"product_id": p["A2"], "unit": "piece", "qty": 2},
    ], payment_method="instapay")
    with pytest.raises(ws.WholesaleError):
        wp.submit_transfer(conn, c, "order", "instapay", None, "x.jpg", order_id=order["id"])
    review_all(conn, order["id"], unavailable=("بسكويت",))
    view = wo.customer_order(conn, c, order["id"])["payment"]
    assert view["can_pay"] and view["to_pay"] == 305.0  # 300 + fixed fee 5
    wp.submit_transfer(conn, c, "order", "instapay", None, "x.jpg", "أحمد", order["id"])
    assert wo.customer_order(conn, c, order["id"])["payment"]["status"] == "pending"
    payment = wp.list_payments(conn)[0]
    with pytest.raises(ws.WholesaleError):
        wp.decide_payment(conn, payment["id"], False, "", None)  # reason required
    wp.decide_payment(conn, payment["id"], False, "الصورة مش واضحة", None)
    assert wo.customer_order(conn, c, order["id"])["payment"]["can_pay"]
    wp.submit_transfer(conn, c, "order", "instapay", None, "y.jpg", "", order["id"])
    wp.decide_payment(conn, wp.list_payments(conn)[0]["id"], True, "", None)
    assert wo.customer_order(conn, c, order["id"])["payment"]["status"] == "paid"

    with pytest.raises(ws.WholesaleError):
        wp.mark_delivered(conn, order["id"], None)  # goods not received yet
    receive_all(conn, order["id"])
    before = wpts.balance(conn, c)
    assert wp.mark_delivered(conn, order["id"], None)["points"] == 6  # 300 / 50
    after = wpts.balance(conn, c)
    assert after == before + 6
    assert wp.award_points(conn, dict(conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order["id"],)).fetchone())) == 0
    assert wo.customer_order(conn, c, order["id"])["status"] == "delivered"


def test_overpaid_after_receipt_shows_refund(conn, shop):
    c, p = shop["c"], shop["p"]
    wp.update_method(conn, "wallet", {"is_enabled": True, "account": "010", "fee_value": 0})
    order = wo.submit_order(conn, c, [{"product_id": p["A1"], "unit": "piece", "qty": 4}], payment_method="wallet")
    review_all(conn, order["id"])
    wp.submit_transfer(conn, c, "order", "wallet", None, "x.jpg", "", order["id"])
    wp.decide_payment(conn, wp.list_payments(conn)[0]["id"], True, "", None)
    po = wo.admin_order(conn, order["id"])["purchase_orders"][0]
    wo.receive_po(conn, po["id"], {str(po["items"][0]["id"]): 3})
    view = wo.admin_order(conn, order["id"])["payment"]
    assert view["grand"] == 300 and view["paid"] == 400 and view["refund_due"] == 100
    wp.mark_refunded(conn, order["id"], 100)
    assert wo.admin_order(conn, order["id"])["payment"]["refund_due"] == 0
    wp.mark_delivered(conn, order["id"], None)


def test_credit_limit_charge_payment_and_overdue(conn, shop):
    c, p = shop["c"], shop["p"]
    wp.update_method(conn, "credit", {"is_enabled": True})
    assert "credit" not in {m["code"] for m in wp.checkout(conn, c, 100)["methods"]}
    wp.set_credit_account(conn, c, 1000, 15)
    first = wo.submit_order(conn, c, [{"product_id": p["A1"], "unit": "piece", "qty": 6}], payment_method="credit")
    assert wp.credit_summary(conn, c)["available"] == 400  # 600 reserved by the open order
    with pytest.raises(ws.WholesaleError):
        wo.submit_order(conn, c, [{"product_id": p["A1"], "unit": "piece", "qty": 5}], payment_method="credit")
    review_all(conn, first["id"])
    receive_all(conn, first["id"])
    wp.mark_delivered(conn, first["id"], None)
    credit = wp.credit_summary(conn, c, with_entries=True)
    assert credit["balance"] == 600 and credit["reserved"] == 0 and credit["available"] == 400
    assert credit["entries"][0]["kind"] == "charge" and credit["status"] == "ok"

    # trader pays part by transfer, admin records cash for the rest
    wp.update_method(conn, "wallet", {"is_enabled": True, "account": "010"})
    with pytest.raises(ws.WholesaleError):
        wp.submit_transfer(conn, c, "credit", "wallet", 700, "x.jpg")
    wp.submit_transfer(conn, c, "credit", "wallet", 200, "x.jpg")
    wp.decide_payment(conn, wp.list_payments(conn)[0]["id"], True, "", None)
    assert wp.credit_summary(conn, c)["balance"] == 400

    # make the charge overdue: new orders are blocked
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    conn.execute("UPDATE ws_credit_entries SET due_date = ? WHERE kind = 'charge'", (past,))
    summary = wp.credit_summary(conn, c)
    assert summary["overdue"] and summary["status"] == "overdue"
    with pytest.raises(ws.WholesaleError) as err:
        wo.submit_order(conn, c, [{"product_id": p["A2"], "unit": "piece", "qty": 1}])
    assert err.value.status == 403
    wp.record_credit_entry(conn, c, "payment", 400, "كاش في المحل", None)
    assert not wp.credit_summary(conn, c)["overdue"]
    wo.submit_order(conn, c, [{"product_id": p["A2"], "unit": "piece", "qty": 1}])
    assert wp.credit_accounts(conn)[0]["balance"] == 0


def test_change_method_before_payment(conn, shop):
    c, p = shop["c"], shop["p"]
    wp.update_method(conn, "wallet", {"is_enabled": True, "account": "010", "fee_value": 2})
    order = wo.submit_order(conn, c, [{"product_id": p["A1"], "unit": "piece", "qty": 1}], payment_method="wallet")
    assert order["payment"]["grand"] == 102
    wp.change_method(conn, c, dict(conn.execute("SELECT * FROM ws_orders WHERE id = ?", (order["id"],)).fetchone()), "cash")
    changed = wo.customer_order(conn, c, order["id"])["payment"]
    assert changed["method"] == "cash" and changed["grand"] == 100 and changed["status"] == "cod"
