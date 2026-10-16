"""Phase 7: returns (cash only, points back, credit orders), ratings, complaints."""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import init_db
from app.wholesale_schema import ws_connect
from app import wholesale_delivery as wd
from app import wholesale_orders as wo
from app import wholesale_payments as wp
from app import wholesale_picking as wk
from app import wholesale_returns as wr
from app import wholesale_points as wpts
from app import wholesale_service as ws


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-returns.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


@pytest.fixture()
def delivered(conn):
    ws.update_settings(conn, {"global_margin_percent": 0, "points_egp_per_point": 10})
    supplier = ws.save_supplier(conn, {"alias": "أ"})
    started = ws.start_import(conn, supplier["id"], "s.csv", csv_bytes([["كود", "الصنف", "قطعة"], ["A1", "عصير", "10"], ["A2", "بسكويت", "5"]]), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    ws.apply_import(conn, started["import_id"], {})
    p = {x["code"]: x["id"] for x in ws.list_products(conn)["items"]}
    customer = conn.execute("SELECT id FROM customers ORDER BY id LIMIT 1").fetchone()[0]
    wo.decide_trader(conn, customer, "approved", None, None)
    picker = wk.create_picker(conn, "منى", "01099998888", "both")

    def make(method="cash"):
        order = wo.submit_order(conn, customer, [
            {"product_id": p["A1"], "unit": "piece", "qty": 10},
            {"product_id": p["A2"], "unit": "piece", "qty": 20},
        ], payment_method=method)
        oid = order["id"]
        for po in wo.admin_order(conn, oid)["purchase_orders"]:
            wo.po_all_available(conn, oid, po["id"])
        wo.confirm_review(conn, oid)
        for po in wo.admin_order(conn, oid)["purchase_orders"]:
            wo.receive_po(conn, po["id"], {})
        view = wk.start_pick(conn, oid, picker["id"])
        for item in view["items"]:
            wk.mark_item(conn, oid, item["id"], picker["id"], "ready")
        wk.finish_pick(conn, oid, picker["id"])
        wp.mark_delivered(conn, oid, None)
        return oid

    return {"make": make, "customer": customer, "picker": picker}


def balance(conn, customer):
    return wpts.balance(conn, customer)


def test_full_return_with_driver_and_points(conn, delivered):
    c, picker = delivered["customer"], delivered["picker"]
    oid = delivered["make"]()  # 10*10 + 20*5 = 200 -> 20 points
    before = balance(conn, c)
    info = wr.returnable(conn, c, oid)
    assert info["open"] and info["days"] == 15
    items = {i["name"]: i for i in info["items"]}
    with pytest.raises(ws.WholesaleError):
        wr.request_return(conn, c, oid, [{"item_id": items["عصير"]["id"], "qty": 11, "reason": "damaged"}])
    ret = wr.request_return(conn, c, oid, [
        {"item_id": items["عصير"]["id"], "qty": 4, "reason": "damaged"},
        {"item_id": items["بسكويت"]["id"], "qty": 2, "reason": "wrong_item"},
    ], note="العلبة مفتوحة")
    assert ret["status"] == "requested" and ret["max_refund"] == 50
    assert wr.returnable(conn, c, oid)["items"][0]["returnable"] == 6

    with pytest.raises(ws.WholesaleError):
        wr.decide(conn, ret["id"], False, "driver", None, "", None)  # reason required
    with pytest.raises(ws.WholesaleError):
        wr.decide(conn, ret["id"], True, "driver", None, "", None)  # driver required
    wr.decide(conn, ret["id"], True, "driver", picker["id"], "", None)
    assert wr.driver_pickups(conn, picker["id"])[0]["order_number"] == wo.order_number(oid)
    assert wr.to_inspect(conn) == []  # still with the trader
    wr.picked_up(conn, ret["id"], picker["id"])
    to_check = wr.to_inspect(conn)[0]
    juice = next(i for i in to_check["items"] if i["name"] == "عصير")
    with pytest.raises(ws.WholesaleError):
        wr.inspect(conn, ret["id"], {str(juice["id"]): {"sound": 3, "damaged": 2}}, picker["id"])
    result = wr.inspect(conn, ret["id"], {str(juice["id"]): {"sound": 1, "damaged": 2}}, picker["id"])
    assert result["refund"] == 3 * 10 + 2 * 5  # one juice never arrived

    with pytest.raises(ws.WholesaleError):
        wr.refund(conn, ret["id"], 41, None)
    done = wr.refund(conn, ret["id"], 40, None)
    assert done["status"] == "refunded" and done["refund_cash"] == 40 and done["points_removed"] == 4  # 40/200 of 20
    assert balance(conn, c) == before - 4
    titles = [n["title"] for n in wo.notifications(conn, c)["items"]]
    assert any("اتقفل" in t for t in titles) and any("اتفحصت" in t for t in titles)
    errors = next(p for p in wk.list_pickers(conn) if p["id"] == picker["id"])["errors"]
    assert errors == 1  # "wrong item" counts against the picker


def test_store_pickup_reject_and_window(conn, delivered):
    c = delivered["customer"]
    oid = delivered["make"]()
    item = wr.returnable(conn, c, oid)["items"][0]
    ret = wr.request_return(conn, c, oid, [{"item_id": item["id"], "qty": 1, "reason": "not_needed"}])
    wr.decide(conn, ret["id"], False, "driver", None, "الصنف مش من محلنا", None)
    assert wr.customer_returns(conn, c, oid)[0]["status_label"] == "اترفض"
    assert wr.returnable(conn, c, oid)["items"][0]["returnable"] == 10  # rejected returns free the quantity
    ret2 = wr.request_return(conn, c, oid, [{"item_id": item["id"], "qty": 1, "reason": "near_expiry"}])
    wr.decide(conn, ret2["id"], True, "store", None, "", None)
    assert wr.to_inspect(conn)[0]["id"] == ret2["id"]  # trader brings it to the store
    old = (datetime.now(timezone.utc) - timedelta(days=16)).isoformat(timespec="seconds")
    conn.execute("UPDATE ws_orders SET delivered_at = ? WHERE id = ?", (old, oid))
    assert not wr.returnable(conn, c, oid)["open"]
    with pytest.raises(ws.WholesaleError):
        wr.request_return(conn, c, oid, [{"item_id": item["id"], "qty": 1, "reason": "damaged"}])
    ws.update_settings(conn, {"return_days": 30})
    assert wr.returnable(conn, c, oid)["open"]


def test_credit_order_refund_reduces_balance(conn, delivered):
    c, picker = delivered["customer"], delivered["picker"]
    wp.update_method(conn, "credit", {"is_enabled": True})
    wp.set_credit_account(conn, c, 5000, 30)
    oid = delivered["make"]("credit")
    assert wp.credit_summary(conn, c)["balance"] == 200
    item = wr.returnable(conn, c, oid)["items"][0]
    ret = wr.request_return(conn, c, oid, [{"item_id": item["id"], "qty": 5, "reason": "damaged"}])
    wr.decide(conn, ret["id"], True, "store", None, "", None)
    wr.inspect(conn, ret["id"], {}, picker["id"])  # default: all sound
    done = wr.refund(conn, ret["id"], 50, None)
    assert done["refund_credit"] == 50 and done["refund_cash"] == 0
    assert wp.credit_summary(conn, c)["balance"] == 150


def test_rating_and_feedback(conn, delivered):
    c = delivered["customer"]
    oid = delivered["make"]()
    wr.rate(conn, c, oid, 4, ["سرعة التوصيل", "حاجة مش في القايمة"], "الكراتين كانت مبلولة")
    with pytest.raises(ws.WholesaleError):
        wr.rate(conn, c, oid, 5, [], "")
    assert wr.rating_for(conn, oid)["liked"] == ["سرعة التوصيل"]
    report = wr.ratings_report(conn)
    assert report["average"] == 4 and report["distribution"][4] == 1
    wr.send_feedback(conn, c, "complaint", "المندوب اتأخر ساعتين", oid)
    wr.send_feedback(conn, c, "suggestion", "ضيفوا أصناف منظفات أكتر")
    inbox = wr.admin_feedback(conn)
    assert len(inbox["items"]) == 3  # the rating's text lands in the inbox too
    complaint = next(f for f in inbox["items"] if f["kind"] == "complaint")
    wr.reply_feedback(conn, complaint["id"], "آسفين جدًا، اتكلمنا مع المندوب", True)
    mine = {f["kind"]: f for f in wr.my_feedback(conn, c)}
    assert mine["complaint"]["status"] == "resolved" and mine["complaint"]["reply"].startswith("آسفين")
    assert "rating" not in mine
    assert wo.notifications(conn, c)["items"][0]["title"] == "رد على شكوتك"
    assert len(wr.admin_feedback(conn, status="open")["items"]) == 2
