"""Phase 10: nearby traders (opt-in), chats, blocking and reports, carton splits."""

from datetime import datetime, timedelta, timezone

import pytest

from app.db import init_db
from app.wholesale_schema import ws_connect
from app import wholesale_orders as wo
from app import wholesale_service as ws
from app import wholesale_social as wsoc


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-social.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


@pytest.fixture()
def town(conn):
    ws.update_settings(conn, {"global_margin_percent": 0})
    s = ws.save_supplier(conn, {"alias": "أ"})
    started = ws.start_import(conn, s["id"], "a.csv", csv_bytes([
        ["الكود", "اسم الصنف", "سعر القطعة", "سعر الكرتونة", "عدد كرتونة"],
        ["A1", "زيت", "30", "240", "12"],
        ["A2", "ملح", "5", "", ""],
    ]), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    ws.apply_import(conn, started["import_id"], {})
    p = {x["code"]: x["id"] for x in ws.list_products(conn)["items"]}
    ids = [r[0] for r in conn.execute("SELECT id FROM customers ORDER BY id LIMIT 3")]
    for cid, (shop, area) in zip(ids, [("محل الأمل", "شبرا"), ("سوبر ماركت النور", "شبرا "), ("بقالة الهدى", "المعادي")]):
        wo.apply_as_trader(conn, cid, {"shop_name": shop, "area": area})
        wo.decide_trader(conn, cid, "approved", None, None)
    return {"a": ids[0], "b": ids[1], "far": ids[2], "p": p}


def test_hidden_until_opt_in_and_near_only(conn, town):
    a, b, far = town["a"], town["b"], town["far"]
    assert wsoc.nearby(conn, a)["traders"] == []
    wsoc.set_profile(conn, b, True, False, "جملة وقطاعي")
    wsoc.set_profile(conn, far, True, True, "")
    found = wsoc.nearby(conn, a)["traders"]
    assert [t["name"] for t in found] == ["سوبر ماركت النور"]  # same area, the other one is far
    assert found[0]["phone"] == "" and found[0]["bio"] == "جملة وقطاعي"
    wsoc.set_profile(conn, b, None, True, None)
    assert wsoc.nearby(conn, a)["traders"][0]["phone"]
    # a hidden trader can't start a chat
    with pytest.raises(ws.WholesaleError):
        wsoc.start_thread(conn, a, b)
    wsoc.set_profile(conn, a, True, False, "")
    with pytest.raises(ws.WholesaleError):
        wsoc.start_thread(conn, a, far)  # not near
    assert wsoc.start_thread(conn, a, b) == wsoc.start_thread(conn, b, a)
    # turning visibility off hides the phone too
    assert wsoc.set_profile(conn, b, False, True, None)["show_phone"] is False


def test_chat_unread_block_report_and_rate_limit(conn, town):
    a, b = town["a"], town["b"]
    wsoc.set_profile(conn, a, True, False, "")
    wsoc.set_profile(conn, b, True, False, "")
    t = wsoc.start_thread(conn, a, b)
    wsoc.send(conn, a, t, "عندك زيت؟")
    wsoc.send(conn, a, t, "محتاج كرتونة")
    assert wsoc.unread_total(conn, b) == 2 and wsoc.unread_total(conn, a) == 0
    view = wsoc.messages(conn, b, t)
    assert [m["mine"] for m in view["messages"]] == [False, False]
    assert wsoc.unread_total(conn, b) == 0
    assert wsoc.threads(conn, b)[0]["last"]["body"] == "محتاج كرتونة"
    with pytest.raises(ws.WholesaleError):
        wsoc.send(conn, b, t, "   ")
    bad = wsoc.send(conn, b, t, "كلام وحش")["messages"][-1]["id"]
    with pytest.raises(ws.WholesaleError):
        wsoc.report(conn, a, t, view["messages"][0]["id"], "مش رسالته")  # own message
    wsoc.report(conn, a, t, bad, "شتيمة")
    report = wsoc.admin_reports(conn)["items"][0]
    assert report["reason"] == "شتيمة" and any(m["flagged"] and m["body"] == "كلام وحش" for m in report["context"])
    assert report["reported"]["id"] == b and report["reported"]["reports"] == 1
    wsoc.block(conn, a, t, True)
    with pytest.raises(ws.WholesaleError):
        wsoc.send(conn, b, t, "رد")
    assert wsoc.nearby(conn, b)["traders"] == []  # blocked people disappear for both
    wsoc.block(conn, a, t, False)
    wsoc.suspend_chat(conn, b, True)
    with pytest.raises(ws.WholesaleError):
        wsoc.send(conn, b, t, "رد")
    assert wsoc.nearby(conn, a)["traders"] == []
    wsoc.suspend_chat(conn, b, False)
    wsoc.close_report(conn, report["id"], "اتكلمنا معاه")
    assert wsoc.admin_reports(conn)["items"] == []
    for i in range(wsoc.RATE_PER_MINUTE - 1):  # b already sent one this minute
        wsoc.send(conn, b, t, f"رسالة {i}")
    with pytest.raises(ws.WholesaleError):
        wsoc.send(conn, b, t, "كتير")


def test_carton_split_flow(conn, town):
    a, b, far = town["a"], town["b"], town["far"]
    with pytest.raises(ws.WholesaleError):
        wsoc.create_split(conn, a, town["p"]["A1"], 6)  # must be visible
    wsoc.set_profile(conn, a, True, False, "")
    with pytest.raises(ws.WholesaleError):
        wsoc.create_split(conn, a, town["p"]["A2"], 1)  # no carton
    with pytest.raises(ws.WholesaleError):
        wsoc.create_split(conn, a, town["p"]["A1"], 12)  # the whole carton is not a split
    s = wsoc.create_split(conn, a, town["p"]["A1"], 6, "نصها لي")
    assert s["pieces_total"] == 12 and s["remaining"] == 6 and s["piece_price"] == 20 and s["my_cost"] == 120
    # b sees it (near) even while hidden; far doesn't
    assert [x["id"] for x in wsoc.nearby(conn, b)["splits"]] == [s["id"]]
    assert wsoc.nearby(conn, far)["splits"] == []
    with pytest.raises(ws.WholesaleError):
        wsoc.join_split(conn, far, s["id"], 2)
    with pytest.raises(ws.WholesaleError):
        wsoc.join_split(conn, b, s["id"], 7)
    joined = wsoc.join_split(conn, b, s["id"], 4)
    assert joined["remaining"] == 2 and joined["my_pieces"] == 4 and joined["my_cost"] == 80
    t = wsoc.threads(conn, a)[0]
    assert t["last"]["kind"] == "split" and "4 قطعة" in t["last"]["body"]
    with pytest.raises(ws.WholesaleError):
        wsoc.order_split(conn, a, s["id"])  # not full yet
    full = wsoc.join_split(conn, b, s["id"], 6)  # b changes their share to the rest
    assert full["status"] == "full" and full["remaining"] == 0
    assert any("اكتملت" in n["title"] for n in wo.notifications(conn, a)["items"])
    with pytest.raises(ws.WholesaleError):
        wsoc.leave_split(conn, b, s["id"])
    with pytest.raises(ws.WholesaleError):
        wsoc.order_split(conn, b, s["id"])
    done = wsoc.order_split(conn, a, s["id"])
    assert done["status"] == "ordered" and done["product"]["units"]["carton"]["price"] == 240
    assert any("اتطلبت" in n["title"] for n in wo.notifications(conn, b)["items"])


def test_split_expires_and_cancel(conn, town):
    a, b = town["a"], town["b"]
    wsoc.set_profile(conn, a, True, False, "")
    s = wsoc.create_split(conn, a, town["p"]["A1"], 3)
    wsoc.join_split(conn, b, s["id"], 2)
    wsoc.leave_split(conn, b, s["id"])
    assert wsoc.split_view(conn, s["id"], a)["taken"] == 3
    wsoc.join_split(conn, b, s["id"], 2)
    old = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    conn.execute("UPDATE ws_splits SET expires_at = ? WHERE id = ?", (old, s["id"]))
    assert wsoc.nearby(conn, b)["splits"] == []
    assert wsoc.split_view(conn, s["id"], a)["status"] == "cancelled"
    s2 = wsoc.create_split(conn, a, town["p"]["A1"], 3)
    wsoc.join_split(conn, b, s2["id"], 1)
    with pytest.raises(ws.WholesaleError):
        wsoc.cancel_split(conn, b, s2["id"])
    assert wsoc.cancel_split(conn, a, s2["id"])["status"] == "cancelled"
    assert any("اتلغى" in n["title"] for n in wo.notifications(conn, b)["items"])
