"""Phase 8: Rose Plus (free delivery, part of the commission, picking priority) and the reports."""

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from app.db import init_db
from app.wholesale_schema import ws_connect
from app import wholesale_excel as wx
from app import wholesale_orders as wo
from app import wholesale_payments as wp
from app import wholesale_picking as wk
from app import wholesale_plus as wplus
from app import wholesale_reports as wrep
from app import wholesale_returns as wr
from app import wholesale_service as ws
from app.wholesale_xlsx import build_xlsx


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-plus.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


@pytest.fixture()
def shop(conn):
    ws.update_settings(conn, {"global_margin_percent": 0, "delivery_fee": 40})
    a = ws.save_supplier(conn, {"alias": "أ"})
    started = ws.start_import(conn, a["id"], "a.csv", csv_bytes([["كود", "الصنف", "قطعة"], ["A1", "عصير", "10"]]), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    ws.apply_import(conn, started["import_id"], {})
    b = ws.save_supplier(conn, {"alias": "ب"})
    started = ws.start_import(conn, b["id"], "b.csv", csv_bytes([["كود", "الصنف", "قطعة"], ["B1", "بسكويت", "5"]]), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    ws.apply_import(conn, started["import_id"], {})
    # margin applies after import: make sales price differ from cost
    ws.update_settings(conn, {"global_margin_percent": 20})
    products = {x["code"]: x["id"] for x in ws.list_products(conn)["items"]}
    customers = [r[0] for r in conn.execute("SELECT id FROM customers ORDER BY id LIMIT 2")]
    for c in customers:
        wo.decide_trader(conn, c, "approved", None, None)
    wp.update_method(conn, "wallet", {"is_enabled": True, "account": "01000000000", "fee_type": "percent", "fee_value": 2})
    picker = wk.create_picker(conn, "منى", "01099998888", "both")
    return {"p": products, "c": customers, "picker": picker}


def deliver(conn, oid, picker_id):
    for po in wo.admin_order(conn, oid)["purchase_orders"]:
        wo.po_all_available(conn, oid, po["id"])
    wo.confirm_review(conn, oid)
    for po in wo.admin_order(conn, oid)["purchase_orders"]:
        wo.receive_po(conn, po["id"], {})
    view = wk.start_pick(conn, oid, picker_id)
    for item in view["items"]:
        wk.mark_item(conn, oid, item["id"], picker_id, "ready")
    wk.finish_pick(conn, oid, picker_id)
    wp.mark_delivered(conn, oid, None)


def test_plus_settings_need_a_price(conn):
    with pytest.raises(ws.WholesaleError):
        ws.update_settings(conn, {"plus_enabled": True})
    s = ws.update_settings(conn, {"plus_enabled": True, "plus_price_month": 150})
    assert s["plus_enabled"] == "1"
    with pytest.raises(ws.WholesaleError):
        ws.update_settings(conn, {"plus_fee_share": 120})


def test_plus_request_activate_and_benefits(conn, shop):
    c = shop["c"][0]
    with pytest.raises(ws.WholesaleError):
        wplus.request_plus(conn, c, "month", "cash")  # Plus is off
    ws.update_settings(conn, {"plus_enabled": True, "plus_price_month": 150, "plus_price_quarter": 405})
    status = wplus.plus_status(conn, c)
    assert [p["code"] for p in status["plans"]] == ["month", "quarter"] and status["active"] is None
    with pytest.raises(ws.WholesaleError):
        wplus.request_plus(conn, c, "month", "wallet")  # transfer needs a screenshot
    req = wplus.request_plus(conn, c, "quarter", "cash")
    with pytest.raises(ws.WholesaleError):
        wplus.request_plus(conn, c, "month", "cash")  # one request at a time
    assert wo.admin_counts(conn)["plus_requests"] == 1
    before = wp.checkout(conn, c, 1000)
    assert before["delivery"] == 40 and before["plus"]["offer"] and before["plus"]["offer_delivery"] == 40
    sub = wplus.decide_plus(conn, req["id"], True, "", None)
    assert sub["status"] == "active" and sub["plan"] == "quarter"
    end = datetime.fromisoformat(sub["ends_at"]) - datetime.fromisoformat(sub["starts_at"])
    assert end == timedelta(days=90)

    co = wp.checkout(conn, c, 1000)
    wallet = next(m for m in co["methods"] if m["code"] == "wallet")
    assert co["delivery"] == 0 and co["plus"]["active"] and co["plus"]["delivery_saved"] == 40
    assert wallet["fee"] == 10 and wallet["fee_saved"] == 10  # 2% of 1000 = 20, Plus pays half

    # renewal starts when the current one ends
    renew = wplus.grant_plus(conn, c, "month", None)
    assert renew["starts_at"] == sub["ends_at"]
    titles = [n["title"] for n in wo.notifications(conn, c)["items"]]
    assert "اشتراك روز بلس اتفعّل" in titles and "اتجدد اشتراك روز بلس" in titles


def test_plus_order_locked_benefits_and_priority(conn, shop):
    ws.update_settings(conn, {"plus_enabled": True, "plus_price_month": 150})
    plus_c, normal_c = shop["c"]
    wplus.grant_plus(conn, plus_c, "month", None)
    normal = wo.submit_order(conn, normal_c, [{"product_id": shop["p"]["A1"], "unit": "piece", "qty": 100}], payment_method="wallet")
    plus = wo.submit_order(conn, plus_c, [{"product_id": shop["p"]["A1"], "unit": "piece", "qty": 100}], payment_method="wallet")
    n, p = wo.admin_order(conn, normal["id"]), wo.admin_order(conn, plus["id"])
    assert n["delivery_fee"] == 40 and not n["is_plus"]
    assert p["delivery_fee"] == 0 and p["is_plus"] and p["plus_delivery_saved"] == 40
    assert p["payment_fee"] == round(n["payment_fee"] / 2 - 0.4, 2)  # half of 2% on products only (no delivery)
    for oid in (normal["id"], plus["id"]):
        for po in wo.admin_order(conn, oid)["purchase_orders"]:
            wo.po_all_available(conn, oid, po["id"])
        wo.confirm_review(conn, oid)
        for po in wo.admin_order(conn, oid)["purchase_orders"]:
            wo.receive_po(conn, po["id"], {})
    q = wk.queue(conn, shop["picker"]["id"])["orders"]
    assert q[0]["id"] == plus["id"] and q[0]["priority"] and not q[1]["priority"]
    # the subscription ends; the order keeps its benefits
    conn.execute("UPDATE ws_plus_subscriptions SET ends_at = ?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"),))
    view = wo.customer_order(conn, plus_c, plus["id"])
    assert view["payment"]["is_plus"] and view["payment"]["plus_saved"] > 40
    assert wp.checkout(conn, plus_c, 1000)["delivery"] == 40
    assert wplus.saved_total(conn, plus_c) == view["payment"]["plus_saved"]


