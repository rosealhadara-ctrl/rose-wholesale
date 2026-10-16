"""Write a simple .xlsx file (several sheets, bold header row, right-to-left) without extra libraries."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any, List, Sequence, Tuple
from xml.sax.saxutils import escape

_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, value: Any, bold: bool) -> str:
    style = ' s="1"' if bold else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style}/>'
    if isinstance(value, bool):
        value = "نعم" if value else "لا"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style}><v>{value!r}</v></c>' if isinstance(value, float) else f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = escape(_BAD_XML.sub("", str(value)))
    return f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(rows: Sequence[Sequence[Any]], header_rows: Sequence[int]) -> str:
    widths: List[int] = []
    for row in rows:
        for i, v in enumerate(row):
            length = min(60, len(str(v)) + 2 if v is not None else 2)
            if i >= len(widths):
                widths.append(length)
            else:
                widths[i] = max(widths[i], length)
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{max(8, w)}" customWidth="1"/>' for i, w in enumerate(widths))
    body = []
    for r, row in enumerate(rows):
        bold = r in header_rows
        cells = "".join(_cell(f"{_col(c)}{r + 1}", v, bold) for c, v in enumerate(row))
        body.append(f'<row r="{r + 1}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0" rightToLeft="1"/></sheetViews>'
        + (f"<cols>{cols}</cols>" if cols else "")
        + f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def build_xlsx(sheets: Sequence[Tuple[str, Sequence[Sequence[Any]], Sequence[int]]]) -> bytes:
    """``sheets``: (name, rows, indexes of bold header rows)."""
    names = []
    for name, _, _ in sheets:
        clean = re.sub(r"[\[\]:*?/\\]", " ", name)[:31] or "Sheet"
        while clean in names:
            clean = clean[:28] + str(len(names))
        names.append(clean)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(sheets)))
            + "</Types>"
        ))
        z.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ))
        z.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names))
            + "</sheets></workbook>"
        ))
        z.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
            + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ))
        z.writestr("xl/styles.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border/></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            "</styleSheet>"
        ))
        for i, (_, rows, headers) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(rows, headers))
    return buf.getvalue()
