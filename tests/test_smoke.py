"""Smoke tests: arranque Qt (offscreen), etiquetas y recibos."""

from __future__ import annotations

import html
import os
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import Qt
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


def _emitir_vale(ui_services, *, importe="80", codigo_producto="VALE-1"):
    """Vende con tarjeta emitiendo un vale y devuelve (producto, vale emitido)."""
    from app.application.commands import (
        CompleteSaleCommand,
        CreateCategoryCommand,
        CreateProductCommand,
        SaleItemRequest,
        SalePaymentRequest,
    )

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code=codigo_producto, name="Producto", unit_price="100", stock=20, category_id=cat.id)
    )
    result = c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=1),),
            payments=(SalePaymentRequest(method="TARJETA", amount="100"),),
            issue_vale=True,
            vale_amount=importe,
        )
    )
    return product, result.issued_vale


def test_payment_dialog_aplica_vale_y_cobra_el_remanente(qapp, ui_services):
    """El vale descuenta del total y el efectivo se cuenta por lo que queda."""
    from app.domain.value_objects import Money
    from app.interface.dialogs import PaymentDialog

    _product, issued = _emitir_vale(ui_services, importe="80")

    dialog = PaymentDialog(Money("100"), queries=ui_services.queries)
    dialog.vale_toggle.setChecked(True)
    # El código se dicta por teléfono: puede llegar en minúscula.
    dialog.vale_code.setText(issued.code.lower())
    dialog._on_lookup_vale()

    assert dialog._vale.code == issued.code
    assert dialog._vale_applied.as_decimal() == Decimal("80.00")
    assert dialog._payable().as_decimal() == Decimal("20.00")
    assert dialog.tendered.value() == 20.0

    sel = dialog.build_selection(print_receipt=True)
    assert sel.payments == [("EFECTIVO", Money("20"))]
    assert sel.vale_code == issued.code
    assert sel.issue_vale is False


def test_payment_dialog_quitar_el_vale_devuelve_el_total(qapp, ui_services):
    from app.domain.value_objects import Money
    from app.interface.dialogs import PaymentDialog

    _product, issued = _emitir_vale(ui_services, importe="80")

    dialog = PaymentDialog(Money("100"), queries=ui_services.queries)
    dialog.vale_toggle.setChecked(True)
    dialog.vale_code.setText(issued.code)
    dialog._on_lookup_vale()
    dialog.vale_clear_btn.click()

    assert dialog._vale is None
    assert dialog._payable().as_decimal() == Decimal("100.00")
    assert dialog.build_selection(print_receipt=False).vale_code == ""


def test_payment_dialog_vale_que_cubre_el_total_no_deja_pagos(qapp, ui_services):
    """Si el vale paga la compra entera no queda nada por cobrar."""
    from app.domain.value_objects import Money
    from app.interface.dialogs import PaymentDialog

    _product, issued = _emitir_vale(ui_services, importe="150")

    dialog = PaymentDialog(Money("100"), queries=ui_services.queries)
    dialog.vale_toggle.setChecked(True)
    dialog.vale_code.setText(issued.code)
    dialog._on_lookup_vale()

    assert dialog._payable() == Money.zero()
    sel = dialog.build_selection(print_receipt=True)
    assert sel.payments == []
    assert sel.vale_code == issued.code
    assert dialog._validate() is None


def test_payment_dialog_rechaza_vale_que_no_se_puede_usar(qapp, ui_services, monkeypatch):
    """Un vale inexistente o agotado se suelta con un aviso, sin frenar el cobro."""
    from app.domain.value_objects import Money
    from app.interface.dialogs import PaymentDialog

    avisos: list[str] = []

    class _Silencioso:
        @staticmethod
        def warning(*args, **kwargs):
            avisos.append(args[2] if len(args) > 2 else "")

    monkeypatch.setattr("app.interface.dialogs.QMessageBox", _Silencioso)

    product, issued = _emitir_vale(ui_services, importe="100", codigo_producto="VALE-2")
    # Se agota el vale aplicándolo a otra compra.
    from app.application.commands import CompleteSaleCommand, SaleItemRequest

    ui_services.commands.execute(
        CompleteSaleCommand(items=(SaleItemRequest(code=product.code, quantity=1),), vale_code=issued.code)
    )

    dialog = PaymentDialog(Money("100"), queries=ui_services.queries)
    dialog.vale_toggle.setChecked(True)

    dialog.vale_code.setText("NOEXISTE")
    dialog._on_lookup_vale()
    assert dialog._vale is None
    assert "no existe" in avisos[-1].lower()

    dialog.vale_code.setText(issued.code)
    dialog._on_lookup_vale()
    assert dialog._vale is None
    assert "agotado" in avisos[-1].lower()
    assert dialog._payable() == Money("100")


