"""Smoke tests: arranque Qt (offscreen), etiquetas y recibos."""

from __future__ import annotations

import html
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def ui_services():
    from app.bootstrap import build_services
    from app.settings import Settings

    return build_services(settings=Settings(database_path=":memory:"))


@pytest.fixture
def store_settings():
    from app.settings import Settings

    return Settings(database_path=":memory:")


@pytest.fixture
def labels(store_settings):
    from app.infrastructure.printing.label_printer import LabelPrintingService

    return LabelPrintingService(store_settings)


def test_main_window_navega_entre_paginas(qapp, ui_services, store_settings, labels):
    from app.application.commands import CompleteSaleCommand, CreateCategoryCommand, CreateProductCommand, SaleItemRequest, SalePaymentRequest
    from app.interface.main_window import MainWindow

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )
    c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40"),),
            tendered="50",
        )
    )

    window = MainWindow(ui_services, store_settings, labels)
    for key in ("inicio", "vender", "productos", "inventario", "historial", "apartados", "caja"):
        window.switch_page(key)
    assert window._stack.currentWidget() is not None


def test_payment_dialog_cinco_metodos(qapp):
    from decimal import Decimal

    from app.domain.value_objects import Money
    from app.interface.dialogs import PAYMENT_METHODS, PaymentDialog

    total = Money("100")
    dialog = PaymentDialog(total)
    codes = [b.property("method") for b in dialog.method_buttons]
    assert codes == [m for m, _, _ in PAYMENT_METHODS]

    dialog._method_buttons_by_code["EFECTIVO"].setChecked(True)
    dialog.tendered.setValue(150.0)
    sel = dialog.build_selection(print_receipt=True)
    assert sel.payments == [("EFECTIVO", total)]
    assert sel.tendered.as_decimal() == Decimal("150.00")
    assert sel.print_receipt is True

    dialog._method_buttons_by_code["CREDITO"].setChecked(True)
    sel = dialog.build_selection(print_receipt=False)
    assert sel.payments == [("CREDITO", total)]
    assert sel.tendered is None
    assert sel.print_receipt is False

    dialog._method_buttons_by_code["TARJETA"].setChecked(True)
    sel = dialog.build_selection(print_receipt=True)
    assert sel.payments == [("TARJETA", total)]
    assert sel.tendered is None

    dialog._method_buttons_by_code["TRANSFERENCIA"].setChecked(True)
    sel = dialog.build_selection(print_receipt=True)
    assert sel.payments == [("TRANSFERENCIA", total)]

    dialog._method_buttons_by_code["MIXTO"].setChecked(True)
    dialog.mix_row["EFECTIVO"][1].setValue(30.0)
    dialog.mix_row["TARJETA"][1].setValue(70.0)
    dialog.mix_row["TRANSFERENCIA"][1].setValue(0.0)
    sel = dialog.build_selection(print_receipt=True)
    assert sel.payments == [("EFECTIVO", Money("30")), ("TARJETA", Money("70"))]
    assert sel.tendered.as_decimal() == Decimal("30.00")


def test_sell_view_tickets_pendientes(qapp, ui_services, store_settings):
    from app.application.commands import CreateCategoryCommand, CreateProductCommand
    from app.interface.sell_view import SellView

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )

    view = SellView(c, ui_services.queries, store_settings)
    view.refresh()
    assert len(view._ticket_carts) == 1

    view._add_product(product)
    assert len(view._current_cart()) == 1
    assert view.pay_btn.isEnabled()

    view._pending_ticket()
    assert len(view._ticket_carts) == 2
    assert len(view._current_cart()) == 0
    assert view.tabs.currentIndex() == 1

    view._add_product(product)
    assert len(view._current_cart()) == 1
    assert view._ticket_carts[0][0]["product"].id == product.id


