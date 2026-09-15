"""Importación de pedido (PDF/Excel) con columnas de costo y precio de venta."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import pytest

from app.application.commands import (
    CreateProductCommand,
    ImportedOrderLineRequest,
    LoadPurchaseOrderCommand,
)
from app.application.queries import GetCatalogQuery, ListPurchaseOrdersQuery
from app.infrastructure.importers.order_excel import parse_order_excel


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app


def _write_xlsx(path, header, rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    wb.save(str(path))


# --------------------------------------------------------------------------- #
# Lectura de columnas en Excel
# --------------------------------------------------------------------------- #


def test_excel_lee_costo_y_venta(tmp_path) -> None:
    path = tmp_path / "pedido.xlsx"
    _write_xlsx(
        path,
        ("Código", "Descripción", "Cantidad", "Precio", "venta"),
        [("KA020", "PARRILLA FT150 NEGRO MOZUKI M", 3, 178.58, 320)],
    )
    result = parse_order_excel(path)
    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.unit_price == Decimal("178.58")
    assert line.sale_price == Decimal("320.00")


def test_excel_costo_y_precio_suelto_es_venta(tmp_path) -> None:
    """Con 'Costo' y 'Precio', el Precio suelto se trata como precio de venta."""
    path = tmp_path / "pedido.xlsx"
    _write_xlsx(
        path,
        ("Código", "Descripción", "Cantidad", "Costo", "Precio"),
        [("KB034", "CANDADO", 2, 100, 250)],
    )
    result = parse_order_excel(path)
    line = result.lines[0]
    assert line.unit_price == Decimal("100.00")
    assert line.sale_price == Decimal("250.00")


def test_excel_solo_precio_no_deja_venta(tmp_path) -> None:
    path = tmp_path / "pedido.xlsx"
    _write_xlsx(
        path,
        ("Código", "Descripción", "Cantidad", "Precio"),
        [("KA031", "CABLE CHICOTE", 5, 21.90)],
    )
    result = parse_order_excel(path)
    line = result.lines[0]
    assert line.unit_price == Decimal("21.90")
    assert line.sale_price is None


# --------------------------------------------------------------------------- #
# Handler: creación y actualización de productos
# --------------------------------------------------------------------------- #


def test_load_order_crea_producto_con_costo_y_precio_venta(services) -> None:
    services.commands.execute(
        LoadPurchaseOrderCommand(
            provider_name="MOZUKI MEXICO",
            lines=(
                ImportedOrderLineRequest(
                    code="KA020",
                    description="PARRILLA FT150 NEGRO MOZUKI M",
                    quantity=3,
                    unit_price="178.58",
                    sale_price="320",
                ),
            ),
        )
    )
    products = services.queries.ask(GetCatalogQuery())
    product = next(p for p in products if p.code == "KA020")
    assert product.cost.as_decimal() == Decimal("178.58")
    assert product.unit_price.as_decimal() == Decimal("320.00")

    orders = services.queries.ask(ListPurchaseOrdersQuery())
    assert len(orders) == 1
    assert orders[0].lines[0].unit_price.as_decimal() == Decimal("178.58")


def test_load_order_actualiza_producto_existente(services) -> None:
    services.commands.execute(
        CreateProductCommand(code="KA021", name="OTRO NEGRO", unit_price="500", cost="200", stock=10)
    )
    services.commands.execute(
        LoadPurchaseOrderCommand(
            provider_name="MOZUKI MEXICO",
            lines=(
                ImportedOrderLineRequest(
                    code="KA021",
                    description="OTRO NEGRO",
                    quantity=1,
                    unit_price="150",
                    sale_price="330",
                ),
            ),
        )
    )
    product = next(p for p in services.queries.ask(GetCatalogQuery()) if p.code == "KA021")
    assert product.cost.as_decimal() == Decimal("150.00")
    assert product.unit_price.as_decimal() == Decimal("330.00")


def test_load_order_sin_venta_solo_actualiza_costo(services) -> None:
    services.commands.execute(
        CreateProductCommand(code="KA021", name="OTRO NEGRO", unit_price="500", cost="200", stock=10)
    )
    services.commands.execute(
        LoadPurchaseOrderCommand(
            provider_name="MOZUKI MEXICO",
            lines=(
                ImportedOrderLineRequest(
                    code="KA021",
                    description="OTRO NEGRO",
                    quantity=1,
                    unit_price="150",
                ),
            ),
        )
    )
    product = next(p for p in services.queries.ask(GetCatalogQuery()) if p.code == "KA021")
    assert product.cost.as_decimal() == Decimal("150.00")
    assert product.unit_price.as_decimal() == Decimal("500.00")


def test_load_order_sin_venta_producto_nuevo_usa_precio_como_venta(services) -> None:
    services.commands.execute(
        LoadPurchaseOrderCommand(
            provider_name="MOZUKI MEXICO",
            lines=(
                ImportedOrderLineRequest(
                    code="KA999",
                    description="NUEVO SIN VENTA",
                    quantity=1,
                    unit_price="90",
                ),
            ),
        )
    )
    product = next(p for p in services.queries.ask(GetCatalogQuery()) if p.code == "KA999")
    assert product.cost.as_decimal() == Decimal("90.00")
    assert product.unit_price.as_decimal() == Decimal("90.00")


# --------------------------------------------------------------------------- #
# Vista previa del diálogo (regresión: la tabla debe renderizar sin errores)
# --------------------------------------------------------------------------- #


def test_dialogo_preview_carga_costo_y_venta(qapp, services, tmp_path) -> None:
    from app.interface.provider_dialogs import ImportedOrderPreviewDialog

    path = tmp_path / "pedido.xlsx"
    _write_xlsx(
        path,
        ("Código", "Descripción", "Cantidad", "Precio", "venta"),
        [("KA020", "PARRILLA FT150 NEGRO MOZUKI M", 3, 178.58, 320)],
    )
    result = parse_order_excel(path)
    dialog = ImportedOrderPreviewDialog(
        services.commands,
        services.queries,
        result,
        "MOZUKI MEXICO",
        parent=None,
    )
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 0).text() == "KA020"
    assert dialog.table.cellWidget(0, 4).value() == 178.58
    assert dialog.table.cellWidget(0, 5).value() == 320.00


def test_dialogo_preview_sin_venta_deja_columna_en_cero(qapp, services, tmp_path) -> None:
    from app.interface.provider_dialogs import ImportedOrderPreviewDialog

    path = tmp_path / "pedido.xlsx"
    _write_xlsx(
        path,
        ("Código", "Descripción", "Cantidad", "Precio"),
        [("KA031", "CABLE CHICOTE", 5, 21.90)],
    )
    result = parse_order_excel(path)
    dialog = ImportedOrderPreviewDialog(
        services.commands,
        services.queries,
        result,
        "MOZUKI MEXICO",
        parent=None,
    )
    assert dialog.table.cellWidget(0, 4).value() == 21.90
    assert dialog.table.cellWidget(0, 5).value() == 0.00