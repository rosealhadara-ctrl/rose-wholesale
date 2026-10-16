"""Tests for the wholesale module, phases 1 and 2 (suppliers, import, pricing, tiers)."""

import sqlite3

import pytest

from app.db import init_db
from app.wholesale_excel import read_sheet_file
from app.wholesale_pricing import prices_for_customer, round_up_half
from app.wholesale_schema import ws_connect
from app import wholesale_service as ws


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    path = tmp_path / "ws.db"
    monkeypatch.setenv("WHOLESALE_DB_PATH", str(path))
    init_db(path, seed=True)
    with ws_connect() as connection:
        yield connection


def csv_bytes(rows):
    return ("\n".join(",".join(str(c) for c in row) for row in rows) + "\n").encode("utf-8")


SHEET_A = [
    ["قائمة أسعار مورد", "", "", "", ""],
    ["الكود", "اسم الصنف", "سعر القطعة", "سعر الدستة", "سعر الكرتونة", "عدد كرتونة"],
    ["A1", "شامبو صنسيلك 400 مل", "50", "560", "2640", "48"],
    ["A2", "صابون لوكس 120 جم", "12.5", "140", "", ""],
    ["A3", "مناديل فاين 10 علب", "", "", "300", "10"],
    ["A4", "معجون سيجنال 100 مل", "abc", "", "", ""],
    ["A5", "", "10", "", "", ""],
]


def do_import(conn, supplier_id, rows, decisions=None, mapping=None, filename="s.csv"):
    started = ws.start_import(conn, supplier_id, filename, csv_bytes(rows), None)
    preview = ws.preview_import(
        conn, started["import_id"], mapping or started["mapping"], started["header_row"], started["sheet_index"]
    )
    applied = ws.apply_import(conn, started["import_id"], decisions or {})
    return started, preview, applied


def test_text_helpers():
    assert ws.normalize_name("شامبـو  صنسيلك ٤٠٠مل") == ws.normalize_name("شامبو صنسيلك 400 مل")
    assert ws.normalize_name("مكرونة إيطالية") == ws.normalize_name("مكرونه ايطاليه")
    assert ws.parse_price("1,250.50") == 1250.5
    assert ws.parse_price("١٢٠٫٥") == 120.5
    assert ws.parse_price("120 ج") == 120
    assert ws.parse_price("") is None
    with pytest.raises(ValueError):
        ws.parse_price("abc")
    assert round_up_half(117.30) == 117.5
    assert round_up_half(117.60) == 118.0
    assert round_up_half(117.0) == 117.0
    assert round_up_half(117.5) == 117.5
    assert round_up_half(115.00000001) == 115.0
    guessed = ws.guess_mapping(["Item", "Code", "Carton", "Pcs/Ctn", "Dozen", "Piece"])
    assert guessed == {"name": 0, "code": 1, "section": None, "price_piece": 5, "price_dozen": 4,
                       "price_carton": 2, "pieces_per_dozen": None, "pieces_per_carton": 3}
    guessed = ws.guess_mapping(["اسم الصنف", "سعر الكرتونة", "عدد القطع بالكرتونة"])
    assert guessed["price_carton"] == 1 and guessed["pieces_per_carton"] == 2


def test_reader_handles_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "أسعار"
    sheet.append(["الصنف", "سعر"])
    sheet.append(["بسكويت", 117.3])
    sheet.append(["كيك", 12])
    second = book.create_sheet("تانية")
    second.append(["x"])
    target = tmp_path / "a.xlsx"
    book.save(target)
    sheets = read_sheet_file("a.xlsx", target.read_bytes())
    assert [s["name"] for s in sheets] == ["أسعار", "تانية"]
    assert sheets[0]["rows"][1] == ["بسكويت", "117.3"]
    assert sheets[0]["rows"][2] == ["كيك", "12"]


