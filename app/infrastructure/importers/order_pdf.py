"""Extracción de pedidos desde PDF de proveedor (formato MOZUKI / remitos).

El PDF del proveedor lista los artículos con columnas
``Código·Descripción Cant Precio Total Ref.`` que pypdf extrae concatenadas.
Se reconoce cada renglón por su patrón de cierre:

    <precio><cantidad> <total><ref NxM>

Ejemplo extraído:  ``KB034CANDADO… MOZUKI 203.701 203.701x10``
→ precio 203.70, cantidad 1, total 203.70, ref 1x10.

Solo se interpretan los renglones de producto; los totales y encabezados
("Sub-Total", "Descuento", …) se ignoran. El stock NO se toca aquí: el pedido
se persiste pendiente y las existencias solo cambian al recibirlo.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

log = logging.getLogger(__name__)

#: Desde ``1`` a ``4`` dígitos enteros + grupos de miles (``,`` o ``.``) + 2 decimales.
_AMOUNT = r"\d{1,4}(?:[.,]\d{2,3})*[.,]\d{2}"
_PRICE = (
    r"(?P<price>"
    + _AMOUNT
    + r")(?P<qty>\d{1,4})\s+(?P<total>"
    + _AMOUNT
    + r")(?P<ref>[0-9]+[xX][0-9]+)"
)

#: Código estilo proveedor (letras + dígitos, p. ej. ``KA959`` / ``ME899``),
#: concatenado con la descripción sin separador en la extracción de pypdf.
_CODE = r"[A-Za-z]{1,4}[0-9]{2,7}(?:-[A-Za-z0-9]+)?|[0-9]{5,10}"

_LINE_RE = re.compile(
    r"^\s*(?P<code>" + _CODE + r")(?P<desc>.+?)\s+" + _PRICE + r"\s*$"
)


def _to_decimal(text: str) -> Decimal | None:
    """'14.70' → Decimal('14.70'); '1,800.50' → Decimal('1800.50')."""
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


@dataclass(slots=True)
class OrderPdfLine:
    """Un renglón de producto reconocido en el PDF.

    ``unit_price`` es siempre el precio de COMPRA. ``sale_price`` (opcional)
    es el precio de VENTA cuando el archivo trae columna propia (Excel con
    ``Costo``/``Precio`` y ``venta``); los PDF de proveedor solo traen uno.
    """

    code: str
    description: str
    quantity: int
    unit_price: Decimal
    sale_price: Decimal | None = None


@dataclass(slots=True)
class OrderPdfMeta:
    """Datos de cabecera del pedido (los que se encuentran)."""

    order_number: str = ""
    supplier: str = ""
    issue_date: str = ""


@dataclass(slots=True)
class OrderPdfResult:
    meta: OrderPdfMeta
    lines: list[OrderPdfLine]
    skipped: int


def _extract_meta(text: str) -> OrderPdfMeta:
    meta = OrderPdfMeta()
    nro = re.search(r"(?m)^[ \t]*(?P<nro>\d{6,12})[ \t]*\\?n?[ \t]*Pedido Nro\\.?[ \t]*$", text)
    if nro:
        meta.order_number = nro.group("nro")
    else:
        nro2 = re.search(r"(?m)^[ \t]*(?P<nro>\d{6,12})[ \t]*$", text)
        if nro2:
            meta.order_number = nro2.group("nro")
    proveedor = re.search(r"(?m)^(?P<sup>[^\n]+)\n[^\n]*Direcci[oó]n[:\s]*$", text)
    if proveedor:
        meta.supplier = proveedor.group("sup").strip()
    fecha = re.search(r"(?im)Fecha Emisi[oó]n[:\s]*([0-9/]{6,10})", text)
    if fecha:
        meta.issue_date = fecha.group(1).replace("/", "/")
    return meta


def parse_order_pdf(path: str | Path) -> OrderPdfResult:
    """Lee un PDF de pedido y devuelve los renglones identificados.

    Levanta ValueError si el archivo no se puede leer; el llamador decide si
    la ausencia de renglones (result.lines vacío) es un error.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    lines: list[OrderPdfLine] = []
    skipped = 0
    seen: set[str] = set()
    for raw in text.splitlines():
        match = _LINE_RE.match(raw)
        if match is None:
            skipped += 1
            continue
        price = _to_decimal(match.group("price"))
        if price is None or price <= 0:
            skipped += 1
            continue
        qty = int(match.group("qty"))
        if qty <= 0:
            skipped += 1
            continue
        code = match.group("code").strip()
        if code in seen:
            continue
        seen.add(code)
        description = " ".join(match.group("desc").strip().split())
        lines.append(OrderPdfLine(code=code, description=description, quantity=qty, unit_price=price))
    log.info("Pedido PDF: %d renglones reconocidos, %d líneas descartadas, meta=%s", len(lines), skipped, _extract_meta(text))
    return OrderPdfResult(meta=_extract_meta(text), lines=lines, skipped=skipped)