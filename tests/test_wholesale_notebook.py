"""Phase 9: the shortages notebook (offline sync, matching, provision requests, reminder, calendar)."""

from datetime import datetime, timedelta

import pytest

from app.db import init_db
from app.wholesale_schema import ws_connect
from app import wholesale_notebook as wn
from app import wholesale_orders as wo
from app import wholesale_reports as wrep
from app import wholesale_service as ws


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-notebook.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


@pytest.fixture()
def trader(conn):
    ws.update_settings(conn, {"global_margin_percent": 0})
    s = ws.save_supplier(conn, {"alias": "أ"})
    started = ws.start_import(conn, s["id"], "a.csv", csv_bytes([["كود", "الصنف", "قطعة", "دستة"], ["A1", "عصير جوافة", "10", "110"], ["A2", "بسكويت", "5", ""]]), None)
    ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    ws.apply_import(conn, started["import_id"], {})
    customer = conn.execute("SELECT id FROM customers ORDER BY id LIMIT 1").fetchone()[0]
    wo.decide_trader(conn, customer, "approved", None, None)
    products = {x["code"]: x["id"] for x in ws.list_products(conn)["items"]}
    return {"c": customer, "p": products}


def test_sync_is_idempotent_and_matches_products(conn, trader):
    c, p = trader["c"], trader["p"]
    ops = [
        {"op": "add", "client_id": "n-000001", "product_id": p["A1"], "name": "عصير جوافة", "unit": "dozen", "qty": 2},
        {"op": "add", "client_id": "n-000002", "name": "  بسكويت ", "unit": "piece", "qty": 30},   # typed offline, matched by name
        {"op": "add", "client_id": "n-000003", "name": "زيت عباد الشمس", "qty": 3},             # not in the store
        {"op": "add", "client_id": "bad id!", "name": "يتجاهل"},
    ]
    first = wn.sync(conn, c, ops)
    again = wn.sync(conn, c, ops)  # the phone resent everything
    assert first["applied"] == 3 and again["applied"] == 0
    items = {i["name"]: i for i in again["items"]}
    assert len(items) == 3
    assert items["عصير جوافة"]["orderable"] and items["عصير جوافة"]["price"] == 110
    assert items["بسكويت"]["product_id"] == p["A2"] and items["بسكويت"]["qty"] == 30
    oil = items["زيت عباد الشمس"]
    assert not oil["orderable"] and oil["is_request"]
    assert again["estimate"] == 2 * 110 + 30 * 5
    requests = conn.execute("SELECT COUNT(*) FROM ws_shortages WHERE source = 'request'").fetchone()[0]
    assert requests == 1
    # a second line with the same missing name does not duplicate the request
    wn.sync(conn, c, [{"op": "add", "client_id": "n-000004", "name": "زيت عباد  الشمس"}])
    assert conn.execute("SELECT COUNT(*) FROM ws_shortages WHERE source = 'request'").fetchone()[0] == 1


def test_update_remove_moved_and_request_answered(conn, trader):
    c, p = trader["c"], trader["p"]
    wn.sync(conn, c, [
        {"op": "add", "client_id": "a-000001", "name": "عصير جوافة", "qty": 1},
        {"op": "add", "client_id": "a-000002", "name": "مناديل كبيرة", "qty": 2},
        {"op": "add", "client_id": "a-000003", "name": "بسكويت", "qty": 1},
    ])
    nb = wn.sync(conn, c, [
        {"op": "update", "client_id": "a-000001", "qty": 4, "unit": "dozen", "note": "الكبير"},
        {"op": "moved", "client_id": "a-000003"},
        {"op": "update", "client_id": "a-000003", "qty": 9},  # after moving: ignored
        {"op": "remove", "client_id": "missing-1"},
    ])
    juice = next(i for i in nb["items"] if i["client_id"] == "a-000001")
    assert juice["qty"] == 4 and juice["unit"] == "dozen" and juice["note"] == "الكبير"
    assert [i["client_id"] for i in nb["items"]] == ["a-000001", "a-000002"]
    # the owner answers the provision request with a product
    req = conn.execute("SELECT id FROM ws_shortages WHERE source = 'request'").fetchone()[0]
    wo.close_request(conn, req, p["A2"], "عندنا بسكويت بداله")
    tissue = next(i for i in wn.notebook(conn, c)["items"] if i["client_id"] == "a-000002")
    assert tissue["product_id"] == p["A2"] and tissue["orderable"]
    wn.sync(conn, c, [{"op": "remove", "client_id": "a-000002"}])
    assert len(wn.notebook(conn, c)["items"]) == 1


def test_frequent_and_index(conn, trader):
    c, p = trader["c"], trader["p"]
    for i in range(3):
        wn.sync(conn, c, [{"op": "add", "client_id": f"f-00000{i}", "product_id": p["A2"], "name": "بسكويت"}])
        wn.sync(conn, c, [{"op": "moved", "client_id": f"f-00000{i}"}])
    wn.sync(conn, c, [{"op": "add", "client_id": "f-000009", "product_id": p["A1"], "name": "عصير جوافة"}])
    freq = wn.frequent(conn, c)
    assert freq[0]["product_id"] == p["A2"] and {f["product_id"] for f in freq} == {p["A1"], p["A2"]}
    index = wn.catalog_index(conn, c)
    assert {row[1] for row in index} == {"عصير جوافة", "بسكويت"}
    juice = next(row for row in index if row[0] == p["A1"])
    assert juice[3] == {"piece": 10, "dozen": 110}


def test_reminder_once_a_week_and_calendar(conn, trader):
    c = trader["c"]
    tz = wrep.local_tz()
    with pytest.raises(ws.WholesaleError):
        wn.reminder_ics(conn, c, "https://x/wholesale/")
    view = wn.set_reminder(conn, c, 4, 9)  # Thursday 9:00
    assert view["day_label"] == "الخميس"
    thursday = datetime(2026, 10, 15, 9, 30, tzinfo=tz)  # a Thursday
    assert wn.last_reminder_moment(4, 9, thursday) == datetime(2026, 10, 15, 9, 0, tzinfo=tz)
    assert wn.last_reminder_moment(4, 9, thursday.replace(hour=8)) == datetime(2026, 10, 8, 9, 0, tzinfo=tz)
    # set "now" right after the reminder was saved: nothing yet
    conn.execute("UPDATE ws_notebook_settings SET last_reminded_at = ?", ((thursday - timedelta(days=2)).isoformat(),))
    assert wn.check_reminder(conn, c, thursday)
    assert not wn.check_reminder(conn, c, thursday + timedelta(hours=3))
    conn.execute("UPDATE ws_notebook_settings SET last_reminded_at = ?", (thursday.isoformat(),))
    assert not wn.check_reminder(conn, c, thursday + timedelta(days=6))
    assert wn.check_reminder(conn, c, thursday + timedelta(days=7, minutes=1))
    title = wo.notifications(conn, c)["items"][0]["title"]
    assert "الخميس" in title
    ics = wn.reminder_ics(conn, c, "https://rose.example/wholesale/#shortages")
    assert "RRULE:FREQ=WEEKLY;BYDAY=TH" in ics and ics.endswith("END:VCALENDAR\r\n")
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    wn.set_reminder(conn, c, None)
    assert not wn.check_reminder(conn, c, thursday + timedelta(days=14, minutes=1))
