"""Extracción de pedidos desde Excel de proveedor (lista de precios / pedido).

Formato esperado (encabezado en la primera fila):
``Código | Descripción | Cantidad | Costo/Precio | venta | …``.

Se leen DOS precios cuando el archivo trae ambas columnas:
- ``costo``/``precio`` (compra): se usa como precio del pedido y como ``cost``.
- ``venta`` / ``precio venta`` (precio de venta): alimenta el ``unit_price``
  de los productos creados o actualizados del catálogo (``sale_price``).
Si solo hay una columna de precio, se usa la misma para compra y venta.

Lo mismo que los PDF: cada renglón se carga como un NUEVO pedido pendiente;
el stock no se toca hasta la recepción.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.infrastructure.importers.order_pdf import OrderPdfLine, OrderPdfMeta, OrderPdfResult

log = logging.getLogger(__name__)


_COLUMN_ALIASES = {
    "code": ("codigo", "código", "clave", "sku", "ref"),
    "desc": ("descripcion", "descripción", "producto", "articulo", "artículo", "nombre"),
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


def _normalize_header(cell) -> str:
    return " ".join(str(cell or "").strip().lower().split())


def _find_columns(row: tuple) -> dict[str, int]:
    """Ubica las columnas tipo precio/distintas a mano; sin alias de substring."""
    positions: dict[str, int] = {}
    cells = {  # cleaned index -> normalized header
        idx: _normalize_header(cell) for idx, cell in enumerate(row)
    }
    # 1) columnas conclusiveas (costo/compra, venta, etc.)
    for field in ("cost", "sale", "code", "desc", "qty"):
        for idx, header in cells.items():
            if field in positions:
                break
            if any(header == alias for alias in _COLUMN_ALIASES[field]):
                positions[field] = idx
                break
    # 2) "precio" a secas: compra si no hay costo; si ya hay costo, es venta.
    for field in ("bare_price",):
        for idx, header in cells.items():
            if any(header == alias for alias in _COLUMN_ALIASES[field]):
                positions[field] = idx
                break
    if "cost" not in positions and "bare_price" in positions:
        positions["cost"] = positions["bare_price"]
    if "sale" not in positions and "bare_price" in positions and positions["bare_price"] != positions.get("cost"):
        positions["sale"] = positions["bare_price"]
    positions.pop("bare_price", None)
    return positions


def _as_text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text.strip()


def _as_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(",", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_order_excel(path: str | Path) -> OrderPdfResult:
    """Lee un Excel de pedido y devuelve los renglones (mismo contrato que PDF).

    Necesita columna ``codigo`` y al menos una de precio (``costo``/``precio``);
    la columna de venta (``venta``/``precio venta``/…) es opcional y se usa
    como ``sale_price`` de cada renglón.
    """
    from openpyxl import load_workbook

    wb = load_workbook(str(path), data_only=True)
    if not wb.sheetnames:
        raise ValueError("El Excel no tiene hojas.")
    ws = wb[wb.sheetnames[0]]

    header_idx: dict[str, int] | None = None
    skipped_header = 0
    for row in ws.iter_rows(values_only=True):
        probe = _find_columns(tuple(row))
        if "code" in probe and "cost" in probe:
            header_idx = probe
            break
        skipped_header += 1
    if header_idx is None:
        raise ValueError(
            "No se encontraron las columnas esperadas (Código, Descripción y al menos una de Precio/Costo)."
        )

    meta = OrderPdfMeta(order_number=Path(path).stem.replace("_", "-"))
    lines: list[OrderPdfLine] = []
    seen: set[str] = set()
    skipped = 0
    desc_col = header_idx.get("desc", header_idx["code"])
    for row in ws.iter_rows(min_row=skipped_header + 2, values_only=True):
        if not row or not any(cell is not None for cell in row):
            skipped += 1
            continue
        code = _as_text(row[header_idx["code"]])
        description = _as_text(row[desc_col])
        if not code or not description:
            skipped += 1
            continue
        price = _as_decimal(row[header_idx["cost"]])
        if price is None or price <= 0:
            skipped += 1
            continue
        sale_price = None
        if "sale" in header_idx and header_idx["sale"] != header_idx["cost"]:
            sale_price = _as_decimal(row[header_idx["sale"]])
            if sale_price is not None and sale_price <= 0:
                sale_price = None
        qty_raw = row[header_idx["qty"]] if "qty" in header_idx else None
        try:
            qty = int(float(qty_raw)) if qty_raw is not None else 1
        except (TypeError, ValueError):
            qty = 1
        if qty <= 0:
            qty = 1
        if code.upper() in seen:
            continue
        seen.add(code.upper())
        lines.append(
            OrderPdfLine(
                code=code,
                description=description,
                quantity=qty,
                unit_price=price.quantize(Decimal("0.01")),
                sale_price=sale_price.quantize(Decimal("0.01")) if sale_price is not None else None,
            )
        )
    log.info("Pedido Excel: %d renglones, %d descartados, meta=%s", len(lines), skipped, meta)
    return OrderPdfResult(meta=meta, lines=lines, skipped=skipped)