def test_cancel_ticket_dialog_lista_tickets_del_dia(qapp, ui_services, store_settings):
    from app.application.commands import (
        CompleteSaleCommand,
        CreateCategoryCommand,
        CreateProductCommand,
        SaleItemRequest,
        SalePaymentRequest,
        VoidSaleCommand,
    )
    from app.application.queries import GetSalesHistoryQuery
    from app.interface.dialogs import CancelTicketDialog

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )
    c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40"),),
        )
    )

    dialog = CancelTicketDialog(c, ui_services.queries, store_settings)
    assert dialog.table.rowCount() == 1
    sale = ui_services.queries.ask(GetSalesHistoryQuery())[0]
    c.execute(VoidSaleCommand(sale_id=sale.id))
    dialog.refresh()
    assert dialog.table.rowCount() == 1  # la anulada sigue listada para auditoría


def test_apartados_page_y_dialogos(qapp, ui_services, store_settings):
    from decimal import Decimal

    from app.application.commands import CreateCategoryCommand, CreateProductCommand
    from app.interface.apartado_view import ApartadoView
    from app.interface.dialogs import ApartadoDialog

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )

    view = ApartadoView(c, ui_services.queries)
    view.refresh()
    assert view.table.rowCount() == 0

    create = ApartadoDialog(ui_services.queries)
    create.client_name.setText("Juan")
    create._add_by_text("Agua")
    create.abono.setValue(20.0)
    values = create.values()
    assert values["client_name"] == "Juan"
    assert values["items"] == [(product.code, 1)]
    assert values["initial_abono"].as_decimal() == Decimal("20.00")


def test_sell_view_ajuste_monto_doble_click(qapp, ui_services, store_settings):
    from app.application.commands import CreateCategoryCommand, CreateProductCommand
    from app.domain.value_objects import Money
    from app.interface.sell_view import SellView

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )

    view = SellView(c, ui_services.queries, store_settings)
    view.refresh()
    view._add_product(product)
    view._add_product(product)
    assert view.total_label.text() == "$ 40,00"

    view._ticket_charges[0] = Money("35")
    view._refresh_totals()
    assert view.total_label.text() == "$ 35,00"
    assert view.discount_label.isVisibleTo(view)
    assert "Descuento" in view.discount_label.text()

    view._clear_cart()
    assert view.total_label.text() == "$ 0,00"
    assert view.discount_label.isHidden()


def test_login_dialog_credenciales(qapp, ui_services, store_settings):
    from app.interface.login_view import LoginDialog

    dialog = LoginDialog(store_settings)
    assert dialog.validate_credentials("angel", "12345") is True
    assert dialog.validate_credentials("angel", "mal") is False
    assert dialog.validate_credentials("otro", "12345") is False


def test_open_cash_day_dialog_pide_efectivo_inicial(qapp, ui_services, store_settings):
    from app.interface.dialogs import OpenCashDayDialog

    dialog = OpenCashDayDialog(ui_services.commands, opened_by="angel")
    assert dialog.initial_cash.value() == 0.0
    assert dialog.initial_cash.isEnabled()


def test_corte_dialog_muestra_y_cierra_con_diferencia(qapp, ui_services, store_settings):
    from decimal import Decimal

    from app.application.commands import (
        CompleteSaleCommand,
        CreateCategoryCommand,
        CreateProductCommand,
        OpenCashDayCommand,
        SaleItemRequest,
        SalePaymentRequest,
    )
    from app.application.queries import GetCorteQuery
    from app.interface.dialogs import CorteDialog

    c = ui_services.commands
    c.execute(OpenCashDayCommand(opening_cash="200", opened_by="angel"))
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )
    c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40"),),
        )
    )
    corte = ui_services.queries.ask(GetCorteQuery())
    assert corte.expected_cash.as_decimal() == Decimal("240.00")
    dialog = CorteDialog(c, ui_services.queries, opened_by="angel")
    assert dialog.counted.value() == 240.0
    assert dialog.close_day_btn.isEnabled()


