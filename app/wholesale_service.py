"""Wholesale business logic: suppliers, sheet import, products, tiers.

Covers phases 1 and 2 of section ١٣ in docs/ROSE_WHOLESALE_AND_FAKKA_SPEC_AR.md
(sections ١٥, ١٦ and ١٧).  All functions take an open SQLite connection from
``wholesale_schema.ws_connect`` and raise ``WholesaleError`` with an Arabic
message for anything the admin should see.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from .db import utc_now
from .wholesale_excel import SheetError, read_sheet_file
from .wholesale_pricing import (
    UNITS,
    default_tier,
    get_settings,
    list_tiers,
    load_offers,
    load_tier_prices,
    price_product,
)


class WholesaleError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- text helpers

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_LETTER_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي", "ـ": ""})


def normalize_name(text: str) -> str:
    """Comparison key for product names (Arabic-aware, spacing-insensitive)."""
    value = unicodedata.normalize("NFKC", str(text or "")).translate(_ARABIC_DIGITS)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.translate(_LETTER_MAP).lower()
    value = re.sub(r"(\d)\s*[x×*]\s*(\d)", r"\1x\2", value)
    value = re.sub(r"[^\w.x]+", " ", value)
    value = re.sub(r"(\d)([^\d\s.x])", r"\1 \2", value)
    value = re.sub(r"([^\d\s.x])(\d)", r"\1 \2", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_text(text: Any, limit: int = 250) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def parse_price(text: Any) -> Optional[float]:
    """'1,250.50' / '١٢٠٫٥' / '120 ج' -> float, '' -> None, junk -> ValueError."""
    value = str(text if text is not None else "").translate(_ARABIC_DIGITS).strip()
    value = value.replace("٫", ".").replace("٬", ",")
    value = re.sub(r"(ج\.?م|جنيه|ج|egp|le)\.?$", "", value, flags=re.IGNORECASE).strip()
    if value in ("", "-", "—"):
        return None
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", value):
        value = value.replace(",", "")
    elif re.fullmatch(r"\d+,\d{1,2}", value):
        value = value.replace(",", ".")
    try:
        number = float(value)
    except ValueError:
        raise ValueError(str(text)) from None
    if number < 0 or number > 10_000_000:
        raise ValueError(str(text))
    return round(number, 4) if number else None


def parse_count(text: Any) -> Optional[int]:
    value = str(text if text is not None else "").translate(_ARABIC_DIGITS).strip()
    match = re.search(r"\d+(\.\d+)?", value)
    if not match:
        return None
    number = int(float(match.group(0)))
    return number if 0 < number <= 100000 else None


# ------------------------------------------------------------------- settings


def update_settings(conn: sqlite3.Connection, values: Dict[str, Any]) -> Dict[str, str]:
    allowed = {
        "global_margin_percent",
        "default_source_mode",
        "announcement_enabled",
        "announcement_title",
        "announcement_text",
        "min_order_total",
        "delivery_fee",
        "points_egp_per_point",
        "credit_period_days",
        "credit_block_overdue",
        "return_days",
        "plus_enabled",
        "plus_price_month",
        "plus_price_quarter",
        "plus_free_delivery",
        "plus_fee_share",
    }
    now = utc_now()
    for key, value in values.items():
        if key not in allowed or value is None:
            continue
        if key == "global_margin_percent":
            number = float(value)
            if number <= -100 or number > 1000:
                raise WholesaleError("نسبة الهامش لازم تكون بين -99 و 1000.")
            value = repr(round(number, 4))
        elif key == "default_source_mode":
            if value not in ("cheapest", "manual"):
                raise WholesaleError("اختيار المورد الافتراضي غير صحيح.")
        elif key in ("min_order_total", "delivery_fee", "points_egp_per_point", "plus_price_month", "plus_price_quarter"):
            number = float(value)
            if number < 0 or number > 10_000_000:
                raise WholesaleError("فيه رقم برّه الحدود.")
            value = repr(round(number, 2))
        elif key == "plus_fee_share":
            number = float(value)
            if number < 0 or number > 100:
                raise WholesaleError("نسبة عمولة مشترك بلس لازم تكون من 0 لحد 100.")
            value = repr(round(number, 2))
        elif key == "return_days":
            number = int(float(value))
            if number < 1 or number > 90:
                raise WholesaleError("مدة الإرجاع لازم تكون من يوم لحد 90 يوم.")
            value = str(number)
        elif key == "credit_period_days":
            number = int(float(value))
            if number < 1 or number > 365:
                raise WholesaleError("فترة السداد لازم تكون من يوم لحد 365 يوم.")
            value = str(number)
        elif key in ("announcement_enabled", "credit_block_overdue", "plus_enabled", "plus_free_delivery"):
            value = "1" if value in (True, 1, "1", "true") else "0"
        elif key == "announcement_enabled":
            value = "1" if value in (True, 1, "1", "true") else "0"
        else:
            value = clean_text(value, 600 if key == "announcement_text" else 120)
        conn.execute(
            "INSERT INTO ws_settings(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, str(value), now),
        )
    result = get_settings(conn)
    if result.get("plus_enabled") == "1" and not any(float(result.get(k) or 0) > 0 for k in ("plus_price_month", "plus_price_quarter")):
        raise WholesaleError("حط سعر لاشتراك روز بلس قبل ما تشغّله.")
    return result


# ------------------------------------------------------------------ suppliers


def list_suppliers(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM ws_offers o WHERE o.supplier_id = s.id AND o.is_active = 1) AS active_items,
               (SELECT MAX(i.applied_at) FROM ws_imports i WHERE i.supplier_id = s.id AND i.status = 'applied') AS last_import_at,
               (SELECT 1 FROM ws_column_templates t WHERE t.supplier_id = s.id) AS has_template
        FROM ws_suppliers s ORDER BY s.is_active DESC, s.alias COLLATE NOCASE
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _supplier(conn: sqlite3.Connection, supplier_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_suppliers WHERE id = ?", (supplier_id,)).fetchone()
    if not row:
        raise WholesaleError("المورد مش موجود.", 404)
    return dict(row)


def save_supplier(conn: sqlite3.Connection, data: Dict[str, Any], supplier_id: Optional[int] = None) -> Dict[str, Any]:
    now = utc_now()
    alias = clean_text(data.get("alias"), 80) if data.get("alias") is not None else None
    phone = re.sub(r"[^\d+]", "", str(data.get("phone") or "").translate(_ARABIC_DIGITS)) if data.get("phone") is not None else None
    notes = clean_text(data.get("notes"), 500) if data.get("notes") is not None else None
    if supplier_id is None:
        if not alias:
            raise WholesaleError("اكتب اسم مستعار للمورد.")
        try:
            cur = conn.execute(
                "INSERT INTO ws_suppliers(alias, phone, notes, is_active, created_at, updated_at) VALUES(?, ?, ?, 1, ?, ?)",
                (alias, phone or "", notes or "", now, now),
            )
        except sqlite3.IntegrityError:
            raise WholesaleError("فيه مورد بنفس الاسم المستعار.") from None
        return _supplier(conn, cur.lastrowid)
    current = _supplier(conn, supplier_id)
    if alias is not None and not alias:
        raise WholesaleError("الاسم المستعار مينفعش يبقى فاضي.")
    active = data.get("is_active")
    try:
        conn.execute(
            "UPDATE ws_suppliers SET alias = ?, phone = ?, notes = ?, is_active = ?, updated_at = ? WHERE id = ?",
            (
                alias if alias is not None else current["alias"],
                phone if phone is not None else current["phone"],
                notes if notes is not None else current["notes"],
                (1 if active else 0) if active is not None else current["is_active"],
                now,
                supplier_id,
            ),
        )
    except sqlite3.IntegrityError:
        raise WholesaleError("فيه مورد بنفس الاسم المستعار.") from None
    return _supplier(conn, supplier_id)


# ------------------------------------------------------------------- sections


def list_sections(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT s.id, s.name, (SELECT COUNT(*) FROM ws_products p WHERE p.section_id = s.id) AS products
            FROM ws_sections s ORDER BY s.sort_order, s.name COLLATE NOCASE
            """
        )
    ]