def test_payment_dialog_solo_entrega_vale_si_se_paga_con_tarjeta(qapp):
    from app.domain.value_objects import Money
    from app.interface.dialogs import PaymentDialog

    dialog = PaymentDialog(Money("100"))
    # Sin bus de consultas no se puede buscar un vale previo.
    assert dialog.vale_toggle.isEnabled() is False
    assert dialog.issue_toggle.isEnabled() is False

    dialog._method_buttons_by_code["TARJETA"].setChecked(True)
    assert dialog.issue_toggle.isEnabled() is True

    dialog.issue_toggle.setChecked(True)
    # El importe propuesto es lo que se paga con tarjeta.
    assert dialog.vale_amount.value() == 100.0
    sel = dialog.build_selection(print_receipt=True)
    assert sel.issue_vale is True
    assert sel.vale_amount.as_decimal() == Decimal("100.00")
    assert sel.vale_code == ""


def test_payment_dialog_no_emite_vale_con_efectivo(qapp, ui_services, monkeypatch):
    """Al cambiar a efectivo la entrega de vale se apaga sola."""
    from app.domain.value_objects import Money
    from app.interface.dialogs import PaymentDialog

    dialog = PaymentDialog(Money("100"), queries=ui_services.queries)
    dialog._method_buttons_by_code["TARJETA"].setChecked(True)
    dialog.issue_toggle.setChecked(True)
    assert dialog.build_selection(print_receipt=True).issue_vale is True

    dialog._method_buttons_by_code["EFECTIVO"].setChecked(True)
    assert dialog.issue_toggle.isChecked() is False
    assert dialog.build_selection(print_receipt=False).issue_vale is False
    assert dialog._validate() is None


def test_sell_view_dicta_el_codigo_del_vale_entregado(qapp, ui_services, store_settings, monkeypatch):
    """El cajero necesita leer el código en voz alta antes de que se vaya el cliente."""
    from app.application.commands import CompleteSaleCommand, SaleItemRequest
    from app.interface.sell_view import SellView

    product, issued = _emitir_vale(ui_services, importe="150", codigo_producto="VALE-3")
    result = ui_services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=1),),
            payments=(),
            vale_code=issued.code,
        )
    )

    avisos: list[tuple] = []

    class _Silencioso:
        @staticmethod
        def information(*args, **kwargs):
            avisos.append(args[1:3])

    monkeypatch.setattr("app.interface.sell_view.QMessageBox", _Silencioso)

    view = SellView(ui_services.commands, ui_services.queries, store_settings)
    view._report_vale(result)

    assert avisos, "el cajero tiene que ver el vale redimido"
    # El vale era de 150 y la compra de 100: se aplicó todo y sobró saldo.
    assert result.vale_applied.as_decimal() == Decimal("100.00")
    assert result.vale_remaining.as_decimal() == Decimal("50.00")
    assert "qued" in avisos[0][1].lower()


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
    assert [(code, qty) for code, qty, _price in values["items"]] == [(product.code, 1)]
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


def test_sell_view_precio_inline_actualiza_total(qapp, ui_services, store_settings):
    """Editar el precio de la columna Precio recalcula subtotal y total del ticket."""
    from PySide6.QtWidgets import QDoubleSpinBox

    from app.application.commands import CreateCategoryCommand, CreateProductCommand
    from app.interface.sell_view import SellView, cart_price

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

    table = view._current_table()
    spin = table.cellWidget(0, 2)
    assert isinstance(spin, QDoubleSpinBox)
    assert spin.value() == 20.0

    # El cajero baja el precio de la partida a 15.
    spin.setValue(15.0)
    spin.editingFinished.emit()

    assert cart_price(view._current_cart()[0]).as_decimal() == Decimal("15.00")
    assert table.item(0, 4).text() == "$ 30,00"
    assert view.total_label.text() == "$ 30,00"
    # El precio del producto en catálogo no se toca.
    assert product.unit_price.as_decimal() == Decimal("20.00")


def test_sell_view_override_sobrevive_a_agregar_mas_unidades(qapp, ui_services, store_settings):
    from PySide6.QtWidgets import QDoubleSpinBox

    from app.application.commands import CreateCategoryCommand, CreateProductCommand
    from app.interface.sell_view import SellView, cart_price

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )

    view = SellView(c, ui_services.queries, store_settings)
    view.refresh()
    view._add_product(product)
    spin = view._current_table().cellWidget(0, 2)
    assert isinstance(spin, QDoubleSpinBox)
    spin.setValue(15.0)
    spin.editingFinished.emit()

    # Agregar otra unidad del mismo producto no pierde el precio ajustado.
    view._add_product(product)
    assert len(view._current_cart()) == 1
    assert view._current_cart()[0]["qty"] == 2
    assert cart_price(view._current_cart()[0]).as_decimal() == Decimal("15.00")
    assert view.total_label.text() == "$ 30,00"


def test_apartado_dialog_precio_inline(qapp, ui_services):
    from PySide6.QtWidgets import QDoubleSpinBox

    from app.application.commands import CreateCategoryCommand, CreateProductCommand
    from app.interface.dialogs import ApartadoDialog

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )

    dialog = ApartadoDialog(ui_services.queries)
    dialog.client_name.setText("Ana")
    dialog._add_by_text("Agua")
    dialog._add_by_text("Agua")
    dialog._add_by_text("Agua")

    spin = dialog.items_table.cellWidget(0, 1)
    assert isinstance(spin, QDoubleSpinBox)
    assert spin.value() == 20.0

    spin.setValue(18.0)
    spin.editingFinished.emit()

    assert dialog.items_table.item(0, 3).text() == "$ 54,00"
    assert "Total: $ 54,00" in dialog.total_label.text()

    values = dialog.values()
    code, qty, price = values["items"][0]
    assert (code, qty) == (product.code, 3)
    assert price.as_decimal() == Decimal("18.00")


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