def test_history_search_con_comodin(qapp, ui_services, store_settings):
    from app.application.commands import (
        CompleteSaleCommand,
        CreateCategoryCommand,
        CreateProductCommand,
        SaleItemRequest,
        SalePaymentRequest,
    )
    from app.interface.history_view import HistoryView

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    c.execute(CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id))
    c.execute(CreateProductCommand(code="7501055302001", name="Refresco Cola 600ml", unit_price="25", stock=5, category_id=cat.id))
    c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code="7501055302082", quantity=1),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="20"),),
        )
    )
    view = HistoryView(c, ui_services.queries, store_settings)
    view.refresh()
    view.search.setText("%Agu%")
    assert len(view._sales) == 1
    view.search.setText("%QUIEN%")
    assert len(view._sales) == 0


def test_search_matches_comodin_no_anclado(qapp):
    from app.interface.widgets import search_matches

    name = "CAMARA MOTO PREMIUM 110/90-17 TR4 25172500 M"
    assert search_matches("camara%110", name)
    assert search_matches("%110%", name)
    assert search_matches("camara%", name)
    assert search_matches("110", name)
    assert not search_matches("camara%999", name)


def test_sell_view_abre_cajon_en_ventas_efectivo(qapp, ui_services, store_settings, monkeypatch):
    from app.domain.value_objects import Money
    from app.interface.sell_view import SellView

    view = SellView(ui_services.commands, ui_services.queries, store_settings)
    calls: list[bool] = []

    def fake_kick() -> bool:
        calls.append(True)
        return True

    import app.infrastructure.printers.ticket_esc_pos as ticket_mod

    monkeypatch.setattr(ticket_mod, "kick_cash_drawer", fake_kick)
    view._kick_drawer_if_cash([("TARJETA", Money(amount=20))])
    assert calls == []
    view._kick_drawer_if_cash([("EFECTIVO", Money(amount=20))])
    assert calls == [True]

    def failing_kick() -> bool:
        return False

    monkeypatch.setattr(ticket_mod, "kick_cash_drawer", failing_kick)
    view._kick_drawer_if_cash([("EFECTIVO", Money(amount=20))])
    assert calls == [True]


def test_cash_view_estados_segun_jornada(qapp, ui_services, store_settings):
    from app.application.commands import (
        OpenCashDayCommand,
        RegisterCashMovementCommand,
    )
    from app.interface.cash_view import CashView

    view = CashView(ui_services.commands, ui_services.queries, opened_by="angel")
    view.refresh()
    assert view.open_btn.isEnabled()
    assert not view.income_btn.isEnabled()
    assert not view.corte_btn.isEnabled()

    ui_services.commands.execute(OpenCashDayCommand(opening_cash="200", opened_by="angel"))
    view.refresh()
    assert not view.open_btn.isEnabled()
    assert view.income_btn.isEnabled()
    assert view.corte_btn.isEnabled()
    assert view.fondo_value.text() == "$ 200,00"

    ui_services.commands.execute(RegisterCashMovementCommand(movement_type="ENTRADA", amount="50", reason="INGRESO"))
    view.refresh()
    assert view.table.rowCount() == 1


def test_refund_line_dialog_muestra_partidas(qapp, ui_services, store_settings):
    from app.application.commands import (
        CompleteSaleCommand,
        CreateCategoryCommand,
        CreateProductCommand,
        RefundSaleItemCommand,
        SaleItemRequest,
        SalePaymentRequest,
    )
    from app.application.queries import GetSaleQuery
    from app.interface.dialogs import RefundLineDialog

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )
    result = c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40"),),
        )
    )
    dialog = RefundLineDialog(c, ui_services.queries, result.sale_id)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 3).text() == "2"

    c.execute(RefundSaleItemCommand(sale_id=result.sale_id, item_index=0, quantity=1, cash_payout=False))
    dialog.sale = ui_services.queries.ask(GetSaleQuery(sale_id=result.sale_id))
    dialog._render()
    assert dialog.table.item(0, 2).text() == "1"
    assert dialog.table.item(0, 3).text() == "1"


