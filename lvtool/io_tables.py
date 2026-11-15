"""Einlesen von Tabellen aus CSV, Excel und XML, ohne die Originaldateien zu verändern."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

SUPPORTED = {".csv", ".txt", ".tsv", ".xlsx", ".xlsm", ".xml"}


@dataclass
class Table:
    source: str            # Dateiname (relativ zum Kurs- bzw. Semesterordner)
    sheet: str | None      # Blattname bei Excel, Elementname bei XML
    headers: list[str]
    rows: list[dict] = field(default_factory=list)
    row_numbers: list[int] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.source} [{self.sheet}]" if self.sheet else self.source


class TableError(Exception):
    pass


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v if v != "" else None
    return v


def _unique_headers(raw: list) -> list[str]:
    out, seen = [], {}
    for i, h in enumerate(raw, start=1):
        h = str(h).strip() if h is not None and str(h).strip() else f"Spalte_{i}"
        if h in seen:
            seen[h] += 1
            h = f"{h}_{seen[h]}"
        else:
            seen[h] = 1
        out.append(h)
    return out


def _rows_to_table(source, sheet, matrix: list[list]) -> Table | None:
    header_idx = None
    for i, row in enumerate(matrix[:30]):
        filled = [c for c in row if _clean(c) is not None]
        strings = [c for c in filled if isinstance(c, str)]
        if len(filled) >= 2 and len(strings) >= max(2, len(filled) // 2):
            header_idx = i
            break
    if header_idx is None:
        return None
    headers = _unique_headers(matrix[header_idx])
    t = Table(source, sheet, headers)
    for n, row in enumerate(matrix[header_idx + 1:], start=header_idx + 2):
        vals = [_clean(c) for c in row] + [None] * (len(headers) - len(row))
        if all(v is None for v in vals):
            continue
        t.rows.append(dict(zip(headers, vals)))
        t.row_numbers.append(n)
    return t


def _read_csv(path: Path, source: str) -> list[Table]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = next((l for l in text.splitlines() if l.strip()), "")
    delim = max(";,\t|", key=first.count)
    matrix = [row for row in csv.reader(io.StringIO(text), delimiter=delim)]
    t = _rows_to_table(source, None, matrix)
    return [t] if t else []


def _read_xlsx(path: Path, source: str) -> list[Table]:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise TableError(f"{source}: Excel-Datei nicht lesbar ({e})") from e
    tables = []
    try:
        for ws in wb.worksheets:
            matrix = [list(r) for r in ws.iter_rows(values_only=True)]
            t = _rows_to_table(source, ws.title, matrix)
            if t and t.rows:
                tables.append(t)
    finally:
        wb.close()
    return tables


def _local(tag) -> str:
    return tag.split("}", 1)[-1] if isinstance(tag, str) else ""


def _flatten(el, prefix="", out=None) -> dict:
    out = {} if out is None else out
    for k, v in el.attrib.items():
        out[prefix + _local(k)] = v
    children = [c for c in el if isinstance(c.tag, str)]
    if not children:
        if el.text and el.text.strip() and prefix:
            out[prefix.rstrip(".")] = el.text.strip()
        return out
    for c in children:
        name = _local(c.tag)
        key = name if not prefix else prefix + name
        if list(c) or c.attrib:
            _flatten(c, key + ".", out)
            if c.text and c.text.strip():
                out[key] = c.text.strip()
        else:
            val = c.text.strip() if c.text and c.text.strip() else None
            if key in out:
                i = 2
                while f"{key}_{i}" in out:
                    i += 1
                key = f"{key}_{i}"
            out[key] = val
    return out


SS = "urn:schemas-microsoft-com:office:spreadsheet"


def _read_spreadsheetml(root, source: str) -> list[Table]:
    """Excel-2003-XML (\"XML-Kalkulationstabelle\"), wie es Universitätssysteme oft exportieren."""
    tables = []
    for ws in root.iter(f"{{{SS}}}Worksheet"):
        matrix = []
        for row in ws.iter(f"{{{SS}}}Row"):
            vals, col = [], 0
            for cell in row.findall(f"{{{SS}}}Cell"):
                idx = cell.get(f"{{{SS}}}Index")
                if idx:
                    while col < int(idx) - 1:
                        vals.append(None)
                        col += 1
                data = cell.find(f"{{{SS}}}Data")
                v = data.text if data is not None else None
                if data is not None and v is not None and data.get(f"{{{SS}}}Type") == "Number":
                    n = float(v)
                    v = int(n) if n.is_integer() else n
                vals.append(v)
                col += 1
            matrix.append(vals)
        t = _rows_to_table(source, ws.get(f"{{{SS}}}Name"), matrix)
        if t and t.rows:
            tables.append(t)
    return tables


def _read_xml(path: Path, source: str) -> list[Table]:
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.parse(str(path), parser).getroot()
    except Exception as e:  # noqa: BLE001
        raise TableError(f"{source}: XML nicht lesbar ({e})") from e
    if root.tag == f"{{{SS}}}Workbook":
        return _read_spreadsheetml(root, source)
    best = None
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        groups: dict[str, list] = {}
        for c in parent:
            if isinstance(c.tag, str) and (len(c) or c.attrib):
                groups.setdefault(_local(c.tag), []).append(c)
        for name, items in groups.items():
            if len(items) >= 1 and (best is None or len(items) > len(best[1])):
                best = (name, items)
    if not best:
        return []
    name, items = best
    rows = [_flatten(i) for i in items]
    headers: list[str] = []
    for r in rows:
        for k in r:
            if k not in headers:
                headers.append(k)
    t = Table(source, name, headers)
    for n, r in enumerate(rows, start=1):
        t.rows.append({h: _clean(r.get(h)) for h in headers})
        t.row_numbers.append(n)
    return [t]


def read_tables(path: Path, source: str | None = None) -> list[Table]:
    path = Path(path)
    source = source or path.name
    ext = path.suffix.lower()
    if ext in (".csv", ".txt", ".tsv"):
        return _read_csv(path, source)
    if ext in (".xlsx", ".xlsm"):
        return _read_xlsx(path, source)
    if ext == ".xml":
        return _read_xml(path, source)
    if ext == ".xls":
        raise TableError(f"{source}: altes .xls-Format wird nicht unterstützt; bitte in Excel als .xlsx speichern.")
    raise TableError(f"{source}: Dateiformat {ext} wird nicht unterstützt.")


# --------------------------------------------------------------------------- Werte

_DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})"), ("d", "m", "y")),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})(?!\d)"), ("d", "m", "yy")),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})"), ("d", "m", "y")),
]


def parse_date_candidates(v) -> list[date]:
    """Alle plausiblen Lesarten; bei 03/04/2026 sowohl 3. April als auch 4. März."""
    d = parse_date(v)
    out = [d] if d else []
    if isinstance(v, str):
        m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})", v)
        if m:
            a, b, y = (int(x) for x in m.groups())
            for dd, mm in ((a, b), (b, a)):
                try:
                    c = date(y, mm, dd)
                except ValueError:
                    continue
                if c not in out:
                    out.append(c)
    return out


def parse_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:  # Excel-Seriennummer
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).date()
    s = str(v).strip()
    s = re.sub(r"^(mo|di|mi|do|fr|sa|so)[a-z]*\.?,?\s*", "", s, flags=re.I)
    for rx, order in _DATE_PATTERNS:
        m = rx.match(s)
        if m:
            parts = dict(zip(order, m.groups()))
            y = int(parts.get("y") or 2000 + int(parts["yy"]))
            try:
                return date(y, int(parts["m"]), int(parts["d"]))
            except ValueError:
                return None
    return None


def parse_number(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", s):
        s = s.replace(".", "")
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_header(h: str) -> str:
    h = str(h).lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        h = h.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", h)