def test_plus_reminder_and_cancel(conn, shop):
    ws.update_settings(conn, {"plus_enabled": True, "plus_price_month": 150})
    c = shop["c"][0]
    sub = wplus.grant_plus(conn, c, "month", None)
    conn.execute("UPDATE ws_plus_subscriptions SET ends_at = ?", ((datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec="seconds"),))
    wplus.plus_status(conn, c)
    wplus.plus_status(conn, c)
    titles = [n["title"] for n in wo.notifications(conn, c)["items"]]
    assert titles.count("اشتراك روز بلس قرب يخلص") == 1
    report = wplus.admin_plus(conn)
    assert report["stats"]["active"] == 1 and report["stats"]["ending_soon"] == 1 and report["stats"]["revenue_month"] == 150
    wplus.cancel_plus(conn, sub["id"], None)
    assert wplus.active_plus(conn, c) is None


def test_store_and_supplier_reports(conn, shop):
    c1, c2 = shop["c"]
    picker = shop["picker"]["id"]
    o1 = wo.submit_order(conn, c1, [{"product_id": shop["p"]["A1"], "unit": "piece", "qty": 10},
                                    {"product_id": shop["p"]["B1"], "unit": "piece", "qty": 4}], payment_method="cash")
    deliver(conn, o1["id"], picker)
    o2 = wo.submit_order(conn, c2, [{"product_id": shop["p"]["A1"], "unit": "piece", "qty": 5},
                                    {"product_id": shop["p"]["B1"], "unit": "piece", "qty": 2}], payment_method="cash")
    order = wo.admin_order(conn, o2["id"])
    po_b = next(po for po in order["purchase_orders"] if po["alias"] == "ب")
    po_a = next(po for po in order["purchase_orders"] if po["alias"] == "أ")
    wo.po_all_available(conn, o2["id"], po_a["id"])
    wo.set_item_decision(conn, o2["id"], po_b["items"][0]["id"], "unavailable")

    # a damaged return on order 1 (supplier أ)
    item = next(i for i in wr.returnable(conn, c1, o1["id"])["items"] if i["name"] == "عصير")
    ret = wr.request_return(conn, c1, o1["id"], [{"item_id": item["id"], "qty": 2, "reason": "damaged"}])
    wr.decide(conn, ret["id"], True, "store", None, "", None)
    juice = wr.to_inspect(conn)[0]["items"][0]
    wr.inspect(conn, ret["id"], {str(juice["id"]): {"sound": 0, "damaged": 2}}, picker)
    wr.refund(conn, ret["id"], 24, None)

    rep = wrep.store_report(conn, "month")
    m = rep["metrics"]
    assert m["orders"] == 1 and m["sales"] == 10 * 12 + 4 * 6  # 20% margin
    assert m["profit"] == 10 * 2 + 4 * 1 and m["returns"] == 24 and m["net"] == 144 - 24
    assert m["new_orders"] == 2 and rep["compare"]["sales"]["change"] is None
    assert sum(x["sales"] for x in rep["chart"]) == 144
    assert rep["top_products"][0]["name"] == "عصير" and rep["top_traders"][0]["orders"] == 1
    assert next(u for u in rep["by_unit"] if u["unit"] == "piece")["share"] == 100
    day = wrep.store_report(conn, "day")
    assert day["metrics"]["sales"] == 144 and len(day["chart"]) == 24
    assert wrep.store_report(conn, "year")["metrics"]["orders"] == 1
    assert wrep.store_report(conn, "month", "2020-01")["metrics"]["orders"] == 0
    with pytest.raises(ws.WholesaleError):
        wrep.store_report(conn, "week")
    with pytest.raises(ws.WholesaleError):
        wrep.store_report(conn, "month", "2026-13")

    sup = wrep.supplier_report(conn, "month")
    a = next(s for s in sup["suppliers"] if s["alias"] == "أ")
    assert a["sales"] == 120 and a["cost"] == 100 and a["profit"] == 20 and a["damaged_qty"] == 2 and a["damaged_cost"] == 20
    b = next(s for s in sup["suppliers"] if s["alias"] == "ب")
    assert b["unavailable"] == 1 and b["lines_reviewed"] == 2 and b["fill_rate"] == 50
    detail = wrep.supplier_report(conn, "month", "", a["id"])["detail"]
    assert detail["products"][0]["qty"] == 10 and detail["damaged"][0]["qty"] == 2

    # Excel files open and read back
    data = build_xlsx(wrep.store_report_rows(rep))
    assert zipfile.ZipFile(io.BytesIO(data)).testzip() is None
    sheets = wx.read_sheet_file("r.xlsx", data)
    assert len(sheets) == 5
    data = build_xlsx(wrep.supplier_report_rows(wrep.supplier_report(conn, "month", "", a["id"])))
    assert len(wx.read_sheet_file("s.xlsx", data)) == 4
