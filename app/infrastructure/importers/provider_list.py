"""Importador de listas de precios de proveedores (.xlsx) con mapeo de columnas.

El usuario elige qué columna es el código, cuál la descripción y cuál el precio.
El área de proveedores es independiente del inventario: esto solo sirve para
cotizar pedidos sin tocar el catálogo.
"""

from __future__ import annotations

import unicodedata
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from app.infrastructure.importers.xlsx import RawRow, _parse_money, iter_rows


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """Columna disponible: letra (excel) y encabezado (fila 1)."""

    letter: str
    header: str


@dataclass(slots=True)
class ProviderImportPreview:
    """Vista previa de un libro: columnas, primeras filas y total de datos."""

    columns: list[ColumnInfo]
    sample: list[RawRow]
    row_count: int


@dataclass(frozen=True, slots=True)
class ProviderItemRow:
    """Artículo ya mapeado de la lista del proveedor."""

    code: str
    description: str
    price: Decimal


def preview_worksheet(path: str | Path, *, sample: int = 10) -> ProviderImportPreview:
    """Inspecciona el libro y devuelve las columnas disponibles y filas de muestra.

    La fila 1 se trata como encabezado; el resto como datos de la cotización.
    """
    columns: dict[str, str] = {}
    sample_rows: list[RawRow] = []
    count = 0
    with zipfile.ZipFile(path) as archive:
        for raw in iter_rows(archive):
            if raw.number == 1:
                for letter, value in raw.cells.items():
                    if value.strip():
                        columns[letter] = value.strip()
                continue
            count += 1
            if len(sample_rows) < sample:
                sample_rows.append(raw)
    letters = sorted(columns.keys())
    return ProviderImportPreview(
        columns=[ColumnInfo(letter=letter, header=columns[letter] or f"Columna {letter}") for letter in letters],
        sample=sample_rows,
        row_count=count,
    )


def read_provider_items_xlsx(
    path: str | Path,
    *,
    code_col: str,
    description_col: str,
    price_col: str,
) -> tuple[list[ProviderItemRow], int]:
    """Lee la lista de precios usando las columnas mapeadas por el usuario.

    Devuelve ``(rows, skipped)``. Se omiten filas sin código, sin descripción o
    con precio no positivo.
    """
    rows: list[ProviderItemRow] = []
    skipped = 0
    with zipfile.ZipFile(path) as archive:
        for raw in iter_rows(archive):
            if raw.number == 1:
                continue
            code = raw.cells.get(code_col, "").strip()
            description = raw.cells.get(description_col, "").strip()
            if not code or not description:
                skipped += 1
                continue
            price = _parse_money(raw.cells.get(price_col))
            if price is None or price <= 0:
                skipped += 1
                continue
            rows.append(ProviderItemRow(code=code.upper(), description=description, price=price))
    return rows, skipped


_CODE_HINTS = ("cod", "cód", "sku", "barras")
_DESCRIPTION_HINTS = ("desc", "prod", "articul", "art", "detalle", "nom", "name")
_PRICE_HINTS = ("precio", "prec", "p.v", "pv", "price", "importe", "valor")


def _normalize_header(header: str) -> str:
    text = unicodedata.normalize("NFKD", header or "").lower()
    return "".join(ch for ch in text if ch.isalnum())


def _matches(header: str, hints: Iterable[str]) -> bool:
    normalized = _normalize_header(header)
    if not normalized:
        return False
    return any(hint in normalized for hint in hints)


def guess_column(columns: list[ColumnInfo], *, code: bool = False, description: bool = False, price: bool = False) -> str | None:
    """Elige la letra más probable para un campo según el encabezado."""
    hints = _CODE_HINTS if code else _DESCRIPTION_HINTS if description else _PRICE_HINTS
    for column in columns:
        if _matches(column.header, hints):
            return column.letter
    return None