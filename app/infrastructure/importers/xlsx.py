"""Lector mínimo de archivos .xlsx usando solo la biblioteca estándar.

Soporta *shared strings* y valores numéricos/inline del libro, que es todo lo
que los inventarios exportados desde Excel/hojas de cálculo necesitan.
"""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


@dataclass(frozen=True, slots=True)
class RawRow:
    """Fila cruda del libro: posición (1-based) y celdas por columna (letra)."""

    number: int
    cells: dict[str, str]


def _column_letter(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.get("t")
    value = cell.find(_NS + "v")
    if cell_type == "s" and value is not None:
        index = int(value.text or "0")
        return shared[index] if index < len(shared) else ""
    if cell_type == "inlineStr":
        inline = cell.find(_NS + "is")
        return "".join(t.text or "" for t in inline.iter(_NS + "t")) if inline is not None else ""
    return value.text if value is not None else ""


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(t.text or "" for t in si.iter(_NS + "t")) for si in root.iter(_NS + "si")]


def iter_rows(archive: zipfile.ZipFile, sheet: str = "xl/worksheets/sheet1.xml") -> Iterator[RawRow]:
    """Itera las filas del libro en orden de aparición."""
    root = ET.fromstring(archive.read(sheet))
    shared = _shared_strings(archive)
    for row in root.iter(_NS + "row"):
        number = int(row.get("r", "0"))
        cells: dict[str, str] = {}
        for cell in row.iter(_NS + "c"):
            cells[_column_letter(cell.get("r", ""))] = _cell_text(cell, shared)
        yield RawRow(number=number, cells=cells)


def _parse_money(value: str | None) -> Decimal | None:
    """Convierte '12.5', '1,234.56', '$40.00' o 'USD 1.234,56' a Decimal."""
    if value is None:
        return None
    clean = "".join(ch for ch in value.strip() if ch.isdigit() or ch in ".,")
    if not clean:
        return None
    last_dot = clean.rfind(".")
    last_comma = clean.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        if last_dot > last_comma:
            clean = clean.replace(",", "").replace(".", ".")
        else:
            clean = clean.replace(".", "").replace(",", ".")
    elif last_comma > 0 and clean.count(",") == 1:
        clean = clean.replace(",", ".")
    try:
        return Decimal(clean or "0")
    except InvalidOperation:
        return None


def _parse_int(value: str | None) -> int:
    if value is None:
        return 0
    digits = "".join(ch for ch in value if ch.isdigit() or ch == "-")
    try:
        return int(digits or "0")
    except ValueError:
        return 0


@dataclass(frozen=True, slots=True)
class InventoryRow:
    """Fila ya mapeada del inventario del kiosco."""

    code: str
    name: str
    unit_price: Decimal
    cost: Decimal | None = None
    wholesale_price: Decimal | None = None
    department: str = ""
    stock: int = 0
    min_stock: int = 0
    max_stock: int = 0
    type_of_sale: str = ""
    provider: str = ""


SIN_DEPARTAMENTO = "- Sin Departamento -"


def _clean_money(value: str | None) -> Decimal | None:
    parsed = _parse_money(value)
    return None if parsed is None or parsed <= 0 else parsed


def map_inventory_row(raw: RawRow) -> InventoryRow | None:
    cells = raw.cells
    code = cells.get("A", "").strip()
    name = cells.get("B", "").strip()
    if not code or not name:
        return None
    unit_price = _parse_money(cells.get("D")) or Decimal("0")
    department = cells.get("F", "").strip()
    if department == SIN_DEPARTAMENTO:
        department = ""
    wholesale = _parse_money(cells.get("E"))
    return InventoryRow(
        code=code.upper(),
        name=name,
        unit_price=unit_price,
        cost=_clean_money(cells.get("C")),
        wholesale_price=None if wholesale is None or wholesale <= 0 else wholesale,
        department=department,
        stock=_parse_int(cells.get("G")),
        min_stock=_parse_int(cells.get("H")),
        max_stock=_parse_int(cells.get("I")),
        type_of_sale=cells.get("J", "").strip(),
        provider=cells.get("K", "").strip(),
    )


def read_inventory_xlsx(path: str | Path) -> list[InventoryRow]:
    """Lee un xlsx de inventario y devuelve las filas mapeadas (valor >= 1 en precio)."""
    rows: list[InventoryRow] = []
    seen: set[str] = set()
    with zipfile.ZipFile(path) as archive:
        for raw in iter_rows(archive):
            if raw.number == 1:
                continue
            mapped = map_inventory_row(raw)
            if mapped is None:
                continue
            if mapped.code in seen:
                continue
            seen.add(mapped.code)
            rows.append(mapped)
    return rows