def section_id_for(conn: sqlite3.Connection, name: str, create: bool = True) -> Optional[int]:
    name = clean_text(name, 80)
    if not name:
        return None
    row = conn.execute("SELECT id FROM ws_sections WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    if not create:
        return None
    return conn.execute("INSERT INTO ws_sections(name, created_at) VALUES(?, ?)", (name, utc_now())).lastrowid


# --------------------------------------------------------------- sheet import

FIELDS: List[Tuple[str, str]] = [
    ("name", "اسم الصنف"),
    ("code", "الكود"),
    ("section", "القسم"),
    ("price_piece", "سعر القطعة"),
    ("price_dozen", "سعر الدستة"),
    ("price_carton", "سعر الكرتونة"),
    ("pieces_per_dozen", "عدد القطع في الدستة"),
    ("pieces_per_carton", "عدد القطع في الكرتونة"),
]
FIELD_KEYS = [key for key, _ in FIELDS]
PRICE_FIELDS = ("price_piece", "price_dozen", "price_carton")

_GUESS_RULES: List[Tuple[str, List[str]]] = [
    ("pieces_per_carton", ["عدد كرتون", "عدد القطع كرتون", "قطع كرتون", "عبوه كرتون", "تعبئه كرتون", "per carton", "pcs carton", "carton qty", "pack"]),
    ("pieces_per_dozen", ["عدد دست", "قطع دست", "per dozen", "dozen qty"]),
    ("price_carton", ["كرتون", "carton", "ctn", "case"]),
    ("price_dozen", ["دسته", "دست", "درزن", "dozen", "dz"]),
    ("price_piece", ["قطعه", "القطعه", "سعر الوحده", "وحده", "piece", "pcs", "unit price", "سعر"]),
    ("code", ["كود", "الكود", "باركود", "رقم الصنف", "code", "sku", "barcode", "id"]),
    ("section", ["قسم", "القسم", "مجموعه", "الفئه", "category", "group", "section"]),
    ("name", ["اسم", "الصنف", "البيان", "الوصف", "name", "item", "product", "description"]),
]


def _count_field(key: str) -> Optional[str]:
    """'عدد القطع بالكرتونه' / 'Pcs/Ctn' -> pieces_per_carton."""
    if "سعر" in key or "price" in key:
        return None
    is_count = any(w in key for w in ("عدد", "قطع", "pcs", "qty", "عبوه", "تعبئه", "pack"))
    if is_count and any(w in key for w in ("كرتون", "ctn", "carton", "case")):
        return "pieces_per_carton"
    if is_count and any(w in key for w in ("دست", "درزن", "dozen", "dz")):
        return "pieces_per_dozen"
    return None


def guess_mapping(headers: List[str]) -> Dict[str, Optional[int]]:
    mapping: Dict[str, Optional[int]] = {key: None for key in FIELD_KEYS}
    used: set = set()
    keys = [normalize_name(h) for h in headers]
    for index, key in enumerate(keys):
        field = _count_field(key)
        if field and mapping[field] is None:
            mapping[field] = index
            used.add(index)
    for field, words in _GUESS_RULES:
        if mapping[field] is not None:
            continue
        for index, key in enumerate(keys):
            if index in used or not key:
                continue
            if any(normalize_name(word) in key for word in words):
                mapping[field] = index
                used.add(index)
                break
    return mapping


def _detect_header_row(rows: List[List[str]]) -> int:
    best, best_score = 1, -1
    for number, row in enumerate(rows[:10], start=1):
        texts = [c for c in row if c and parse_count(c) is None]
        score = len(texts) * 2 + sum(1 for k in guess_mapping(row).values() if k is not None) * 3
        if score > best_score:
            best, best_score = number, score
    return best


def _template_header_row(template: Dict[str, Any], rows: List[List[str]]) -> int:
    """Find the row holding the saved header texts (the sheet may have moved)."""
    wanted = {normalize_name(v.get("header", "")) for v in (template.get("mapping") or {}).values() if v}
    wanted.discard("")
    best, best_hits = int(template.get("header_row") or 1), 0
    for number, row in enumerate(rows[:15], start=1):
        hits = len(wanted & {normalize_name(c) for c in row if c})
        if hits > best_hits:
            best, best_hits = number, hits
    return best


def _template(conn: sqlite3.Connection, supplier_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM ws_column_templates WHERE supplier_id = ?", (supplier_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["mapping"] = json.loads(data.pop("mapping_json"))
    return data


def _mapping_from_template(template: Dict[str, Any], headers: List[str]) -> Dict[str, Optional[int]]:
    """Re-locate saved columns by header text first, then by position."""
    mapping: Dict[str, Optional[int]] = {key: None for key in FIELD_KEYS}
    keys = [normalize_name(h) for h in headers]
    for field, saved in (template.get("mapping") or {}).items():
        if field not in mapping or not saved:
            continue
        header_key = normalize_name(saved.get("header", ""))
        if header_key and header_key in keys:
            mapping[field] = keys.index(header_key)
        elif saved.get("index") is not None and saved["index"] < len(headers):
            mapping[field] = int(saved["index"])
    return mapping


def _import_row(conn: sqlite3.Connection, import_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_imports WHERE id = ?", (import_id,)).fetchone()
    if not row:
        raise WholesaleError("عملية الرفع دي مش موجودة.", 404)
    return dict(row)


def _sheet_view(sheets: List[Dict[str, Any]], sheet_index: int, header_row: int) -> Dict[str, Any]:
    if sheet_index < 0 or sheet_index >= len(sheets):
        raise WholesaleError("الورقة المختارة مش موجودة في الملف.")
    rows = sheets[sheet_index]["rows"]
    header_row = max(1, min(int(header_row or 1), max(1, len(rows))))
    headers = list(rows[header_row - 1]) if rows else []
    width = max((len(r) for r in rows), default=0)
    headers = (headers + [""] * width)[:width]
    headers = [h or f"عمود {i + 1}" for i, h in enumerate(headers)]
    data = [r for r in rows[header_row:] if any(r)]
    return {
        "sheet_index": sheet_index,
        "sheet_name": sheets[sheet_index]["name"],
        "header_row": header_row,
        "headers": headers,
        "sample": data[:8],
        "data_rows": len(data),
        "top_rows": rows[:6],
    }


def start_import(
    conn: sqlite3.Connection, supplier_id: int, filename: str, content: bytes, user_id: Optional[int]
) -> Dict[str, Any]:
    supplier = _supplier(conn, supplier_id)
    try:
        sheets = read_sheet_file(filename, content)
    except SheetError as exc:
        raise WholesaleError(str(exc)) from None
    template = _template(conn, supplier_id)
    sheet_index = 0
    if template and template.get("sheet_name"):
        names = [s["name"] for s in sheets]
        if template["sheet_name"] in names:
            sheet_index = names.index(template["sheet_name"])
    rows = sheets[sheet_index]["rows"]
    header_row = _template_header_row(template, rows) if template else _detect_header_row(rows)
    cur = conn.execute(
        """
        INSERT INTO ws_imports(supplier_id, filename, sheet_name, status, matrix_json, header_row, created_by, created_at)
        VALUES(?, ?, ?, 'uploaded', ?, ?, ?, ?)
        """,
        (
            supplier_id,
            clean_text(filename, 180),
            sheets[sheet_index]["name"],
            json.dumps(sheets, ensure_ascii=False),
            header_row,
            user_id,
            utc_now(),
        ),
    )
    view = _sheet_view(sheets, sheet_index, header_row)
    mapping = _mapping_from_template(template, view["headers"]) if template else guess_mapping(view["headers"])
    return {
        "import_id": cur.lastrowid,
        "supplier": {"id": supplier["id"], "alias": supplier["alias"]},
        "filename": filename,
        "sheets": [{"index": i, "name": s["name"], "rows": len(s["rows"])} for i, s in enumerate(sheets)],
        "from_template": bool(template),
        "fields": [{"key": k, "label": l} for k, l in FIELDS],
        "mapping": mapping,
        **view,
    }


def import_columns(conn: sqlite3.Connection, import_id: int, sheet_index: int, header_row: int) -> Dict[str, Any]:
    record = _import_row(conn, import_id)
    sheets = json.loads(record["matrix_json"])
    view = _sheet_view(sheets, sheet_index, header_row)
    template = _template(conn, record["supplier_id"])
    mapping = _mapping_from_template(template, view["headers"]) if template else guess_mapping(view["headers"])
    return {"import_id": import_id, "mapping": mapping, **view}


def _validate_mapping(mapping: Dict[str, Any], width: int) -> Dict[str, Optional[int]]:
    clean: Dict[str, Optional[int]] = {}
    for key in FIELD_KEYS:
        value = mapping.get(key)
        if value in (None, "", -1):
            clean[key] = None
            continue
        index = int(value)
        if index < 0 or index >= width:
            raise WholesaleError("فيه عمود مختار مش موجود في الشيت.")
        clean[key] = index
    if clean["name"] is None:
        raise WholesaleError("لازم تختار عمود اسم الصنف.")
    if all(clean[k] is None for k in PRICE_FIELDS):
        raise WholesaleError("لازم تختار عمود سعر واحد على الأقل (قطعة أو دستة أو كرتونة).")
    used = [v for v in clean.values() if v is not None]
    if len(used) != len(set(used)):
        raise WholesaleError("نفس العمود مختار لأكتر من خانة.")
    return clean


def _extract_rows(rows: List[List[str]], header_row: int, mapping: Dict[str, Optional[int]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    good: List[Dict[str, Any]] = []
    problems: List[Dict[str, Any]] = []
    seen_codes: Dict[str, int] = {}
    seen_names: Dict[str, int] = {}

    def cell(row: List[str], field: str) -> str:
        index = mapping.get(field)
        return row[index] if index is not None and index < len(row) else ""

    for offset, row in enumerate(rows[header_row:], start=header_row + 1):
        if not any(row):
            continue
        name = clean_text(cell(row, "name"), 200)
        code = clean_text(cell(row, "code"), 80)
        base = {"row": offset, "name": name, "code": code}
        if not name:
            if any(cell(row, f) for f in PRICE_FIELDS):
                problems.append({**base, "issue": "اسم فاضي"})
            continue
        prices: Dict[str, Optional[float]] = {}
        bad = []
        for field in PRICE_FIELDS:
            raw = cell(row, field)
            try:
                prices[field] = parse_price(raw)
            except ValueError:
                bad.append(raw)
                prices[field] = None
        key = normalize_name(name)
        record = {
            **base,
            "name_key": key,
            "section": clean_text(cell(row, "section"), 80),
            **prices,
            "pieces_per_dozen": parse_count(cell(row, "pieces_per_dozen")) if mapping.get("pieces_per_dozen") is not None else None,
            "pieces_per_carton": parse_count(cell(row, "pieces_per_carton")) if mapping.get("pieces_per_carton") is not None else None,
        }
        if bad:
            problems.append({**record, "issue": "سعر مش رقم: " + "، ".join(bad[:3])})
            continue
        if all(prices[f] is None for f in PRICE_FIELDS):
            problems.append({**record, "issue": "سعر فاضي"})
            continue
        if code and code in seen_codes:
            problems.append({**record, "issue": f"كود متكرر (زي صف {seen_codes[code]})"})
            continue
        if not code and key in seen_names:
            problems.append({**record, "issue": f"اسم متكرر (زي صف {seen_names[key]})"})
            continue
        if code:
            seen_codes[code] = offset
        seen_names.setdefault(key, offset)
        good.append(record)
    return good, problems


def _numbers(key: str) -> set:
    return set(re.findall(r"\d+(?:\.\d+)?", key))


class _ProductIndex:
    """Token index to suggest same-item matches across suppliers quickly."""

    def __init__(self, products: List[Dict[str, Any]]):
        self.products = {p["id"]: p for p in products}
        self.tokens: Dict[str, set] = {}
        for product in products:
            for token in set(product["name_key"].split()):
                if len(token) > 1:
                    self.tokens.setdefault(token, set()).add(product["id"])

    def suggest(self, key: str, exclude: set, limit: int = 3) -> List[Dict[str, Any]]:
        counts: Dict[int, int] = {}
        for token in set(key.split()):
            ids = self.tokens.get(token)
            if not ids or len(ids) > 400:
                continue
            for pid in ids:
                counts[pid] = counts.get(pid, 0) + 1
        candidates = sorted(counts, key=lambda pid: -counts[pid])[:60]
        numbers = _numbers(key)
        scored = []
        for pid in candidates:
            if pid in exclude:
                continue
            other = self.products[pid]["name_key"]
            other_numbers = _numbers(other)
            if numbers and other_numbers and other_numbers != numbers:
                continue  # 400 مل vs 1 لتر: different sizes, never the same item
            ratio = SequenceMatcher(None, key, other).ratio()
            if ratio >= 0.72:
                scored.append((ratio, pid))
        scored.sort(reverse=True)
        return [
            {"product_id": pid, "name": self.products[pid]["name"], "score": round(ratio * 100)}
            for ratio, pid in scored[:limit]
        ]


def _analyse(conn: sqlite3.Connection, record: Dict[str, Any], mapping: Dict[str, Optional[int]], header_row: int, sheet_index: int) -> Dict[str, Any]:
    sheets = json.loads(record["matrix_json"])
    if sheet_index < 0 or sheet_index >= len(sheets):
        raise WholesaleError("الورقة المختارة مش موجودة في الملف.")
    rows = sheets[sheet_index]["rows"]
    width = max((len(r) for r in rows), default=0)
    mapping = _validate_mapping(mapping, width)
    if header_row < 1 or header_row > len(rows):
        raise WholesaleError("رقم صف العناوين مش صحيح.")
    good, problems = _extract_rows(rows, header_row, mapping)
    supplier_id = record["supplier_id"]
    offers = [dict(r) for r in conn.execute("SELECT * FROM ws_offers WHERE supplier_id = ?", (supplier_id,))]
    by_code = {o["supplier_code"]: o for o in offers if o["supplier_code"]}
    by_name: Dict[str, Dict[str, Any]] = {}
    for offer in offers:
        by_name.setdefault(offer["name_key"], offer)
    products = [dict(r) for r in conn.execute("SELECT id, name, name_key, code FROM ws_products")]
    product_by_code = {p["code"]: p for p in products if p["code"]}
    other_code: Dict[str, int] = {}
    for row in conn.execute("SELECT supplier_code, product_id FROM ws_offers WHERE supplier_id != ? AND supplier_code != ''", (supplier_id,)):
        other_code.setdefault(row["supplier_code"], row["product_id"])
    product_by_key: Dict[str, Dict[str, Any]] = {}
    for p in products:
        product_by_key.setdefault(p["name_key"], p)
    supplier_products = {o["product_id"] for o in offers}
    index = _ProductIndex(products)

    matched_offers: set = set()
    existing: List[Dict[str, Any]] = []
    new: List[Dict[str, Any]] = []
    claimed_products: Dict[int, int] = {}

    def find_offer(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if item.get("code") and item["code"] in by_code:
            return by_code[item["code"]]
        offer = by_name.get(item["name_key"])
        if offer and (not item.get("code") or not offer["supplier_code"]):
            return offer
        return None

    for item in problems:
        if item.get("name_key"):
            offer = find_offer(item)
            if offer:
                matched_offers.add(offer["id"])  # keep it; the row just has a bad price

    for item in good:
        offer = find_offer(item)
        if offer and offer["id"] not in matched_offers:
            matched_offers.add(offer["id"])
            changes = []
            for unit in UNITS:
                field = "price_" + unit
                if mapping.get(field) is None:
                    continue
                if (offer[field] or None) != (item[field] or None):
                    changes.append({"unit": unit, "old": offer[field], "new": item[field]})
            existing.append({**item, "offer_id": offer["id"], "product_id": offer["product_id"], "changes": changes, "reactivated": not offer["is_active"]})
            continue
        auto_id = None
        how = ""
        if item.get("code"):
            if item["code"] in product_by_code:
                auto_id, how = product_by_code[item["code"]]["id"], "نفس الكود"
            elif item["code"] in other_code:
                auto_id, how = other_code[item["code"]], "نفس الكود عند مورد تاني"
        if auto_id in supplier_products:
            auto_id = None
        suggestions: List[Dict[str, Any]] = []
        default_choice = 0
        if auto_id is None:
            same = product_by_key.get(item["name_key"])
            if same and same["id"] not in supplier_products:
                suggestions = [{"product_id": same["id"], "name": same["name"], "score": 100}]
                default_choice = same["id"]
            else:
                suggestions = index.suggest(item["name_key"], supplier_products)
        else:
            default_choice = auto_id
        new.append({**item, "auto_product_id": auto_id, "auto_reason": how, "suggestions": suggestions, "default_choice": default_choice})
        if default_choice:
            claimed_products[default_choice] = claimed_products.get(default_choice, 0) + 1

    removed = []
    for offer in offers:
        if offer["is_active"] and offer["id"] not in matched_offers:
            others = conn.execute(
                "SELECT COUNT(*) FROM ws_offers WHERE product_id = ? AND id != ? AND is_active = 1",
                (offer["product_id"], offer["id"]),
            ).fetchone()[0]
            removed.append({
                "offer_id": offer["id"],
                "product_id": offer["product_id"],
                "name": offer["supplier_name"],
                "code": offer["supplier_code"],
                "hides_product": others == 0,
            })
    warnings = []
    if mapping.get("price_carton") is not None and mapping.get("pieces_per_carton") is None:
        warnings.append("الشيت فيه سعر كرتونة من غير عدد القطع جوه الكرتونة. سعر القطعة جوه الكرتونة مش هيتحسب غير لو العدد متسجل على الصنف.")
    changed = [e for e in existing if e["changes"]]
    summary = {
        "rows": len(good) + len(problems),
        "new": len(new),
        "new_auto_linked": sum(1 for n in new if n["auto_product_id"]),
        "needs_confirmation": sum(1 for n in new if n["suggestions"] and not n["auto_product_id"]),
        "changed": len(changed),
        "unchanged": len(existing) - len(changed),
        "reactivated": sum(1 for e in existing if e["reactivated"]),
        "problems": len(problems),
        "removed": len(removed),
        "hidden_products": sum(1 for r in removed if r["hides_product"]),
    }
    return {
        "mapping": mapping,
        "header_row": header_row,
        "sheet_index": sheet_index,
        "sheet_name": sheets[sheet_index]["name"],
        "summary": summary,
        "new": new,
        "changed": changed,
        "existing": existing,
        "problems": problems,
        "removed": removed,
        "warnings": warnings,
    }


def preview_import(conn: sqlite3.Connection, import_id: int, mapping: Dict[str, Any], header_row: int, sheet_index: int) -> Dict[str, Any]:
    record = _import_row(conn, import_id)
    if record["status"] in ("applied", "cancelled"):
        raise WholesaleError("الرفع ده اتقفل. ارفع الشيت من جديد.")
    result = _analyse(conn, record, mapping, header_row, sheet_index)
    conn.execute(
        "UPDATE ws_imports SET status = 'previewed', mapping_json = ?, header_row = ?, sheet_name = ?, summary_json = ? WHERE id = ?",
        (json.dumps(result["mapping"]), header_row, result["sheet_name"], json.dumps(result["summary"], ensure_ascii=False), import_id),
    )
    result["import_id"] = import_id
    result.pop("existing")
    return result


def _save_template(conn: sqlite3.Connection, supplier_id: int, mapping: Dict[str, Optional[int]], headers: List[str], header_row: int, sheet_name: str) -> None:
    saved = {
        field: {"index": index, "header": headers[index] if index < len(headers) else ""}
        for field, index in mapping.items()
        if index is not None
    }
    conn.execute(
        """
        INSERT INTO ws_column_templates(supplier_id, mapping_json, header_row, sheet_name, updated_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(supplier_id) DO UPDATE SET mapping_json = excluded.mapping_json,
            header_row = excluded.header_row, sheet_name = excluded.sheet_name, updated_at = excluded.updated_at
        """,
        (supplier_id, json.dumps(saved, ensure_ascii=False), header_row, sheet_name, utc_now()),
    )


def apply_import(
    conn: sqlite3.Connection,
    import_id: int,
    decisions: Dict[str, int],
    save_template: bool = True,
) -> Dict[str, Any]:
    record = _import_row(conn, import_id)
    if record["status"] != "previewed":
        raise WholesaleError("لازم تعاين الشيت الأول قبل الحفظ." if record["status"] == "uploaded" else "الرفع ده اتحفظ أو اتلغى قبل كده.")
    mapping = json.loads(record["mapping_json"])
    sheets = json.loads(record["matrix_json"])
    names = [s["name"] for s in sheets]
    sheet_index = names.index(record["sheet_name"]) if record["sheet_name"] in names else 0
    result = _analyse(conn, record, mapping, record["header_row"], sheet_index)
    supplier_id = record["supplier_id"]
    settings = get_settings(conn)
    default_mode = settings.get("default_source_mode", "cheapest")
    now = utc_now()
    counts = {"updated": 0, "price_changes": 0, "created_products": 0, "linked": 0, "deactivated": 0, "skipped": 0}
    mapped = {f: mapping.get(f) is not None for f in FIELD_KEYS}

    for item in result["existing"]:
        offer = conn.execute("SELECT * FROM ws_offers WHERE id = ?", (item["offer_id"],)).fetchone()
        values = {
            "supplier_name": item["name"],
            "name_key": item["name_key"],
            "supplier_code": item["code"] or offer["supplier_code"],
            "section_text": item["section"] if mapped["section"] else offer["section_text"],
        }
        for unit in UNITS:
            field = "price_" + unit
            values[field] = item[field] if mapped[field] else offer[field]
            if mapped[field] and (offer[field] or None) != (item[field] or None):
                conn.execute(
                    "INSERT INTO ws_price_history(offer_id, unit, old_price, new_price, import_id, changed_at) VALUES(?, ?, ?, ?, ?, ?)",
                    (offer["id"], unit, offer[field], item[field], import_id, now),
                )
                counts["price_changes"] += 1
        for field in ("pieces_per_dozen", "pieces_per_carton"):
            values[field] = item[field] if mapped[field] and item[field] else offer[field]
        conn.execute(
            """
            UPDATE ws_offers SET supplier_name = :supplier_name, name_key = :name_key, supplier_code = :supplier_code,
                section_text = :section_text, price_piece = :price_piece, price_dozen = :price_dozen,
                price_carton = :price_carton, pieces_per_dozen = :pieces_per_dozen,
                pieces_per_carton = :pieces_per_carton, is_active = 1, last_import_id = :import_id, updated_at = :now
            WHERE id = :id
            """,
            {**values, "import_id": import_id, "now": now, "id": offer["id"]},
        )
        counts["updated"] += 1

    used_products = {row["product_id"] for row in conn.execute("SELECT product_id FROM ws_offers WHERE supplier_id = ?", (supplier_id,))}
    for item in result["new"]:
        key = str(item["row"])
        if item["auto_product_id"]:
            target = item["auto_product_id"]
        elif key in decisions:
            target = int(decisions[key] or 0)
        else:
            target = item["default_choice"] or 0
        if target:
            exists = conn.execute("SELECT 1 FROM ws_products WHERE id = ?", (target,)).fetchone()
            if not exists:
                target = 0
        if target and target in used_products:
            # This supplier already has a row on that product; keep both items apart.
            target = 0
        if not target:
            section_id = section_id_for(conn, item["section"]) if item["section"] else None
            target = conn.execute(
                """
                INSERT INTO ws_products(name, name_key, code, section_id, pieces_per_dozen, pieces_per_carton,
                    is_visible, source_mode, created_by, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, 1, ?, 'import', ?, ?)
                """,
                (item["name"], item["name_key"], item["code"], section_id, item["pieces_per_dozen"], item["pieces_per_carton"], default_mode, now, now),
            ).lastrowid
            counts["created_products"] += 1
        else:
            counts["linked"] += 1
            conn.execute(
                """
                UPDATE ws_products SET
                    pieces_per_dozen = COALESCE(pieces_per_dozen, ?),
                    pieces_per_carton = COALESCE(pieces_per_carton, ?),
                    code = CASE WHEN code = '' THEN ? ELSE code END,
                    updated_at = ? WHERE id = ?
                """,
                (item["pieces_per_dozen"], item["pieces_per_carton"], item["code"], now, target),
            )
        conn.execute(
            """
            INSERT INTO ws_offers(product_id, supplier_id, supplier_code, supplier_name, name_key, section_text,
                price_piece, price_dozen, price_carton, pieces_per_dozen, pieces_per_carton, is_active,
                last_import_id, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                target, supplier_id, item["code"], item["name"], item["name_key"], item["section"],
                item["price_piece"], item["price_dozen"], item["price_carton"],
                item["pieces_per_dozen"], item["pieces_per_carton"], import_id, now, now,
            ),
        )
        used_products.add(target)

    for item in result["removed"]:
        conn.execute("UPDATE ws_offers SET is_active = 0, updated_at = ? WHERE id = ?", (now, item["offer_id"]))
        counts["deactivated"] += 1

    if save_template:
        headers = _sheet_view(sheets, sheet_index, record["header_row"])["headers"]
        _save_template(conn, supplier_id, mapping, headers, record["header_row"], record["sheet_name"])
    counts["skipped"] = len(result["problems"])
    summary = {**result["summary"], "applied": counts}
    conn.execute(
        "UPDATE ws_imports SET status = 'applied', applied_at = ?, summary_json = ?, matrix_json = '[]' WHERE id = ?",
        (now, json.dumps(summary, ensure_ascii=False), import_id),
    )
    return {"import_id": import_id, "summary": summary}


def cancel_import(conn: sqlite3.Connection, import_id: int) -> None:
    record = _import_row(conn, import_id)
    if record["status"] == "applied":
        raise WholesaleError("الرفع ده اتحفظ خلاص.")
    conn.execute("UPDATE ws_imports SET status = 'cancelled', matrix_json = '[]' WHERE id = ?", (import_id,))


def list_imports(conn: sqlite3.Connection, limit: int = 30) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT i.id, i.supplier_id, s.alias AS supplier_alias, i.filename, i.sheet_name, i.status,
               i.summary_json, i.created_at, i.applied_at
        FROM ws_imports i JOIN ws_suppliers s ON s.id = i.supplier_id
        WHERE i.status IN ('applied')
        ORDER BY i.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["summary"] = json.loads(item.pop("summary_json") or "{}")
        result.append(item)
    return result


def get_template(conn: sqlite3.Connection, supplier_id: int) -> Optional[Dict[str, Any]]:
    _supplier(conn, supplier_id)
    return _template(conn, supplier_id)


def delete_template(conn: sqlite3.Connection, supplier_id: int) -> None:
    conn.execute("DELETE FROM ws_column_templates WHERE supplier_id = ?", (supplier_id,))


# ------------------------------------------------------------------- products


def _product(conn: sqlite3.Connection, product_id: int) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT p.*, s.name AS section_name FROM ws_products p LEFT JOIN ws_sections s ON s.id = p.section_id WHERE p.id = ?",
        (product_id,),
    ).fetchone()
    if not row:
        raise WholesaleError("الصنف مش موجود.", 404)
    return dict(row)


def _tier(conn: sqlite3.Connection, tier_id: Optional[int]) -> Dict[str, Any]:
    if tier_id:
        row = conn.execute("SELECT * FROM ws_customer_tiers WHERE id = ?", (tier_id,)).fetchone()
        if not row:
            raise WholesaleError("الفئة مش موجودة.", 404)
        return dict(row)
    return default_tier(conn)


def list_products(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    supplier_id: Optional[int] = None,
    section_id: Optional[int] = None,
    status: str = "",
    tier_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 50,
) -> Dict[str, Any]:
    tier = _tier(conn, tier_id)
    settings = get_settings(conn)
    sql = "SELECT p.*, s.name AS section_name FROM ws_products p LEFT JOIN ws_sections s ON s.id = p.section_id WHERE 1 = 1"
    params: List[Any] = []
    if q:
        key = normalize_name(q)
        sql += " AND (p.name_key LIKE ? OR p.code LIKE ? OR p.id IN (SELECT product_id FROM ws_offers WHERE name_key LIKE ? OR supplier_code LIKE ?))"
        params += [f"%{key}%", f"%{q.strip()}%", f"%{key}%", f"%{q.strip()}%"]
    if supplier_id:
        sql += " AND p.id IN (SELECT product_id FROM ws_offers WHERE supplier_id = ? AND is_active = 1)"
        params.append(supplier_id)
    if section_id:
        sql += " AND p.section_id = ?"
        params.append(section_id)
    sql += " ORDER BY p.name_key"
    products = [dict(row) for row in conn.execute(sql, params)]
    ids = [p["id"] for p in products]
    offers = load_offers(conn, ids)
    tier_prices = load_tier_prices(conn, ids)
    rows = []
    counts = {"all": len(products), "available": 0, "hidden": 0, "no_supplier": 0, "warnings": 0, "multi_supplier": 0}
    for product in products:
        product_offers = offers.get(product["id"], [])
        priced = price_product(product, product_offers, tier, settings, tier_prices.get(product["id"]))
        active = [o for o in product_offers if o["is_active"]]
        item = {
            "id": product["id"],
            "name": product["name"],
            "code": product["code"],
            "section_name": product["section_name"],
            "image_url": _image_url(product),
            "is_visible": bool(product["is_visible"]),
            "source_mode": product["source_mode"],
            "has_custom_price": product["margin_percent"] is not None
            or any(product["fixed_price_" + u] is not None for u in UNITS)
            or bool(tier_prices.get(product["id"])),
            "suppliers": [{"id": o["supplier_id"], "alias": o["supplier_alias"]} for o in active],
            "available": priced["available"],
            "units": priced["units"],
            "warnings": priced["warnings"],
        }
        counts["available"] += 1 if priced["available"] else 0
        counts["hidden"] += 0 if product["is_visible"] else 1
        counts["no_supplier"] += 0 if active else 1
        counts["warnings"] += 1 if priced["warnings"] else 0
        counts["multi_supplier"] += 1 if len(active) > 1 else 0
        if status == "available" and not priced["available"]:
            continue
        if status == "hidden" and product["is_visible"]:
            continue
        if status == "no_supplier" and active:
            continue
        if status == "warnings" and not priced["warnings"]:
            continue
        if status == "multi_supplier" and len(active) < 2:
            continue
        rows.append(item)
    per_page = max(10, min(per_page, 200))
    total = len(rows)
    page = max(1, page)
    return {
        "tier": {"id": tier["id"], "name": tier["name"]},
        "counts": counts,
        "total": total,
        "page": page,
        "pages": max(1, -(-total // per_page)),
        "items": rows[(page - 1) * per_page: page * per_page],
    }


def _image_url(product: Dict[str, Any]) -> str:
    path = product.get("image_path") or ""
    if not path:
        return ""
    return path if path.startswith(("http://", "https://")) else f"/api/wholesale/images/{path}"


def product_detail(conn: sqlite3.Connection, product_id: int) -> Dict[str, Any]:
    product = _product(conn, product_id)
    settings = get_settings(conn)
    offers = load_offers(conn, [product_id]).get(product_id, [])
    tier_prices = load_tier_prices(conn, [product_id]).get(product_id, {})
    tiers = list_tiers(conn)
    history = [
        dict(row)
        for row in conn.execute(
            """
            SELECT h.unit, h.old_price, h.new_price, h.changed_at, s.alias AS supplier_alias
            FROM ws_price_history h JOIN ws_offers o ON o.id = h.offer_id JOIN ws_suppliers s ON s.id = o.supplier_id
            WHERE o.product_id = ? ORDER BY h.id DESC LIMIT 30
            """,
            (product_id,),
        )
    ]
    return {
        **{k: v for k, v in product.items() if k not in ("name_key",)},
        "is_visible": bool(product["is_visible"]),
        "image_url": _image_url(product),
        "offers": [
            {
                "id": o["id"],
                "supplier_id": o["supplier_id"],
                "supplier_alias": o["supplier_alias"],
                "supplier_code": o["supplier_code"],
                "supplier_name": o["supplier_name"],
                "price_piece": o["price_piece"],
                "price_dozen": o["price_dozen"],
                "price_carton": o["price_carton"],
                "pieces_per_dozen": o["pieces_per_dozen"],
                "pieces_per_carton": o["pieces_per_carton"],
                "is_active": bool(o["is_active"]),
                "updated_at": o["updated_at"],
            }
            for o in offers
        ],
        "tier_prices": {str(tid): prices for tid, prices in tier_prices.items()},
        "pricing": [price_product(product, offers, tier, settings, tier_prices) for tier in tiers],
        "history": history,
    }


def _optional_float(value: Any, low: float = 0) -> Optional[float]:
    if value in (None, ""):
        return None
    number = float(value)
    if number < low or number > 10_000_000:
        raise WholesaleError("فيه رقم برّه الحدود المسموحة.")
    return round(number, 4)


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    number = int(value)
    if number <= 0 or number > 100000:
        raise WholesaleError("عدد القطع لازم يكون رقم موجب.")
    return number


def update_product(conn: sqlite3.Connection, product_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    product = _product(conn, product_id)
    values: Dict[str, Any] = {}
    if "name" in data and data["name"] is not None:
        name = clean_text(data["name"], 200)
        if not name:
            raise WholesaleError("اسم الصنف مينفعش يبقى فاضي.")
        values["name"], values["name_key"] = name, normalize_name(name)
    if "code" in data and data["code"] is not None:
        values["code"] = clean_text(data["code"], 80)
    if "section" in data and data["section"] is not None:
        values["section_id"] = section_id_for(conn, data["section"])
    for field in ("pieces_per_dozen", "pieces_per_carton"):
        if field in data:
            values[field] = _optional_int(data[field])
    if "is_visible" in data and data["is_visible"] is not None:
        values["is_visible"] = 1 if data["is_visible"] else 0
    if "source_mode" in data and data["source_mode"] is not None:
        if data["source_mode"] not in ("cheapest", "manual"):
            raise WholesaleError("اختيار المورد غير صحيح.")
        values["source_mode"] = data["source_mode"]
    if "manual_supplier_id" in data:
        supplier = data["manual_supplier_id"]
        if supplier:
            has = conn.execute("SELECT 1 FROM ws_offers WHERE product_id = ? AND supplier_id = ?", (product_id, supplier)).fetchone()
            if not has:
                raise WholesaleError("المورد ده مش بيبيع الصنف ده.")
        values["manual_supplier_id"] = supplier or None
    if "margin_percent" in data:
        values["margin_percent"] = _optional_float(data["margin_percent"], -99)
    for unit in UNITS:
        field = "fixed_price_" + unit
        if field in data:
            values[field] = _optional_float(data[field])
    if "image_path" in data and data["image_path"] is not None:
        values["image_path"] = str(data["image_path"])[:300]
    mode = values.get("source_mode", product["source_mode"])
    manual = values.get("manual_supplier_id", product["manual_supplier_id"])
    if mode == "manual" and not manual:
        raise WholesaleError("اختار المورد لما يكون الاختيار يدوي.")
    if values:
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{k} = :{k}" for k in values)
        conn.execute(f"UPDATE ws_products SET {assignments} WHERE id = :id", {**values, "id": product_id})
    return product_detail(conn, product_id)


def create_manual_product(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    """Add one product by hand: it still needs a supplier and purchase prices."""
    name = clean_text(data.get("name"), 200)
    if not name:
        raise WholesaleError("اكتب اسم الصنف.")
    supplier_id = int(data.get("supplier_id") or 0)
    _supplier(conn, supplier_id)
    prices = {u: _optional_float(data.get("price_" + u)) for u in UNITS}
    if all(v is None for v in prices.values()):
        raise WholesaleError("اكتب سعر شراء واحد على الأقل.")
    now = utc_now()
    code = clean_text(data.get("code"), 80)
    settings = get_settings(conn)
    per_dozen = _optional_int(data.get("pieces_per_dozen"))
    per_carton = _optional_int(data.get("pieces_per_carton"))
    product_id = conn.execute(
        """
        INSERT INTO ws_products(name, name_key, code, section_id, pieces_per_dozen, pieces_per_carton, is_visible,
            source_mode, created_by, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)
        """,
        (
            name, normalize_name(name), code, section_id_for(conn, data.get("section") or ""),
            per_dozen, per_carton, 0 if data.get("is_visible") is False else 1,
            settings.get("default_source_mode", "cheapest"), now, now,
        ),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO ws_offers(product_id, supplier_id, supplier_code, supplier_name, name_key, section_text,
            price_piece, price_dozen, price_carton, pieces_per_dozen, pieces_per_carton, is_active, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (product_id, supplier_id, code, name, normalize_name(name), prices["piece"], prices["dozen"], prices["carton"], per_dozen, per_carton, now, now),
    )
    extra = {k: data[k] for k in ("margin_percent",) if data.get(k) not in (None, "")}
    if extra:
        update_product(conn, product_id, extra)
    return product_detail(conn, product_id)


def save_offer(conn: sqlite3.Connection, product_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """Add or edit one supplier's purchase prices for a product by hand."""
    _product(conn, product_id)
    supplier_id = int(data.get("supplier_id") or 0)
    supplier = _supplier(conn, supplier_id)
    prices = {u: _optional_float(data.get("price_" + u)) for u in UNITS}
    now = utc_now()
    existing = conn.execute("SELECT * FROM ws_offers WHERE product_id = ? AND supplier_id = ?", (product_id, supplier_id)).fetchone()
    active = 0 if data.get("is_active") is False else 1
    if existing:
        for unit in UNITS:
            if (existing["price_" + unit] or None) != (prices[unit] or None):
                conn.execute(
                    "INSERT INTO ws_price_history(offer_id, unit, old_price, new_price, import_id, changed_at) VALUES(?, ?, ?, ?, NULL, ?)",
                    (existing["id"], unit, existing["price_" + unit], prices[unit], now),
                )
        conn.execute(
            "UPDATE ws_offers SET price_piece = ?, price_dozen = ?, price_carton = ?, is_active = ?, updated_at = ? WHERE id = ?",
            (prices["piece"], prices["dozen"], prices["carton"], active, now, existing["id"]),
        )
    else:
        if all(v is None for v in prices.values()):
            raise WholesaleError("اكتب سعر شراء واحد على الأقل.")
        product = _product(conn, product_id)
        conn.execute(
            """
            INSERT INTO ws_offers(product_id, supplier_id, supplier_code, supplier_name, name_key, price_piece, price_dozen,
                price_carton, is_active, created_at, updated_at)
            VALUES(?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, supplier["id"], product["name"], product["name_key"], prices["piece"], prices["dozen"], prices["carton"], active, now, now),
        )
    return product_detail(conn, product_id)


def merge_products(conn: sqlite3.Connection, source_id: int, target_id: int) -> Dict[str, Any]:
    """Link two products that turned out to be the same item (source joins target)."""
    if source_id == target_id:
        raise WholesaleError("اختار صنف مختلف.")
    source, target = _product(conn, source_id), _product(conn, target_id)
    clash = conn.execute(
        """
        SELECT s.alias FROM ws_offers a JOIN ws_offers b ON a.supplier_id = b.supplier_id
        JOIN ws_suppliers s ON s.id = a.supplier_id WHERE a.product_id = ? AND b.product_id = ?
        """,
        (source_id, target_id),
    ).fetchone()
    if clash:
        raise WholesaleError(f"المورد «{clash['alias']}» عنده الصنفين دول كأصناف مختلفة، فمينفعش يتربطوا.")
    now = utc_now()
    conn.execute("UPDATE ws_offers SET product_id = ?, updated_at = ? WHERE product_id = ?", (target_id, now, source_id))
    conn.execute(
        "INSERT OR IGNORE INTO ws_tier_prices(product_id, tier_id, unit, price, updated_at) SELECT ?, tier_id, unit, price, ? FROM ws_tier_prices WHERE product_id = ?",
        (target_id, now, source_id),
    )
    conn.execute(
        """
        UPDATE ws_products SET pieces_per_dozen = COALESCE(pieces_per_dozen, ?), pieces_per_carton = COALESCE(pieces_per_carton, ?),
            code = CASE WHEN code = '' THEN ? ELSE code END,
            image_path = CASE WHEN image_path = '' THEN ? ELSE image_path END,
            section_id = COALESCE(section_id, ?), updated_at = ?
        WHERE id = ?
        """,
        (source["pieces_per_dozen"], source["pieces_per_carton"], source["code"], source["image_path"], source["section_id"], now, target_id),
    )
    conn.execute("DELETE FROM ws_products WHERE id = ?", (source_id,))
    return product_detail(conn, target["id"])


def split_offer(conn: sqlite3.Connection, offer_id: int) -> Dict[str, Any]:
    """Undo a wrong link: move one supplier offer out to its own product."""
    offer = conn.execute("SELECT * FROM ws_offers WHERE id = ?", (offer_id,)).fetchone()
    if not offer:
        raise WholesaleError("سعر المورد ده مش موجود.", 404)
    others = conn.execute("SELECT COUNT(*) FROM ws_offers WHERE product_id = ? AND id != ?", (offer["product_id"], offer_id)).fetchone()[0]
    if not others:
        raise WholesaleError("الصنف ده مالوش غير المورد ده.")
    source = _product(conn, offer["product_id"])
    now = utc_now()
    new_id = conn.execute(
        """
        INSERT INTO ws_products(name, name_key, code, section_id, pieces_per_dozen, pieces_per_carton, is_visible,
            source_mode, created_by, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, 1, ?, 'import', ?, ?)
        """,
        (offer["supplier_name"], offer["name_key"], offer["supplier_code"], source["section_id"], offer["pieces_per_dozen"], offer["pieces_per_carton"], source["source_mode"] if source["source_mode"] == "cheapest" else "cheapest", now, now),
    ).lastrowid
    conn.execute("UPDATE ws_offers SET product_id = ?, updated_at = ? WHERE id = ?", (new_id, now, offer_id))
    if source["manual_supplier_id"] == offer["supplier_id"]:
        conn.execute("UPDATE ws_products SET source_mode = 'cheapest', manual_supplier_id = NULL WHERE id = ?", (source["id"],))
    return product_detail(conn, new_id)


def set_tier_prices(conn: sqlite3.Connection, product_id: int, tier_id: int, prices: Dict[str, Any]) -> Dict[str, Any]:
    _product(conn, product_id)
    _tier(conn, tier_id)
    now = utc_now()
    for unit in UNITS:
        if unit not in prices:
            continue
        value = _optional_float(prices[unit])
        if value is None:
            conn.execute("DELETE FROM ws_tier_prices WHERE product_id = ? AND tier_id = ? AND unit = ?", (product_id, tier_id, unit))
        else:
            conn.execute(
                """
                INSERT INTO ws_tier_prices(product_id, tier_id, unit, price, updated_at) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(product_id, tier_id, unit) DO UPDATE SET price = excluded.price, updated_at = excluded.updated_at
                """,
                (product_id, tier_id, unit, value, now),
            )
    return product_detail(conn, product_id)


def bulk_update_products(conn: sqlite3.Connection, product_ids: List[int], data: Dict[str, Any]) -> int:
    allowed = {k: data[k] for k in ("is_visible", "source_mode", "section", "margin_percent") if k in data}
    if not allowed:
        raise WholesaleError("مفيش تعديل مختار.")
    if allowed.get("source_mode") == "manual":
        raise WholesaleError("الاختيار اليدوي للمورد بيتعمل لكل صنف لوحده.")
    for product_id in product_ids[:500]:
        update_product(conn, int(product_id), allowed)
    return len(product_ids[:500])


def summary(conn: sqlite3.Connection) -> Dict[str, Any]:
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "suppliers": one("SELECT COUNT(*) FROM ws_suppliers WHERE is_active = 1"),
        "products": one("SELECT COUNT(*) FROM ws_products"),
        "multi_supplier": one(
            "SELECT COUNT(*) FROM (SELECT product_id FROM ws_offers WHERE is_active = 1 GROUP BY product_id HAVING COUNT(*) > 1)"
        ),
        "no_supplier": one(
            "SELECT COUNT(*) FROM ws_products p WHERE NOT EXISTS (SELECT 1 FROM ws_offers o WHERE o.product_id = p.id AND o.is_active = 1)"
        ),
        "pending_subscriptions": one("SELECT COUNT(*) FROM ws_subscription_requests WHERE status = 'pending'"),
        "settings": get_settings(conn),
    }


# ---------------------------------------------------------------------- tiers


def save_tier(conn: sqlite3.Connection, data: Dict[str, Any], tier_id: Optional[int] = None) -> List[Dict[str, Any]]:
    now = utc_now()
    name = clean_text(data.get("name"), 60) if data.get("name") is not None else None
    margin_given = "margin_percent" in data
    margin = _optional_float(data.get("margin_percent"), -99) if margin_given else None
    try:
        if tier_id is None:
            if not name:
                raise WholesaleError("اكتب اسم الفئة.")
            order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 10 FROM ws_customer_tiers").fetchone()[0]
            tier_id = conn.execute(
                "INSERT INTO ws_customer_tiers(name, margin_percent, is_default, is_subscriber, sort_order, created_at, updated_at) VALUES(?, ?, 0, ?, ?, ?, ?)",
                (name, margin, 1 if data.get("is_subscriber") else 0, order, now, now),
            ).lastrowid
        else:
            current = _tier(conn, tier_id)
            if name is not None and not name:
                raise WholesaleError("اسم الفئة مينفعش يبقى فاضي.")
            conn.execute(
                "UPDATE ws_customer_tiers SET name = ?, margin_percent = ?, is_subscriber = ?, updated_at = ? WHERE id = ?",
                (
                    name or current["name"],
                    margin if margin_given else current["margin_percent"],
                    (1 if data["is_subscriber"] else 0) if data.get("is_subscriber") is not None else current["is_subscriber"],
                    now,
                    tier_id,
                ),
            )
    except sqlite3.IntegrityError:
        raise WholesaleError("فيه فئة بنفس الاسم.") from None
    if data.get("is_default"):
        conn.execute("UPDATE ws_customer_tiers SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END", (tier_id,))
    return list_tiers(conn)


def delete_tier(conn: sqlite3.Connection, tier_id: int) -> List[Dict[str, Any]]:
    tier = _tier(conn, tier_id)
    if tier["is_default"]:
        raise WholesaleError("مينفعش تمسح الفئة الافتراضية. خلّي فئة تانية افتراضية الأول.")
    moved = conn.execute("SELECT COUNT(*) FROM ws_trader_tiers WHERE tier_id = ?", (tier_id,)).fetchone()[0]
    if moved:
        raise WholesaleError(f"فيه {moved} تاجر في الفئة دي. انقلهم لفئة تانية الأول.")
    conn.execute("DELETE FROM ws_customer_tiers WHERE id = ?", (tier_id,))
    return list_tiers(conn)


def list_traders(conn: sqlite3.Connection, q: str = "", tier_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Rose customers with their wholesale tier (unassigned = default tier)."""
    default = default_tier(conn)
    sql = """
        SELECT c.id, c.name, c.phone, tt.tier_id, t.name AS tier_name
        FROM customers c
        LEFT JOIN ws_trader_tiers tt ON tt.customer_id = c.id
        LEFT JOIN ws_customer_tiers t ON t.id = tt.tier_id
        WHERE 1 = 1
    """
    params: List[Any] = []
    if q:
        digits = re.sub(r"\D", "", q.translate(_ARABIC_DIGITS))
        sql += " AND (c.name LIKE ?" + (" OR c.phone LIKE ?" if digits else "") + ")"
        params.append(f"%{q.strip()}%")
        if digits:
            params.append(f"%{digits[-9:]}%")
    if tier_id:
        if tier_id == default["id"]:
            sql += " AND (tt.tier_id = ? OR tt.tier_id IS NULL)"
        else:
            sql += " AND tt.tier_id = ?"
        params.append(tier_id)
    elif not q:
        sql += " AND tt.tier_id IS NOT NULL"
    sql += " ORDER BY c.name LIMIT ?"
    params.append(max(1, min(limit, 200)))
    rows = []
    for row in conn.execute(sql, params):
        item = dict(row)
        if item["tier_id"] is None:
            item["tier_id"], item["tier_name"], item["is_default_tier"] = default["id"], default["name"], True
        rows.append(item)
    return rows


def set_trader_tier(conn: sqlite3.Connection, customer_id: int, tier_id: int) -> Dict[str, Any]:
    if not conn.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
        raise WholesaleError("العميل مش موجود.", 404)
    tier = _tier(conn, tier_id)
    conn.execute(
        "INSERT INTO ws_trader_tiers(customer_id, tier_id, updated_at) VALUES(?, ?, ?) "
        "ON CONFLICT(customer_id) DO UPDATE SET tier_id = excluded.tier_id, updated_at = excluded.updated_at",
        (customer_id, tier_id, utc_now()),
    )
    return {"customer_id": customer_id, "tier_id": tier["id"], "tier_name": tier["name"]}


# --------------------------------------------------------------- subscription


def announcement_for_customer(conn: sqlite3.Connection, customer_id: int) -> Optional[Dict[str, Any]]:
    settings = get_settings(conn)
    if settings.get("announcement_enabled") != "1":
        return None
    from .wholesale_pricing import tier_for_customer

    if tier_for_customer(conn, customer_id).get("is_subscriber"):
        return None
    pending = conn.execute(
        "SELECT 1 FROM ws_subscription_requests WHERE customer_id = ? AND status = 'pending'", (customer_id,)
    ).fetchone()
    return {
        "title": settings.get("announcement_title", ""),
        "text": settings.get("announcement_text", ""),
        "request_pending": bool(pending),
    }


def request_subscription(conn: sqlite3.Connection, customer_id: int, note: str = "") -> Dict[str, Any]:
    if get_settings(conn).get("announcement_enabled") != "1":
        raise WholesaleError("الاشتراك مش متاح دلوقتي.")
    pending = conn.execute(
        "SELECT id FROM ws_subscription_requests WHERE customer_id = ? AND status = 'pending'", (customer_id,)
    ).fetchone()
    if pending:
        return {"id": pending["id"], "status": "pending", "already": True}
    request_id = conn.execute(
        "INSERT INTO ws_subscription_requests(customer_id, status, note, created_at) VALUES(?, 'pending', ?, ?)",
        (customer_id, clean_text(note, 300), utc_now()),
    ).lastrowid
    return {"id": request_id, "status": "pending", "already": False}


def list_subscription_requests(conn: sqlite3.Connection, status: str = "pending") -> List[Dict[str, Any]]:
    sql = """
        SELECT r.id, r.customer_id, c.name, c.phone, r.status, r.note, r.created_at, r.decided_at,
               t.name AS tier_name, ct.name AS current_tier_name
        FROM ws_subscription_requests r
        JOIN customers c ON c.id = r.customer_id
        LEFT JOIN ws_customer_tiers t ON t.id = r.tier_id
        LEFT JOIN ws_trader_tiers tt ON tt.customer_id = r.customer_id
        LEFT JOIN ws_customer_tiers ct ON ct.id = tt.tier_id
    """
    params: List[Any] = []
    if status in ("pending", "approved", "rejected"):
        sql += " WHERE r.status = ?"
        params.append(status)
    sql += " ORDER BY r.id DESC LIMIT 200"
    default = default_tier(conn)["name"]
    rows = []
    for row in conn.execute(sql, params):
        item = dict(row)
        item["current_tier_name"] = item["current_tier_name"] or default
        rows.append(item)
    return rows


def decide_subscription(conn: sqlite3.Connection, request_id: int, approve: bool, tier_id: Optional[int], user_id: Optional[int]) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ws_subscription_requests WHERE id = ?", (request_id,)).fetchone()
    if not row:
        raise WholesaleError("الطلب مش موجود.", 404)
    if row["status"] != "pending":
        raise WholesaleError("الطلب ده اتراجع قبل كده.")
    chosen = None
    if approve:
        if tier_id:
            chosen = _tier(conn, tier_id)
        else:
            found = conn.execute("SELECT * FROM ws_customer_tiers WHERE is_subscriber = 1 ORDER BY sort_order, id LIMIT 1").fetchone()
            if not found:
                raise WholesaleError("مفيش فئة متعلّمة إنها فئة المشتركين. اختار فئة.")
            chosen = dict(found)
        set_trader_tier(conn, row["customer_id"], chosen["id"])
    conn.execute(
        "UPDATE ws_subscription_requests SET status = ?, tier_id = ?, decided_at = ?, decided_by = ? WHERE id = ?",
        ("approved" if approve else "rejected", chosen["id"] if chosen else None, utc_now(), user_id, request_id),
    )
    return {"id": request_id, "status": "approved" if approve else "rejected", "tier_name": chosen["name"] if chosen else None}