def test_import_mapping_preview_and_apply(conn):
    supplier = ws.save_supplier(conn, {"alias": "مورد أ", "phone": "0100 000 0001"})
    started = ws.start_import(conn, supplier["id"], "a.csv", csv_bytes(SHEET_A), None)
    assert started["header_row"] == 2
    mapping = started["mapping"]
    assert mapping["code"] == 0 and mapping["name"] == 1
    assert mapping["price_piece"] == 2 and mapping["price_dozen"] == 3 and mapping["price_carton"] == 4
    assert mapping["pieces_per_carton"] == 5

    preview = ws.preview_import(conn, started["import_id"], mapping, 2, 0)
    assert preview["summary"]["new"] == 3
    issues = {p["code"]: p["issue"] for p in preview["problems"]}
    assert issues["A4"].startswith("سعر مش رقم")
    assert issues["A5"] == "اسم فاضي"

    applied = ws.apply_import(conn, started["import_id"], {})
    assert applied["summary"]["applied"]["created_products"] == 3
    assert ws.get_template(conn, supplier["id"])["header_row"] == 2

    products = ws.list_products(conn)["items"]
    shampoo = next(p for p in products if p["code"] == "A1")
    # default margin 15%, rounded up to 0.50
    assert shampoo["units"]["piece"]["price"] == 57.5
    assert shampoo["units"]["carton"]["price"] == 3036.0
    assert shampoo["units"]["carton"]["per_piece"] == 63.25
    assert shampoo["units"]["dozen"]["pieces"] == 12

    # Second sheet: price change, one item gone, columns re-ordered (template follows headers).
    sheet2 = [
        ["اسم الصنف", "الكود", "سعر الدستة", "سعر القطعة", "سعر الكرتونة", "عدد كرتونة"],
        ["شامبو صنسيلك 400 مل", "A1", "560", "55", "2640", "48"],
        ["مناديل فاين 10 علب", "A3", "", "", "320", "10"],
    ]
    started2 = ws.start_import(conn, supplier["id"], "b.csv", csv_bytes(sheet2), None)
    assert started2["from_template"]
    assert started2["mapping"]["name"] == 0 and started2["mapping"]["price_piece"] == 3
    preview2 = ws.preview_import(conn, started2["import_id"], started2["mapping"], started2["header_row"], 0)
    assert preview2["summary"]["changed"] == 2
    assert preview2["summary"]["removed"] == 1 and preview2["summary"]["hidden_products"] == 1
    ws.apply_import(conn, started2["import_id"], {})
    soap = next(p for p in ws.list_products(conn)["items"] if p["code"] == "A2")
    assert soap["available"] is False and soap["suppliers"] == []
    detail = ws.product_detail(conn, shampoo["id"])
    assert any(h["unit"] == "piece" and h["new_price"] == 55 for h in detail["history"])


def test_multi_supplier_linking_and_cheapest(conn):
    a = ws.save_supplier(conn, {"alias": "أ"})
    b = ws.save_supplier(conn, {"alias": "ب"})
    do_import(conn, a["id"], [["كود", "الصنف", "قطعة", "دستة"], ["X1", "زيت كريستال 1 لتر", "80", "900"], ["X2", "سكر الأسرة 1 كيلو", "30", "350"]])
    _, preview, _ = do_import(
        conn,
        b["id"],
        [["كود", "الصنف", "قطعة", "دستة"], ["X1", "زيت كريستال لتر", "82", "880"], ["Z9", "سكر الاسره ١ كيلو جرام", "29", ""], ["Z8", "شاي العروسة", "40", ""]],
    )
    by_code = {n["code"]: n for n in preview["new"]}
    assert by_code["X1"]["auto_product_id"]  # same code -> automatic
    assert by_code["Z9"]["suggestions"] and not by_code["Z9"]["auto_product_id"]
    assert by_code["Z9"]["default_choice"] == 0  # not identical -> manager must confirm
    assert by_code["Z8"]["suggestions"] == []

    items = {p["code"]: p for p in ws.list_products(conn)["items"]}
    oil = items["X1"]
    assert {s["alias"] for s in oil["suppliers"]} == {"أ", "ب"}
    # cheapest is computed per unit: piece from أ (80), dozen from ب (880)
    assert oil["units"]["piece"]["cost"] == 80 and oil["units"]["piece"]["supplier_alias"] == "أ"
    assert oil["units"]["dozen"]["cost"] == 880 and oil["units"]["dozen"]["supplier_alias"] == "ب"
    # sugar from ب was not confirmed, so it became its own product; link it now
    sugar_b = next(p for p in ws.list_products(conn, q="جرام")["items"])
    merged = ws.merge_products(conn, sugar_b["id"], items["X2"]["id"])
    assert len(merged["offers"]) == 2

    # manual supplier choice
    detail = ws.update_product(conn, oil["id"], {"source_mode": "manual", "manual_supplier_id": b["id"]})
    default_pricing = detail["pricing"][0]
    assert default_pricing["units"]["piece"]["cost"] == 82
    # split the link again
    offer_b = next(o for o in detail["offers"] if o["supplier_id"] == b["id"])
    ws.split_offer(conn, offer_b["id"])
    assert ws.product_detail(conn, oil["id"])["source_mode"] == "cheapest"


def test_confirmed_suggestion_links(conn):
    a = ws.save_supplier(conn, {"alias": "أ"})
    b = ws.save_supplier(conn, {"alias": "ب"})
    do_import(conn, a["id"], [["الصنف", "قطعة"], ["سكر الأسرة 1 كيلو", "30"]])
    started = ws.start_import(conn, b["id"], "b.csv", csv_bytes([["الصنف", "قطعة"], ["سكر الاسره ١ كيلو جرام", "29"]]), None)
    preview = ws.preview_import(conn, started["import_id"], started["mapping"], 1, 0)
    row = preview["new"][0]
    ws.apply_import(conn, started["import_id"], {str(row["row"]): row["suggestions"][0]["product_id"]})
    items = ws.list_products(conn)["items"]
    assert len(items) == 1 and items[0]["units"]["piece"]["cost"] == 29


