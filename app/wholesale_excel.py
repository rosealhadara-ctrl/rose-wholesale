"""Read any supplier sheet (XLSX or CSV) into a plain matrix of text cells.

Supplier sheets come in different shapes, so this reader does not assume any
column names.  The admin maps columns to fields afterwards (see
``wholesale_service``).  Dependency-free: only the standard library.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List

SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

MAX_ROWS = 20000
MAX_COLUMNS = 60


class SheetError(ValueError):
    """A user-facing problem with the uploaded file (Arabic message)."""


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + (ord(char) - 64)
    return value - 1


def _shared_strings(archive: zipfile.ZipFile) -> List[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values = []
    for item in root.findall(f"{{{SHEET_NS}}}si"):
        values.append("".join(node.text or "" for node in item.iter(f"{{{SHEET_NS}}}t")))
    return values


def _sheet_paths(archive: zipfile.ZipFile) -> List[Dict[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relation.attrib.get("Id"): relation.attrib.get("Target", "")
        for relation in relations.findall(f"{{{REL_NS}}}Relationship")
    }
    sheets = []
    for sheet in workbook.findall(f".//{{{SHEET_NS}}}sheet"):
        relation_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id")
        target = targets.get(relation_id, "")
        if not target:
            continue
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        sheets.append({"name": sheet.attrib.get("name", ""), "path": target})
    return sheets


def _clean(value: str) -> str:
    text = str(value if value is not None else "").replace(" ", " ").strip()
    # Excel stores whole numbers as "12.0" or floats like "117.30000000000001".
    if re.fullmatch(r"-?\d+\.\d{6,}", text):
        text = repr(round(float(text), 4))
    if re.fullmatch(r"-?\d+\.0+", text):
        text = text.split(".")[0]
    return text


def _read_sheet(archive: zipfile.ZipFile, path: str, shared: List[str]) -> List[List[str]]:
    root = ET.fromstring(archive.read(path))
    matrix: List[List[str]] = []
    for row in root.findall(f".//{{{SHEET_NS}}}row"):
        if len(matrix) >= MAX_ROWS:
            break
        row_number = int(row.attrib.get("r", len(matrix) + 1))
        while len(matrix) < row_number - 1:
            matrix.append([])
        values: List[str] = []
        for cell in row.findall(f"{{{SHEET_NS}}}c"):
            index = _column_index(cell.attrib.get("r", "A1"))
            if index >= MAX_COLUMNS:
                continue
            while len(values) <= index:
                values.append("")
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(f"{{{SHEET_NS}}}t"))
            else:
                node = cell.find(f"{{{SHEET_NS}}}v")
                raw = node.text if node is not None and node.text is not None else ""
                if cell_type == "s" and raw:
                    try:
                        value = shared[int(raw)]
                    except (IndexError, ValueError):
                        value = ""
                else:
                    value = raw
            values[index] = _clean(value)
        matrix.append(values)
    return _trim(matrix)


def _trim(matrix: List[List[str]]) -> List[List[str]]:
    while matrix and not any(cell for cell in matrix[-1]):
        matrix.pop()
    width = 0
    for row in matrix:
        for index, cell in enumerate(row):
            if cell:
                width = max(width, index + 1)
    return [(row + [""] * width)[:width] for row in matrix]


def read_xlsx(content: bytes) -> List[Dict[str, object]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise SheetError("الملف مش شيت Excel سليم (xlsx).") from exc
    with archive:
        try:
            shared = _shared_strings(archive)
            sheets = []
            for sheet in _sheet_paths(archive):
                try:
                    rows = _read_sheet(archive, sheet["path"], shared)
                except KeyError:
                    continue
                sheets.append({"name": sheet["name"], "rows": rows})
        except (KeyError, ET.ParseError) as exc:
            raise SheetError("تعذّر قراءة الشيت. جرّب تحفظه من Excel تاني بصيغة xlsx.") from exc
    if not sheets:
        raise SheetError("الملف مافيهوش أي ورقة فيها بيانات.")
    return sheets


def read_csv(content: bytes) -> List[Dict[str, object]]:
    text = None
    for encoding in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SheetError("تعذّر قراءة ملف CSV.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for index, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if index >= MAX_ROWS:
            break
        rows.append([_clean(cell) for cell in row[:MAX_COLUMNS]])
    return [{"name": "CSV", "rows": _trim(rows)}]


def read_sheet_file(filename: str, content: bytes) -> List[Dict[str, object]]:
    """Return ``[{"name": sheet_name, "rows": [[cell, ...], ...]}, ...]``."""
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix in ("xlsx", "xlsm"):
        sheets = read_xlsx(content)
    elif suffix == "csv":
        sheets = read_csv(content)
    elif suffix == "xls":
        raise SheetError("صيغة xls القديمة مش مدعومة. افتح الشيت في Excel واحفظه بصيغة xlsx.")
    else:
        raise SheetError("الملف لازم يكون xlsx أو csv.")
    sheets = [sheet for sheet in sheets if sheet["rows"]]
    if not sheets:
        raise SheetError("الشيت فاضي.")
    return sheets