def test_ticket_imprime_leyenda_de_devolucion(ui_services, store_settings):
    """Cada ticket de venta deja escrita la política de devolución."""
    from app.application.commands import (
        CompleteSaleCommand,
        CreateCategoryCommand,
        CreateProductCommand,
        SaleItemRequest,
        SalePaymentRequest,
    )
    from app.infrastructure.printers.receipt import (
        render_receipt_html,
        render_sale_plain_text,
    )
    from app.settings import DEFAULT_SALE_LEGEND

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    product = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=5, category_id=cat.id)
    )
    result = c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=1),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="20"),),
            tendered="20",
        )
    )
    store = store_settings.store
    assert store.sale_legend == DEFAULT_SALE_LEGEND

    receipt_html = render_receipt_html(result.sale, store)
    assert "se permite devoluci" in receipt_html
    assert "se entrega un vale" in receipt_html

    text = render_sale_plain_text(result.sale, store)
    # El texto plano envuelve la leyenda al ancho de la impresora.
    assert "directa. En compras con tarjeta" in text
    assert len(text.splitlines()[-1]) <= 32


def test_ticket_omite_leyenda_si_se_vacia(store_settings):
    """Configurar la leyenda vacía la saca del ticket sin romper el pie."""
    from app.infrastructure.printers.receipt import render_legend_html
    from app.settings import StoreInfo

    assert render_legend_html(StoreInfo(sale_legend="")) == ""
    assert "ATENCION" in render_legend_html(StoreInfo(sale_legend="ATENCION"))


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


def _make_product(commands, *, code="7501055302082", name="Agua 500ml", stock=10):
    from app.application.commands import CreateCategoryCommand, CreateProductCommand

    cat = commands.execute(CreateCategoryCommand(name="BEBIDAS"))
    return commands.execute(
        CreateProductCommand(code=code, name=name, unit_price="20", stock=stock, category_id=cat.id)
    )


def test_inventory_view_fija_cantidad_inline(qapp, ui_services):
    """Doble clic en la columna Stock fija la cantidad y lo asienta en el cardex.

    El inventario local es la suma de deltas, así que editar la cantidad se
    traduce en un ajuste con movimiento ``CONTEO`` que después se replica al hub.
    """
    from app.application.queries import GetCatalogQuery, GetStockMovementsQuery
    from app.interface.inventory_view import REASON_COUNT, STOCK_COLUMN, InventoryView

    commands, queries = ui_services.commands, ui_services.queries
    product = _make_product(commands)

    view = InventoryView(commands, queries)
    view.refresh()
    row = 0
    assert view.table.item(row, STOCK_COLUMN).text() == "10"
    assert view.table.item(row, STOCK_COLUMN).flags() & Qt.ItemIsEditable

    # El cajero cuenta 7 unidades y confirma la edición inline.
    index = view.table.model().index(row, STOCK_COLUMN)
    editor = view.stock_delegate.createEditor(view, None, index)
    view.stock_delegate.setEditorData(editor, index)
    assert editor.value() == 10
    editor.setValue(7)
    view.stock_delegate.setModelData(editor, view.table.model(), index)

    refreshed = {p.id: p for p in queries.ask(GetCatalogQuery(include_inactive=False))}[product.id]
    assert refreshed.stock == 7

    movements = queries.ask(GetStockMovementsQuery(product_id=product.id))
    assert [(m.delta, m.reason) for m in movements] == [(-3, REASON_COUNT)]
    assert "10 -> 7" in movements[0].note
    # La tabla se refresca sola con la cantidad nueva.
    assert view.table.item(row, STOCK_COLUMN).text() == "7"


def test_inventory_view_conteo_ignorado_si_no_cambia(qapp, ui_services):
    """Confirmar el mismo número no genera un movimiento inútil."""
    from app.application.queries import GetStockMovementsQuery
    from app.interface.inventory_view import STOCK_COLUMN, InventoryView

    commands, queries = ui_services.commands, ui_services.queries
    product = _make_product(commands)

    view = InventoryView(commands, queries)
    view.refresh()
    view._on_stock_edited(0, 10)  # mismo valor

    assert queries.ask(GetStockMovementsQuery(product_id=product.id)) == []


def test_inventory_view_solo_stock_es_editable(qapp, ui_services):
    """El resto de columnas es de sólo lectura: nada de editar precio o estado."""
    from app.interface.inventory_view import STOCK_COLUMN, InventoryView

    commands, queries = ui_services.commands, ui_services.queries
    _make_product(commands)
    view = InventoryView(commands, queries)
    view.refresh()

    editable = [
        col
        for col in range(view.table.columnCount())
        if view.table.item(0, col).flags() & Qt.ItemIsEditable
    ]
    assert editable == [STOCK_COLUMN]