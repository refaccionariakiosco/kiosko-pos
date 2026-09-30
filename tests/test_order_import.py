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


# --------------------------------------------------------------------------- #
# Formato fijo: Código · Cantidad · Descripción · Costo · Precio (fila 1 = títulos)
# --------------------------------------------------------------------------- #


def _write_csv(path, header, rows, *, encoding="cp1252") -> None:
    import csv

    with open(path, "w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def test_columnas_fijas_ignora_lo_que_diga_la_fila_uno(tmp_path) -> None:
    path = tmp_path / "pedido.xlsx"
    _write_xlsx(
        path,
        ("NO", "SON", "ENCABEZADOS", "CUALQUIERA", "COSA"),
        [("KB034", 2, "CANDADO GRANDE", 100, 250), ("KB035", 1, "CANDADO CHICO", 50, 120)],
    )
    result = parse_order_excel(path)
    assert [(l.code, l.quantity, l.unit_price, l.sale_price) for l in result.lines] == [
        ("KB034", 2, Decimal("100.00"), Decimal("250.00")),
        ("KB035", 1, Decimal("50.00"), Decimal("120.00")),
    ]
    assert result.lines[0].description == "CANDADO GRANDE"


def test_csv_con_codificacion_cp1252_y_moneda(tmp_path) -> None:
    path = tmp_path / "pedido.csv"
    _write_csv(
        path,
        ("Código", "Cantidad", "Descripción", "COSTO", "VENTA"),
        [
            ("RINA015", "3", "RIN DELANTERO DE ALUMINIO ÑOÑO", "$578,84", "950"),
            ("RITZ164", "2", "RETENES DE MOTOR (JUEGO)", "$12,76", 25),
        ],
    )
    result = parse_order_excel(path)
    assert len(result.lines) == 2
    line = result.lines[0]
    assert line.code == "RINA015"
    assert line.quantity == 3
    assert line.description == "RIN DELANTERO DE ALUMINIO ÑOÑO"
    assert line.unit_price == Decimal("578.84")
    assert line.sale_price == Decimal("950.00")
    assert result.lines[1].unit_price == Decimal("12.76")
    assert result.lines[1].sale_price == Decimal("25.00")


def test_archivo_sin_encabezados_y_separador_punto_y_coma(tmp_path) -> None:
    path = tmp_path / "pedido.csv"
    path.write_text('GYG01;4;"REFACCION VARIADA";"1.234,56";"2.000,00"\n', encoding="utf-8")
    result = parse_order_excel(path)
    assert len(result.lines) == 1
    assert result.lines[0].quantity == 4
    assert result.lines[0].unit_price == Decimal("1234.56")
    assert result.lines[0].sale_price == Decimal("2000.00")


def test_higieniza_espacios_comillas_y_codigo(tmp_path) -> None:
    path = tmp_path / "pedido.csv"
    path.write_text(
        '"Código","Cantidad","Descripción","Costo","Precio"\n'
        '"  ka020  "," 3 " ,"  PARRILLA   FT150  NEGRO ","$178.58","$320.00"\n',
        encoding="utf-8",
    )
    result = parse_order_excel(path)
    assert len(result.lines) == 1
    line = result.lines[0]
    assert line.code == "KA020"
    assert line.quantity == 3
    assert line.description == "PARRILLA FT150 NEGRO"
    assert line.unit_price == Decimal("178.58")
    assert line.sale_price == Decimal("320.00")


def test_archivo_excel97_rechaza_con_mensaje_claro(tmp_path) -> None:
    path = tmp_path / "pedido.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
    with pytest.raises(ValueError, match="Excel 97"):
        parse_order_excel(path)


def test_archivo_xlsx_es_realmente_un_csv(tmp_path) -> None:
    path = tmp_path / "pedido.xlsx"
    path.write_text(
        "Código,Cantidad,Descripción,COSTO,VENTA\nKA020,2,PARRILLA,100,250\n",
        encoding="utf-8",
    )
    result = parse_order_excel(path)
    assert len(result.lines) == 1
    assert result.lines[0].code == "KA020"
    assert result.lines[0].quantity == 2


# --------------------------------------------------------------------------- #
# Recepción: la cantidad del archivo se suma a la existencia
# --------------------------------------------------------------------------- #


def test_pedido_cargado_desde_csv_se_recibe_y_suma_existencia(services, tmp_path) -> None:
    from app.application.commands import ReceiveLineRequest, ReceivePurchaseOrderCommand

    path = tmp_path / "pedido.csv"
    _write_csv(
        path,
        ("Código", "Cantidad", "Descripción", "COSTO", "VENTA"),
        [
            ("GYG-01", "3", "REFACCION DE PRUEBA", "$10,50", "25"),
            ("GYG-02", "2", "OTRO REFACCION", "$5,00", "12"),
        ],
    )
    result = parse_order_excel(path)
    order = services.commands.execute(
        LoadPurchaseOrderCommand(
            provider_name="GYG",
            lines=tuple(
                ImportedOrderLineRequest(
                    code=line.code,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=str(line.unit_price),
                    sale_price=str(line.sale_price) if line.sale_price is not None else None,
                )
                for line in result.lines
            ),
        )
    )
    assert order.status == "PENDIENTE"

    catalog = {p.code: p for p in services.queries.ask(GetCatalogQuery())}
    assert catalog["GYG-01"].stock == 0
    assert catalog["GYG-02"].stock == 0

    received = services.commands.execute(
        ReceivePurchaseOrderCommand(
            purchase_order_id=order.id,
            note="Llegó el pedido",
            lines=tuple(
                ReceiveLineRequest(
                    product_id=catalog[line.code].id,
                    quantity=line.quantity,
                    unit_price=str(line.unit_price),
                )
                for line in result.lines
            ),
        )
    )
    assert received == 5

    catalog = {p.code: p for p in services.queries.ask(GetCatalogQuery())}
    assert catalog["GYG-01"].stock == 3
    assert catalog["GYG-02"].stock == 2