"""Extracción de pedidos desde archivos de proveedor (Excel y CSV).

Formato fijo: la **fila 1 son encabezados** y su contenido se ignora por
completo. Las columnas se toman siempre por posición:

    1) Código   2) Cantidad   3) Descripción   4) Costo   5) Precio (venta)

Los datos se higienizan al leer: espacios de más, comillas, símbolos de
moneda (``$578,84``), separadores regionales, celdas vacías y códigos en
minúsculas. Si con esas posiciones no se obtiene ningún renglón (archivo con
otro orden de columnas o con filas de título antes del encabezado) se usa
como respaldo la detección de columnas por el texto de los encabezados.

Se leen DOS precios cuando el archivo trae ambas columnas:
- ``costo`` (columna 4): precio de compra del pedido y ``cost`` del producto.
- ``precio``/``venta`` (columna 5): precio de venta (``sale_price``).
Si solo hay una columna de precio, se usa la misma para compra y venta.

Lo mismo que los PDF: cada renglón se carga como un NUEVO pedido pendiente;
el stock no se toca hasta la recepción, donde la cantidad se suma a la
existencia.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

from app.infrastructure.importers.order_pdf import OrderPdfLine, OrderPdfMeta, OrderPdfResult
from app.infrastructure.importers.xlsx import _parse_money

log = logging.getLogger(__name__)

#: Columnas fijas (índice 0-based) del formato estándar de pedido.
FIXED_COLUMNS: dict[str, int] = {"code": 0, "qty": 1, "desc": 2, "cost": 3, "sale": 4}

_COLUMN_ALIASES = {
    "code": ("codigo", "clave", "sku", "ref"),
    "desc": ("descripcion", "producto", "articulo", "nombre"),
    "qty": ("cantidad", "cant", "piezas", "unidades"),
    "cost": ("costo", "cost", "compra", "precio compra", "precio de compra", "p.compra", "costo de compra"),
    "sale": (
        "venta",
        "precio venta",
        "precio de venta",
        "p.venta",
        "precio publico",
        "precio al publico",
        "publico",
        "mayoreo",
        "precio mayoreo",
        "precio sugerido",
        "sugerido",
    ),
    "bare_price": (
        "precio",
        "p.unit",
        "precio unit",
        "precio neto",
        "precio unitario",
        "importe",
        "price",
        "p.u",
    ),
}

#: Unidades que pueden acompañar a la cantidad (``3 pzas``, ``2 unidades``…).
_QTY_UNITS = (
    "unidades",
    "unidad",
    "piezas",
    "pieza",
    "pzas",
    "pza",
    "uds",
    "ud",
    "pcs",
    "kit",
)

_WS_RE = re.compile(r"\s+")
_QTY_RE = re.compile(r"[\d\s.,+\-]+")
_THOUSANDS_RE = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_DELIMITERS = ",;\t|"
_XLS_OLE_MAGIC = b"\xd0\xcf\x11\xe0"


# --------------------------------------------------------------------------- #
# Higienizado
# --------------------------------------------------------------------------- #


def _clean_text(value) -> str:
    """Normaliza una celda: espacios, comillas, caracteres de control."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    text = "".join(ch for ch in text if ch.isprintable())
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def _clean_code(value) -> str:
    """Código higienizado: sin espacios sobrantes y en mayúsculas."""
    return _clean_text(value).upper()


def _as_qty(value) -> int | None:
    """Cantidad entera > 0; None cuando la celda no es una cantidad."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        qty = int(round(float(value)))
        return qty if qty > 0 else None
    text = _clean_text(value).lower()
    if not text or not any(ch.isdigit() for ch in text):
        return None
    for unit in _QTY_UNITS:
        if text.endswith(unit):
            text = text[: -len(unit)].strip()
            break
    text = text.rstrip(". ")
    if not text or not _QTY_RE.fullmatch(text):
        return None
    if _THOUSANDS_RE.fullmatch(text):
        text = text.replace(",", "")
    amount = _parse_money(text)
    if amount is None:
        return None
    qty = int(amount)
    return qty if qty > 0 else None


def _as_money(value) -> Decimal | None:
    """Precio higienizado (``$578,84`` / ``1,234.56`` / ``1.234,56``)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = _clean_text(value)
    if not text:
        return None
    return _parse_money(text)


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"))