def test_refund_line_dialog_botones_devuelven_segun_fila(qapp, ui_services, store_settings):
    from app.application.commands import (
        CompleteSaleCommand,
        CreateCategoryCommand,
        CreateProductCommand,
        SaleItemRequest,
        SalePaymentRequest,
    )
    from app.interface.dialogs import RefundLineDialog

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    p1 = c.execute(CreateProductCommand(code="REF-A1", name="AGUA REFUND", unit_price="20", stock=10, category_id=cat.id))
    p2 = c.execute(CreateProductCommand(code="REF-A2", name="GASEOSA REFUND", unit_price="30", stock=10, category_id=cat.id))
    result = c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=p1.code, quantity=2), SaleItemRequest(code=p2.code, quantity=1)),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="70")),
        )
    )
    dialog = RefundLineDialog(c, ui_services.queries, result.sale_id)
    captured: list[int] = []

    class FakeForm:
        Accepted = 1

        def __init__(self, _commands, sale, index, parent=None):
            captured.append(index)

        def exec(self):
            return self.Accepted

    import app.interface.dialogs as dlg_mod

    old = dlg_mod.RefundLineFormDialog
    dlg_mod.RefundLineFormDialog = FakeForm
    try:
        dialog.table.cellWidget(0, 5).click()
        dialog.table.cellWidget(1, 5).click()
    finally:
        dlg_mod.RefundLineFormDialog = old
    assert captured == [0, 1]


def test_render_etiqueta_ql810w(qapp):
    from app.domain.value_objects import Money
    from app.infrastructure.labels.label_renderer import LabelSpec, render_barcode_label

    spec = LabelSpec(width_mm=90, height_mm=29, dpi=300)
    image = render_barcode_label(code="7501055302082", name="Agua mineral 500ml", price=Money("120"), spec=spec)
    w, h = image.width(), image.height()
    assert w == pytest.approx(90 / 25.4 * 300, abs=1)
    assert h == pytest.approx(29 / 25.4 * 300, abs=1)


def test_render_etiqueta_code128(qapp):
    from app.domain.value_objects import Money
    from app.infrastructure.labels.label_renderer import render_barcode_label

    image = render_barcode_label(code="PAPAS-01", name="Papas fritas", price=Money("30"))
    assert image.width() > 0 and image.height() > 0


def test_etiqueta_rotada_para_ql_810w(qapp):
    from app.infrastructure.labels.label_renderer import render_barcode_label
    from app.infrastructure.printing.label_printer import _rotate_for_ql

    image = render_barcode_label(code="100202", name="SLIDER EJE LLANTA", price=None)
    assert (image.width(), image.height()) == (1063, 354)

    portrait = _rotate_for_ql(image)
    assert (portrait.width(), portrait.height()) == (354, 1063)  # 30x90 mm @ 300 dpi


def test_recibo_texto_plano(ui_services, store_settings):
    from app.application.commands import CreateCategoryCommand, CreateProductCommand, CompleteSaleCommand, SaleItemRequest, SalePaymentRequest
    from app.infrastructure.printers.receipt import render_sale_plain_text

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=5, category_id=cat.id)
    )
    result = c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40"),),
            tendered="100",
        )
    )
    text = render_sale_plain_text(result.sale, store_settings.store)
    assert result.receipt_number in text
    assert "TOTAL" in text
    assert "Vuelto" in text


def test_barcode_html_escanible(ui_services, store_settings):
    from app.application.commands import CreateCategoryCommand, CreateProductCommand, CompleteSaleCommand, SaleItemRequest, SalePaymentRequest
    from app.infrastructure.printers.receipt import render_barcode_html, render_receipt_html

    assert "background-color:#111111" in render_barcode_html("R-000001")

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=5, category_id=cat.id)
    )
    result = c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=1),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="20"),),
            tendered="50",
        )
    )
    receipt_html = render_receipt_html(result.sale, store_settings.store)
    assert f"N. {result.receipt_number}" in receipt_html
    assert "background-color:#111111" in receipt_html
    assert f">{html.escape(result.receipt_number)}</div>" in receipt_html