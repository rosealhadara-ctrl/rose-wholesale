"""Product search: words in any order, singular/plural, synonyms (iphone = ابل), closest names."""

import pytest

from app import wholesale_orders as wo
from app import wholesale_search as wsearch
from app import wholesale_service as ws
from app.db import init_db
from app.wholesale_schema import ws_connect
from app.wholesale_service import normalize_name


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws-search.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def load(conn, alias, rows):
    supplier = ws.save_supplier(conn, {"alias": alias, "phone": "01000000001"})
    content = ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")
    started = ws.start_import(conn, supplier["id"], "s.csv", content, None)
    ws.preview_import(conn, started["import_id"], started["mapping"], started["header_row"], 0)
    ws.apply_import(conn, started["import_id"], {})
    return supplier


@pytest.fixture()
def gadgets(conn):
    ws.update_settings(conn, {"global_margin_percent": 10})
    load(conn, "أ", [
        ["كود", "الصنف", "قطعة"],
        ["H1", "سماعة أبل لاسلكية", "300"],
        ["H2", "سماعات سامسونج", "200"],
        ["H3", "شاحن ايفون سريع", "150"],
        ["W1", "مجموعة أوزان قابلة للتعديل", "900"],
    ])
    return conn


def names(data):
    return {i["name"] for i in data["items"]}


def test_terms_stem_and_synonyms():
    terms = wsearch.query_terms(normalize_name("السماعات iPhone"))
    assert "سماع" in terms[0] and "headphone" in terms[0]
    assert {"ابل", "iphone", "ايفون"} <= set(terms[1])


def test_trader_search_understands_other_words(gadgets):
    assert names(wo.catalog(gadgets, None, q="سماعات iphone")) == {"سماعة أبل لاسلكية"}
    assert names(wo.catalog(gadgets, None, q="Apple سماعه")) == {"سماعة أبل لاسلكية"}
    assert names(wo.catalog(gadgets, None, q="headphones samsung")) == {"سماعات سامسونج"}
    # "ابل" must not hit "قابلة"
    assert "مجموعة أوزان قابلة للتعديل" not in names(wo.catalog(gadgets, None, q="ابل"))
    assert wo.catalog(gadgets, None, q="H3")["total"] == 1


def test_closest_names_when_nothing_has_every_word(gadgets):
    data = wo.catalog(gadgets, None, q="سماعات هواوي")
    assert data["approximate"] is True
    assert names(data) == {"سماعة أبل لاسلكية", "سماعات سامسونج"}
    exact = wo.catalog(gadgets, None, q="سماعات ابل")
    assert exact["approximate"] is False
    assert wo.catalog(gadgets, None, q="تلاجة")["total"] == 0


def test_admin_link_search_finds_the_trader_request(gadgets):
    data = ws.list_products(gadgets, q="سماعات iphone")
    assert [i["name"] for i in data["items"]] == ["سماعة أبل لاسلكية"]
    near = ws.list_products(gadgets, q="سماعات هواوي")
    assert near["approximate"] and {i["name"] for i in near["items"]} == {"سماعة أبل لاسلكية", "سماعات سامسونج"}