# --------------------------------------------------------------------------- #
# Lectura de filas (xlsx / csv)
# --------------------------------------------------------------------------- #


def _rows_from_xlsx(path: str | Path) -> list[list[object]]:
    """Primera hoja con contenido del libro."""
    from openpyxl import load_workbook

    wb = load_workbook(str(path), data_only=True)
    try:
        for name in wb.sheetnames:
            rows = [list(row) for row in wb[name].iter_rows(values_only=True)]
            if any(_clean_text(cell) for row in rows for cell in row):
                return rows
        return []
    finally:
        wb.close()


def _decode(data: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        pass
    line = next((line for line in sample.splitlines() if line.strip()), "")
    best, best_count = ",", 0
    for delimiter in _DELIMITERS:
        count = line.count(delimiter)
        if count > best_count:
            best, best_count = delimiter, count
    return best


def _rows_from_csv(path: str | Path) -> list[list[str]]:
    """CSV con detección de codificación (utf-8 / cp1252) y delimitador."""
    text = _decode(Path(path).read_bytes())
    delimiter = _sniff_delimiter(text[:8192])
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _read_rows(path: str | Path) -> list[list[object]]:
    """Filas crudas del archivo (Excel o CSV), sin interpretar."""
    with open(path, "rb") as handle:
        head = handle.read(8)
    if head.startswith(_XLS_OLE_MAGIC):
        raise ValueError(
            "El archivo es de Excel 97 (.xls). Ábralo en Excel y guárdelo como "
            ".xlsx o .csv, luego vuelva a cargarlo."
        )
    if Path(path).suffix.lower() in {".csv", ".txt", ".tsv"}:
        return _rows_from_csv(path)
    try:
        return _rows_from_xlsx(path)
    except Exception:  # noqa: BLE001 - un xlsx dañado puede ser un CSV renombrado
        log.warning("No se pudo abrir %s como Excel; se intenta como texto.", path, exc_info=True)
        rows = _rows_from_csv(path)
        if not rows:
            raise ValueError("El archivo no se pudo leer como Excel ni como CSV.")
        return rows


# --------------------------------------------------------------------------- #
# Encabezados (respaldo cuando las posiciones fijas no aplican)
# --------------------------------------------------------------------------- #


def _normalize_header(cell) -> str:
    """'Código:' → 'codigo'; quita acentos, signos y espacios de más."""
    text = _clean_text(cell).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


_HEADER_ALIASES = {
    field: {_normalize_header(alias) for alias in aliases} for field, aliases in _COLUMN_ALIASES.items()
}


def _find_columns(row) -> dict[str, int]:
    """Ubica las columnas por el texto de la fila de encabezados."""
    positions: dict[str, int] = {}
    cells = {idx: _normalize_header(cell) for idx, cell in enumerate(row)}
    for field in ("cost", "sale", "code", "desc", "qty"):
        for idx, header in cells.items():
            if field in positions:
                break
            if header and header in _HEADER_ALIASES[field]:
                positions[field] = idx
                break
    for idx, header in cells.items():
        if header and header in _HEADER_ALIASES["bare_price"]:
            positions["bare_price"] = idx
            break
    if "cost" not in positions and "bare_price" in positions:
        positions["cost"] = positions["bare_price"]
    if "sale" not in positions and "bare_price" in positions and positions["bare_price"] != positions.get("cost"):
        positions["sale"] = positions["bare_price"]
    positions.pop("bare_price", None)
    return positions


def _cell(row, index: int):
    return row[index] if 0 <= index < len(row) else None


# --------------------------------------------------------------------------- #
# Armado de renglones
# --------------------------------------------------------------------------- #


def _build_line(code: str, description: str, qty: int, cost: Decimal, sale: Decimal | None) -> OrderPdfLine:
    return OrderPdfLine(
        code=code,
        description=description,
        quantity=qty,
        unit_price=_quantize(cost),
        sale_price=_quantize(sale) if sale is not None and sale > 0 else None,
    )


def _parse_fixed(rows: list[list[object]], start: int) -> tuple[list[OrderPdfLine], int]:
    """Renglones con las posiciones fijas, ignorando la fila de encabezados."""
    lines: list[OrderPdfLine] = []
    seen: set[str] = set()
    skipped = 0
    for row in rows[start:]:
        if not any(_clean_text(cell) for cell in row):
            skipped += 1
            continue
        code = _clean_code(_cell(row, FIXED_COLUMNS["code"]))
        qty = _as_qty(_cell(row, FIXED_COLUMNS["qty"]))
        description = _clean_text(_cell(row, FIXED_COLUMNS["desc"]))
        cost = _as_money(_cell(row, FIXED_COLUMNS["cost"]))
        sale = _as_money(_cell(row, FIXED_COLUMNS["sale"]))
        if not code or not description or qty is None or cost is None or cost <= 0:
            skipped += 1
            continue
        if code in seen:
            continue
        seen.add(code)
        lines.append(_build_line(code, description, qty, cost, sale))
    return lines, skipped


def _parse_by_header(rows: list[list[object]]) -> tuple[list[OrderPdfLine], int] | None:
    """Respaldo: detecta la fila de encabezados y mapea columnas por nombre."""
    for index, row in enumerate(rows):
        positions = _find_columns(row)
        if "code" not in positions or "cost" not in positions:
            continue
        lines: list[OrderPdfLine] = []
        seen: set[str] = set()
        skipped = 0
        for data_row in rows[index + 1 :]:
            if not any(_clean_text(cell) for cell in data_row):
                skipped += 1
                continue
            code = _clean_code(_cell(data_row, positions["code"]))
            description = _clean_text(_cell(data_row, positions.get("desc", positions["code"])))
            cost = _as_money(_cell(data_row, positions["cost"]))
            if not code or not description or cost is None or cost <= 0:
                skipped += 1
                continue
            qty = _as_qty(_cell(data_row, positions["qty"])) if "qty" in positions else None
            sale = _as_money(_cell(data_row, positions["sale"])) if "sale" in positions else None
            if code in seen:
                continue
            seen.add(code)
            lines.append(_build_line(code, description, qty if qty is not None else 1, cost, sale))
        return lines, skipped
    return None


def parse_order_excel(path: str | Path) -> OrderPdfResult:
    """Lee un pedido (Excel o CSV) y devuelve sus renglones.

    La fila 1 se toma siempre como encabezados (su texto no importa) y las
    columnas se leen en orden fijo: código, cantidad, descripción, costo y
    precio de venta. Si con ese orden no se detecta ningún renglón, se
    intenta mapeando las columnas por el nombre de los encabezados.

    Levanta ``ValueError`` cuando el archivo ni siquiera se puede abrir; la
    ausencia de renglones se devuelve como ``lines`` vacío.
    """
    rows = _read_rows(path)
    if not rows:
        return OrderPdfResult(meta=OrderPdfMeta(order_number=Path(path).stem.replace("_", "-")), lines=[], skipped=0)

    candidates: list[tuple[list[OrderPdfLine], int, str]] = []
    # 1) Posiciones fijas ignorando la fila 1 (encabezados): el formato esperado.
    fixed_lines, fixed_skipped = _parse_fixed(rows, 1)
    candidates.append((fixed_lines, fixed_skipped, "fijo"))
    # 2) Respaldo: encabezados reconocibles (títulos antes del encabezado, otro orden).
    by_header = _parse_by_header(rows)
    if by_header is not None:
        candidates.append((by_header[0], by_header[1], "encabezado"))
    # 3) Archivo sin fila de encabezados: datos desde la fila 1.
    no_header_lines, no_header_skipped = _parse_fixed(rows, 0)
    candidates.append((no_header_lines, no_header_skipped, "sin encabezado"))

    lines, skipped, strategy = max(candidates, key=lambda item: len(item[0]))
    meta = OrderPdfMeta(order_number=Path(path).stem.replace("_", "-"))
    log.info(
        "Pedido %s: %d renglones, %d descartados, estrategia=%s",
        Path(path).name,
        len(lines),
        skipped,
        strategy,
    )
    return OrderPdfResult(meta=meta, lines=lines, skipped=skipped)
