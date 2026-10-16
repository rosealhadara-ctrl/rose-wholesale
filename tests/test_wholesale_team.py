"""Trader team accounts: employees without prices, manager approval, direct sending, unavailable notices."""

import pytest

from app.db import init_db
from app.wholesale_schema import ws_connect
from app import wholesale_notebook as wn
from app import wholesale_orders as wo
from app import wholesale_service as ws
from app import wholesale_team as wt


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-team.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


@pytest.fixture()
def shop(conn):
    ws.update_settings(conn, {"global_margin_percent": 0})
    s = ws.save_supplier(conn, {"alias": "أ"})
    started = ws.start_import(conn, s["id"], "a.csv", csv_bytes([["كود", "الصنف", "قطعة"], ["A1", "عصير", "10"], ["A2", "بسكويت", "5"]]), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    ws.apply_import(conn, started["import_id"], {})
    owner = conn.execute("SELECT id FROM customers ORDER BY id LIMIT 1").fetchone()[0]
    wo.decide_trader(conn, owner, "approved", None, None)
    p = {x["code"]: x["id"] for x in ws.list_products(conn)["items"]}
    return {"owner": owner, "p": p}


def test_add_staff_login_and_hidden_prices(conn, shop):
    owner = shop["owner"]
    s = wt.add_staff(conn, owner, "حسن", "01055556666", {})
    assert len(s["pin"]) == 4 and not s["can_see_prices"] and not s["can_send_direct"] and s["can_use_notebook"]
    with pytest.raises(ws.WholesaleError):
        wt.add_staff(conn, owner, "تاني", "01055556666", {})  # phone already used
    token = wt.login(conn, "01055556666", s["pin"])
    me = wt.current_staff(conn, token)
    assert me["name"] == "حسن"
    items = wt.staff_catalog(conn, me)["items"]
    juice = next(i for i in items if i["name"] == "عصير")
    assert "price" not in juice["units"]["piece"]  # no prices for this employee
    wt.update_staff(conn, owner, s["id"], {"can_see_prices": True})
    me = wt.current_staff(conn, token)
    assert next(i for i in wt.staff_catalog(conn, me)["items"] if i["name"] == "عصير")["units"]["piece"]["price"] == 10
    # deactivating ends the session; a new PIN replaces the old one
    wt.update_staff(conn, owner, s["id"], {"is_active": False})
    assert wt.current_staff(conn, token) is None
    wt.update_staff(conn, owner, s["id"], {"is_active": True})
    new = wt.reset_pin(conn, owner, s["id"])["pin"]
    if new != s["pin"]:
        with pytest.raises(ws.WholesaleError):
            wt.login(conn, "01055556666", s["pin"])
    assert wt.login(conn, "01055556666", new)


def test_request_approval_into_an_order(conn, shop):
    owner, p = shop["owner"], shop["p"]
    s = wt.add_staff(conn, owner, "حسن", "01055556666", {})
    me = wt.current_staff(conn, wt.login(conn, "01055556666", s["pin"]))
    req = wt.submit_request(conn, me, [
        {"product_id": p["A1"], "unit": "piece", "qty": 5, "note": "البرتقال"},
        {"product_id": p["A2"], "unit": "piece", "qty": 10},
        {"product_id": p["A1"], "unit": "piece", "qty": 1},
    ], "محتاجينهم بكرة")
    assert req["status"] == "pending" and req["total"] is None and all(i["price"] is None for i in req["items"])
    assert [i["qty"] for i in req["items"]] == [6, 10]  # same product and unit merged
    assert wt.pending_count(conn, owner) == 1
    assert "مستنية موافقتك" in wo.notifications(conn, owner)["items"][0]["title"]
    seen = wt.owner_requests(conn, owner)[0]
    assert seen["total"] == 6 * 10 + 10 * 5 and seen["items"][0]["product"]["id"] == p["A1"]
    biscuit = next(i for i in seen["items"] if i["name"] == "بسكويت")
    juice = next(i for i in seen["items"] if i["name"] == "عصير")
    approved = wt.decide_request(conn, owner, seen["id"], True, [{"item_id": biscuit["id"], "remove": True}, {"item_id": juice["id"], "qty": 8}], "شلت البسكويت")
    assert approved["status"] == "approved" and [(i["name"], i["qty"]) for i in approved["items"]] == [("عصير", 8)]
    with pytest.raises(ws.WholesaleError):
        wt.decide_request(conn, owner, seen["id"], False)
    order = wo.submit_order(conn, owner, [{"product_id": p["A1"], "unit": "piece", "qty": 8}], payment_method="cash")
    wt.link_order(conn, owner, [seen["id"]], order["id"], order["number"])
    mine = wt.staff_requests(conn, me)[0]
    assert mine["status"] == "ordered" and mine["order_number"] == order["number"]
    titles = [n["title"] for n in wt.staff_notices(conn, me["id"])["items"]]
    assert "المدير وافق على قايمتك" in titles and any(str(order["number"]) in t for t in titles)


def test_direct_send_unavailable_notice_and_block(conn, shop):
    owner, p = shop["owner"], shop["p"]
    s = wt.add_staff(conn, owner, "منى", "01077778888", {"can_send_direct": True})
    me = wt.current_staff(conn, wt.login(conn, "01077778888", s["pin"]))
    req = wt.submit_request(conn, me, [{"product_id": p["A1"], "unit": "piece", "qty": 3}, {"product_id": p["A2"], "unit": "piece", "qty": 4}])
    assert req["status"] == "ordered" and req["order_id"]
    order = wo.admin_order(conn, req["order_id"])
    assert order["customer_note"].startswith("طلب من منى") and order["payment_method"] == "cash"
    juice = next(i for po in order["purchase_orders"] for i in po["items"] if i["name"] == "عصير")
    wo.set_item_decision(conn, order["id"], juice["id"], "unavailable")
    for po in order["purchase_orders"]:
        wo.po_all_available(conn, order["id"], po["id"])
    wo.confirm_review(conn, order["id"])
    notices = wt.staff_notices(conn, me["id"], mark_read=True)
    assert notices["unread"] == 1 and notices["items"][0]["title"] == "«عصير» مش متوفر دلوقتي"
    assert wt.staff_notices(conn, me["id"])["unread"] == 0
    assert next(i for i in wt.staff_catalog(conn, me)["items"] if i["name"] == "عصير")["waiting"]
    with pytest.raises(ws.WholesaleError):
        wt.submit_request(conn, me, [{"product_id": p["A1"], "unit": "piece", "qty": 1}])  # blocked until available
    wo.mark_available(conn, p["A1"])
    assert wt.staff_notices(conn, me["id"])["items"][0]["title"] == "«عصير» اتوفر"
    assert wt.submit_request(conn, me, [{"product_id": p["A1"], "unit": "piece", "qty": 1}])["status"] == "ordered"
    wo.mark_available(conn, p["A1"])  # nothing waiting: no second "available" notice
    assert sum(1 for n in wt.staff_notices(conn, me["id"])["items"] if "اتوفر" in n["title"]) == 1


def test_lockout_and_shared_notebook(conn, shop):
    owner = shop["owner"]
    s = wt.add_staff(conn, owner, "حسن", "01055556666", {})
    conn.commit()
    wrong = "1111" if s["pin"] != "1111" else "2222"
    for _ in range(wt.MAX_FAILED):
        with pytest.raises(ws.WholesaleError):
            with ws_connect() as c:
                wt.login(c, "01055556666", wrong)
    with pytest.raises(ws.WholesaleError) as err:
        with ws_connect() as c:
            wt.login(c, "01055556666", s["pin"])
    assert err.value.status == 429
    # employees write to the shop's notebook
    wn.sync(conn, owner, [{"op": "add", "client_id": "team-0001", "name": "بسكويت", "qty": 2, "note": "سجّله حسن"}])
    assert wn.notebook(conn, owner)["items"][0]["note"] == "سجّله حسن"
