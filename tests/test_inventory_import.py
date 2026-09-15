"""Pruebas del lector de xlsx y del importador masivo de inventario."""

from __future__ import annotations

import zipfile

import pytest
from sqlalchemy import select

from app.infrastructure.db import create_engine_for, create_session_factory
from app.infrastructure.importers.inventory import DatabaseNotEmptyError, import_inventory_xlsx
from app.infrastructure.importers.xlsx import read_inventory_xlsx
from app.infrastructure.orm import CategoryRow, ProductRow


def _make_xlsx(rows: list[list[object]], path: str) -> None:
    """Crea un .xlsx mínimo (sharedStrings + sheet1) con las filas dadas."""
    shared: dict[str, int] = {}
    cells: list[list[tuple[str, str, str]]] = []
    for row in rows:
        row_cells: list[tuple[str, str, str]] = []
        for idx, value in enumerate(row, start=1):
            col = chr(64 + idx)
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                row_cells.append((col, "n", str(value)))
            else:
                text = str(value)
                if text not in shared:
                    shared[text] = len(shared)
                row_cells.append((col, "s", str(shared[text])))
        cells.append(row_cells)

    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="%d">'
        + "".join(f"<si><t>{text}</t></si>" for text in shared)
        + "</sst>"
    ) % len(shared)
    sheet = ['<?xml version="1.0" encoding="UTF-8"?>']
    sheet.append('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>')
    for r, row_cells in enumerate(cells, start=1):
        inner = "".join(f'<c r="{c}{r}" t="{t}"><v>{v}</v></c>' for c, t, v in row_cells)
        sheet.append(f'<row r="{r}">{inner}</row>')
    sheet.append("</sheetData></worksheet>")

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        zf.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="Sheet1"/></sheets></workbook>')
        zf.writestr("xl/sharedStrings.xml", shared_xml)
        zf.writestr("xl/worksheets/sheet1.xml", "".join(sheet))


SAMPLE = [
    ["Código", "Producto", "P. Costo", "P. Venta", "P. Mayoreo", "Departamento", "Existencia", "Inv. Mínimo", "Inv. Máximo", "Tipo de Venta", "Proveedor"],
    [100202, "SLIDER EJE ROJO", "$40.00", "$100.00", "$0.00", "TRACCION", 3, 1, 5, "UNIDAD", ""],
    ["7790895008472", "Papas 100g", "$0.50", "$1,000.50", "$0.90", "VARIOS", 12, 0, 0, "UNIDAD", "Distribuidora X"],
    ["1010A", "FUSIBLE CLAVIJA 10A", 0.24, 3.0, "", "- Sin Departamento -", 88, 0, 0, "UNIDAD", ""],
    ["", "Fila sin código", "", "", "", "", "", "", "", "", ""],
]


@pytest.fixture()
def xlsx(tmp_path) -> str:
    path = str(tmp_path / "inventario.xlsx")
    _make_xlsx(SAMPLE, path)
    return path


def test_mapeo_de_filas(xlsx: str) -> None:
    rows = read_inventory_xlsx(xlsx)
    assert len(rows) == 3

    row = rows[0]
    assert row.code == "100202"
    assert float(row.unit_price) == 100.0
    assert float(row.cost or 0) == 40.0
    assert row.wholesale_price is None  # $0.00 se descarta
    assert row.department == "TRACCION"
    assert row.stock == 3
    assert row.min_stock == 1
    assert row.max_stock == 5

    papas = rows[1]
    assert papas.code == "7790895008472"
    assert float(papas.unit_price) == 1000.5  # "1,000.50" -> 1000.50
    assert float(papas.wholesale_price or 0) == 0.9
    assert papas.provider == "Distribuidora X"

    fusible = rows[2]
    assert fusible.department == ""  # "- Sin Departamento -"
    assert float(fusible.unit_price) == 3.0


def test_importa_xlsx_a_base_vacia(xlsx: str, tmp_path) -> None:
    db = str(tmp_path / "kiosco.db")
    engine = create_engine_for(db)
    result = import_inventory_xlsx(engine, xlsx)

    assert result.products == 3
    assert result.categories_created == 2  # TRACCION, VARIOS (Sin Departamento no crea)
    assert result.categories_total == 2

    with create_session_factory(engine)() as session:
        products = session.execute(select(ProductRow).order_by(ProductRow.code)).scalars().all()
        assert len(products) == 3
        assert all(p.active for p in products)
        by_code = {p.code: p for p in products}
        assert float(by_code["7790895008472"].wholesale_price or 0) == 0.9
        assert by_code["7790895008472"].category_id is not None
        assert by_code["1010A"].category_id is None
        assert by_code["100202"].stock == 3

        categories = session.execute(select(CategoryRow).order_by(CategoryRow.name)).scalars().all()
        assert {c.name for c in categories} == {"TRACCION", "VARIOS"}


def test_rechaza_base_con_productos(xlsx: str, tmp_path) -> None:
    db = str(tmp_path / "kiosco.db")
    engine = create_engine_for(db)
    import_inventory_xlsx(engine, xlsx)

    with pytest.raises(DatabaseNotEmptyError):
        import_inventory_xlsx(engine, xlsx)


def test_duplicados_se_descartan(tmp_path) -> None:
    rows = [
        ["Código", "Producto", "P. Costo", "P. Venta", "P. Mayoreo", "Departamento", "Existencia"],
        ["ABC", "Uno", "1", "2", "", "DEPT", 1],
        ["abc", "Uno duplicado", "1", "2", "", "DEPT", 2],
    ]
    path = str(tmp_path / "dup.xlsx")
    _make_xlsx(rows, path)
    result = read_inventory_xlsx(path)
    assert len(result) == 1
    assert result[0].stock == 1