def test_pricing_order_tiers_and_subscription(conn):
    sup = ws.save_supplier(conn, {"alias": "أ"})
    do_import(conn, sup["id"], [["الصنف", "قطعة", "دستة"], ["عصير جهينة", "10", "117.3"]])
    product = ws.list_products(conn)["items"][0]
    ws.update_settings(conn, {"global_margin_percent": 0})
    assert ws.product_detail(conn, product["id"])["pricing"][0]["units"]["dozen"]["price"] == 117.5

    tiers = ws.save_tier(conn, {"name": "تاجر كبير", "margin_percent": 8})
    big = next(t for t in tiers if t["name"] == "تاجر كبير")
    subscriber = next(t for t in tiers if t["is_subscriber"])
    ws.update_settings(conn, {"global_margin_percent": 20})

    def price(tier_name, unit="piece"):
        pricing = ws.product_detail(conn, product["id"])["pricing"]
        return next(p for p in pricing if p["tier_name"] == tier_name)["units"][unit]

    assert price("عادي")["price"] == 12.0 and price("عادي")["rule"] == "global_margin"
    assert price("تاجر كبير")["price"] == 11.0  # 10.8 -> 11.0
    assert price("مشترك")["price"] == 10.0  # sheet price exactly
    # product margin beats the tier margin
    ws.update_product(conn, product["id"], {"margin_percent": 5})
    assert price("تاجر كبير")["price"] == 10.5 and price("تاجر كبير")["rule"] == "product_margin"
    # a fixed price for one tier beats everything, and warns when below cost
    detail = ws.set_tier_prices(conn, product["id"], big["id"], {"piece": 9})
    big_pricing = next(p for p in detail["pricing"] if p["tier_id"] == big["id"])
    assert big_pricing["units"]["piece"]["price"] == 9 and big_pricing["units"]["piece"]["below_cost"]
    assert big_pricing["warnings"]
    ws.set_tier_prices(conn, product["id"], big["id"], {"piece": ""})
    assert price("تاجر كبير")["rule"] == "product_margin"

    # traders: default tier until assigned; trader sees only own prices, no supplier data
    customer_id = conn.execute("SELECT id FROM customers ORDER BY id LIMIT 1").fetchone()[0]
    seen = prices_for_customer(conn, customer_id)[product["id"]]
    assert seen["piece"]["price"] == 10.5 and "cost" not in seen["piece"] and "supplier_alias" not in seen["piece"]

    # subscription announcement -> request -> approve -> subscriber tier
    assert ws.announcement_for_customer(conn, customer_id) is None
    ws.update_settings(conn, {"announcement_enabled": True, "announcement_text": "اطلب بسعر المورد"})
    assert ws.announcement_for_customer(conn, customer_id)["text"] == "اطلب بسعر المورد"
    request = ws.request_subscription(conn, customer_id)
    assert ws.request_subscription(conn, customer_id)["already"]
    assert ws.announcement_for_customer(conn, customer_id)["request_pending"]
    decided = ws.decide_subscription(conn, request["id"], True, None, None)
    assert decided["tier_name"] == subscriber["name"]
    assert ws.announcement_for_customer(conn, customer_id) is None
    ws.update_product(conn, product["id"], {"margin_percent": None})
    assert prices_for_customer(conn, customer_id)[product["id"]]["piece"]["price"] == 10.0

    with pytest.raises(ws.WholesaleError):
        ws.delete_tier(conn, subscriber["id"])  # has a trader
    with pytest.raises(ws.WholesaleError):
        ws.delete_tier(conn, next(t for t in tiers if t["is_default"])["id"])


def test_manual_product_and_hidden(conn):
    sup = ws.save_supplier(conn, {"alias": "مخزن"})
    detail = ws.create_manual_product(
        conn, {"name": "كوباية زجاج", "supplier_id": sup["id"], "price_carton": "600", "pieces_per_carton": 24, "section": "أدوات"}
    )
    carton = detail["pricing"][0]["units"]["carton"]
    assert carton["price"] == 690 and carton["per_piece"] == 28.75
    assert detail["pricing"][0]["units"]["piece"] is None
    ws.update_product(conn, detail["id"], {"is_visible": False})
    customer_id = conn.execute("SELECT id FROM customers LIMIT 1").fetchone()[0]
    assert detail["id"] not in prices_for_customer(conn, customer_id)
    with pytest.raises(ws.WholesaleError):
        ws.create_manual_product(conn, {"name": "x", "supplier_id": sup["id"]})


def test_mapping_validation(conn):
    sup = ws.save_supplier(conn, {"alias": "أ"})
    started = ws.start_import(conn, sup["id"], "a.csv", csv_bytes([["a", "b"], ["x", "1"]]), None)
    with pytest.raises(ws.WholesaleError):
        ws.preview_import(conn, started["import_id"], {"name": 0}, 1, 0)
    with pytest.raises(ws.WholesaleError):
        ws.preview_import(conn, started["import_id"], {"name": 0, "price_piece": 0}, 1, 0)
    with pytest.raises(ws.WholesaleError):
        ws.apply_import(conn, started["import_id"], {})
    with pytest.raises(ws.WholesaleError):
        ws.start_import(conn, sup["id"], "a.xls", b"junk", None)
    with pytest.raises(ws.WholesaleError):
        ws.save_supplier(conn, {"alias": "أ"})
