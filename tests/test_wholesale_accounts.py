"""Standalone app: trader signup and login, password reset, wholesale points and rewards."""

import pytest

from app import customer_auth as ca
from app import wholesale_orders as wo
from app import wholesale_points as wpts
from app import wholesale_service as ws
from app.db import connect, init_db
from app.wholesale_schema import ws_connect

SHOP = {"shop_name": "محل النور", "area": "شبرا", "address": "شارع 1", "note": ""}


@pytest.fixture()
def path(tmp_path, monkeypatch):
    p = tmp_path / "ws-accounts.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(p))
    init_db(p)
    return p


def test_signup_creates_pending_trader_and_login_works(path):
    with connect() as conn:
        cid = ca.signup(conn, "كريم", "01012345678", "secret-pass-1", SHOP)
    with ws_connect() as conn:
        assert conn.execute("SELECT status FROM ws_traders WHERE customer_id = ?", (cid,)).fetchone()[0] == "pending"
        with pytest.raises(ca.AccountError):
            ca.signup(conn, "تاني", "01012345678", "secret-pass-1", SHOP)
    with connect() as conn:
        assert ca.authenticate(conn, "01012345678", "secret-pass-1")["id"] == cid


def test_short_password_rejected(path):
    with connect() as conn, pytest.raises(ca.AccountError):
        ca.signup(conn, "كريم", "01012345678", "123", SHOP)


def test_lockout_survives_request_rollback(path):
    with connect() as conn:
        ca.signup(conn, "كريم", "01012345678", "secret-pass-1", SHOP)
    for _ in range(ca.MAX_FAILED):
        with pytest.raises(ca.AccountError):
            with connect() as conn:  # each try is its own request; the error rolls it back
                ca.authenticate(conn, "01012345678", "wrong-pass-xx")
    with pytest.raises(ca.AccountError) as exc:
        with connect() as conn:
            ca.authenticate(conn, "01012345678", "secret-pass-1")
    assert exc.value.status == 429


def test_admin_reset_forces_change(path):
    with connect() as conn:
        cid = ca.signup(conn, "كريم", "01012345678", "secret-pass-1", SHOP)
        temp = ca.admin_reset_password(conn, cid)
    with connect() as conn:
        with pytest.raises(ca.AccountError):
            ca.authenticate(conn, "01012345678", "secret-pass-1")
        assert ca.authenticate(conn, "01012345678", temp)["must_change_password"] == 1
        ca.change_password(conn, cid, temp, "brand-new-pass")
        assert ca.authenticate(conn, "01012345678", "brand-new-pass")["must_change_password"] == 0


def test_points_rewards_and_codes(path):
    with connect() as conn:
        cid = ca.signup(conn, "كريم", "01012345678", "secret-pass-1", SHOP)
    with ws_connect() as conn:
        wo.decide_trader(conn, cid, "approved", None, None)
        assert wpts.earn_for_order(conn, cid, 7, 30, "نقاط طلب رقم 7")
        assert not wpts.earn_for_order(conn, cid, 7, 30, "تاني")  # once per order
        reward = wpts.save_reward(conn, {"name": "خصم 50 ج", "cost_points": 25, "stock": 1})
        with pytest.raises(ws.WholesaleError):
            wpts.save_reward(conn, {"name": "x", "cost_points": 0})
        got = wpts.redeem(conn, cid, reward["id"])
        assert len(got["code"]) == 6 and got["balance"] == 5
        with pytest.raises(ws.WholesaleError):
            wpts.redeem(conn, cid, reward["id"])  # out of stock
        assert wpts.list_rewards(conn, active_only=True) == []
        assert [r["code"] for r in wpts.admin_redemptions(conn, "pending", got["code"])] == [got["code"]]
        wpts.close_redemption(conn, got["id"], False, None)
        assert wpts.balance(conn, cid) == 30
        assert wpts.list_rewards(conn)[0]["stock"] == 1
        with pytest.raises(ws.WholesaleError):
            wpts.close_redemption(conn, got["id"], True, None)
        wpts.take_back(conn, cid, 40, "مرتجع", 1)
        assert wpts.balance(conn, cid) == -10
        with pytest.raises(ws.WholesaleError):
            wpts.redeem(conn, cid, reward["id"])
        wpts.adjust(conn, cid, 10, "تصحيح")
        acc = wpts.account(conn, cid)
        assert acc["balance"] == 0 and [e["kind"] for e in acc["entries"]][:2] == ["adjust", "return"]
