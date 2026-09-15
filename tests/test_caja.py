"""Tests de caja, devoluciones y búsquedas con comodín."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.application.commands import (
    CloseCashDayCommand,
    CompleteSaleCommand,
    CreateApartadoCommand,
    CreateProductCommand,
    OpenCashDayCommand,
    RefundSaleItemCommand,
    RegisterCashMovementCommand,
    SaleItemRequest,
    SalePaymentRequest,
)
from app.application.queries import (
    GetApartadosQuery,
    GetCashMovementsQuery,
    GetCorteQuery,
    GetOpenCashDayQuery,
    GetSalesHistoryQuery,
)
from app.domain.exceptions import (
    CashDayAlreadyOpenError,
    NoOpenCashDayError,
    SaleItemRefundError,
    SaleNotFoundError,
    ValidationError,
)


@pytest.fixture
def product(services):
    return services.commands.execute(
        CreateProductCommand(
            code="7501055302082",
            name="Agua 500ml",
            unit_price="30",
            stock=6,
            cost="10",
        )
    )


def sell_cash(services, code, qty, total, tendered):
    return services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=code, quantity=qty),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount=total),),
            tendered=tendered,
        )
    )


# --------------------------------------------------------------------------- #
# Apertura y corte de caja
# --------------------------------------------------------------------------- #


def test_abrir_jornada_y_consultar(services):
    opened = services.commands.execute(OpenCashDayCommand(opening_cash="100", opened_by="angel", note="Inicio"))
    assert opened.status == "ABIERTA"
    assert opened.opening_cash.as_decimal() == Decimal("100.00")
    current = services.queries.ask(GetOpenCashDayQuery())
    assert current is not None
    assert current.id == opened.id
    corte = services.queries.ask(GetCorteQuery())
    assert corte is not None
    assert corte.day.id == opened.id
    assert corte.expected_cash.as_decimal() == Decimal("100.00")


def test_no_abrir_doble_jornada(services):
    services.commands.execute(OpenCashDayCommand(opening_cash="50"))
    with pytest.raises(CashDayAlreadyOpenError):
        services.commands.execute(OpenCashDayCommand(opening_cash="50"))


def test_cerrar_sin_jornada(services):
    with pytest.raises(NoOpenCashDayError):
        services.commands.execute(CloseCashDayCommand(counted_cash="100"))


def test_corte_efectivo_esperado_y_cierre_exacto(services, product):
    services.commands.execute(OpenCashDayCommand(opening_cash="100"))
    sell_cash(services, product.code, 2, "60", "100")
    services.commands.execute(RegisterCashMovementCommand(movement_type="ENTRADA", amount="50", reason="INGRESO"))
    services.commands.execute(RegisterCashMovementCommand(movement_type="SALIDA", amount="20", reason="RETIRO"))

    corte = services.queries.ask(GetCorteQuery())
    assert corte.sales_count == 1
    assert corte.sales_total.as_decimal() == Decimal("60.00")
    assert corte.by_method["EFECTIVO"].as_decimal() == Decimal("60.00")
    assert corte.cash_in_total.as_decimal() == Decimal("50.00")
    assert corte.cash_out_total.as_decimal() == Decimal("20.00")
    assert corte.expected_cash.as_decimal() == Decimal("190.00")

    closed = services.commands.execute(CloseCashDayCommand(counted_cash="190"))
    assert closed.status == "CERRADA"
    assert closed.difference.as_decimal() == Decimal("0.00")
    assert services.queries.ask(GetOpenCashDayQuery()) is None


def test_corte_con_sobrante(services, product):
    services.commands.execute(OpenCashDayCommand(opening_cash="100"))
    sell_cash(services, product.code, 1, "30", "50")
    closed = services.commands.execute(CloseCashDayCommand(counted_cash="140"))
    assert closed.difference.as_decimal() == Decimal("10.00")
    assert closed.difference_kind == "SOBRANTE"


def test_corte_con_faltante(services, product):
    services.commands.execute(OpenCashDayCommand(opening_cash="100"))
    sell_cash(services, product.code, 1, "30", "50")
    closed = services.commands.execute(CloseCashDayCommand(counted_cash="120"))
    assert closed.difference.as_decimal() == Decimal("10.00")
    assert closed.difference_kind == "FALTANTE"


def test_cierre_repetido(services, product):
    services.commands.execute(OpenCashDayCommand(opening_cash="0"))
    services.commands.execute(CloseCashDayCommand(counted_cash="0"))
    with pytest.raises(NoOpenCashDayError):
        services.commands.execute(CloseCashDayCommand(counted_cash="0"))


def test_movimiento_de_caja_invalido(services):
    with pytest.raises(ValidationError):
        services.commands.execute(RegisterCashMovementCommand(movement_type="X", amount="10"))
    with pytest.raises(ValidationError):
        services.commands.execute(RegisterCashMovementCommand(movement_type="ENTRADA", amount="-5"))
    with pytest.raises(ValidationError):
        services.commands.execute(RegisterCashMovementCommand(movement_type="ENTRADA", amount="-5"))


# --------------------------------------------------------------------------- #
# Devoluciones
# --------------------------------------------------------------------------- #


def test_devolucion_parcial_restaura_stock_y_registra_salida(services, product):
    services.commands.execute(OpenCashDayCommand(opening_cash="100"))
    sale = sell_cash(services, product.code, 2, "60", "100")
    assert services.queries.ask(GetSalesHistoryQuery())[0].items[0].remaining_qty == 2

    updated = services.commands.execute(
        RefundSaleItemCommand(sale_id=sale.sale_id, item_index=0, quantity=1, reason="Producto dañado")
    )
    item = updated.items[0]
    assert item.refunded_qty == 1
    assert item.remaining_qty == 1
    assert updated.status == "COMPLETADA"

    from app.application.queries import GetCatalogQuery

    assert services.queries.ask(GetCatalogQuery())[0].stock == 5
    movements = services.queries.ask(GetCashMovementsQuery())
    assert len(movements) == 1
    assert movements[0].movement_type == "SALIDA"
    assert movements[0].reason == "DEVOLUCION"
    assert movements[0].amount.as_decimal() == Decimal("30.00")

    corte = services.queries.ask(GetCorteQuery())
    assert corte.refunds_total.as_decimal() == Decimal("30.00")
    assert corte.expected_cash.as_decimal() == Decimal("130.00")


def test_devolucion_total_marca_devuelta(services, product):
    services.commands.execute(OpenCashDayCommand(opening_cash="0"))
    sale = sell_cash(services, product.code, 2, "60", "100")
    result = services.commands.execute(
        RefundSaleItemCommand(sale_id=sale.sale_id, item_index=0, quantity=2)
    )
    assert result.status == "DEVUELTA"
    from app.application.queries import GetCatalogQuery

    assert services.queries.ask(GetCatalogQuery())[0].stock == 6
    from app.application.commands import VoidSaleCommand
    from app.domain.exceptions import ValidationError as _VE

    with pytest.raises((_VE, SaleItemRefundError)):
        services.commands.execute(VoidSaleCommand(sale_id=sale.sale_id))


def test_devolucion_invalida(services, product):
    sale = sell_cash(services, product.code, 1, "30", "50")
    with pytest.raises(SaleItemRefundError):
        services.commands.execute(RefundSaleItemCommand(sale_id=sale.sale_id, item_index=0, quantity=2))
    with pytest.raises(SaleItemRefundError):
        services.commands.execute(RefundSaleItemCommand(sale_id=sale.sale_id, item_index=9, quantity=1))
    with pytest.raises(SaleNotFoundError):
        services.commands.execute(RefundSaleItemCommand(sale_id=99999, item_index=0, quantity=1))


def test_devolucion_sin_reposicion_de_efectivo(services, product):
    services.commands.execute(OpenCashDayCommand(opening_cash="0"))
    sale = sell_cash(services, product.code, 1, "30", "50")
    services.commands.execute(
        RefundSaleItemCommand(sale_id=sale.sale_id, item_index=0, quantity=1, cash_payout=False)
    )
    assert services.queries.ask(GetCashMovementsQuery()) == []
    corte = services.queries.ask(GetCorteQuery())
    assert corte.expected_cash.as_decimal() == Decimal("0.00")


def test_devolucion_en_historial_busca_por_recibo(services, product):
    sale = sell_cash(services, product.code, 1, "30", "50")
    matches = services.queries.ask(GetSalesHistoryQuery(search=sale.receipt_number))
    assert len(matches) == 1
    assert matches[0].id == sale.sale_id


# --------------------------------------------------------------------------- #
# Búsqueda con comodín %
# --------------------------------------------------------------------------- #


def test_busqueda_historial_con_comodin(services, product):
    from app.application.commands import CreateProductCommand

    services.commands.execute(
        CreateProductCommand(code="7790895008472", name="Papitas 300g", unit_price="25", stock=4, cost="10")
    )
    sell_cash(services, product.code, 1, "30", "50")
    sell_cash(services, "7790895008472", 1, "25", "50")

    matches = services.queries.ask(GetSalesHistoryQuery(search="Ag%"))
    assert len(matches) == 1
    assert matches[0].items[0].product_name == "Agua 500ml"

    matches = services.queries.ask(GetSalesHistoryQuery(search="%itas%"))
    assert len(matches) == 1
    assert matches[0].items[0].product_name == "Papitas 300g"


def test_busqueda_apartados_con_comodin(services):
    services.commands.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="30", stock=5, cost="10")
    )
    services.commands.execute(
        CreateProductCommand(code="7790895008472", name="Papitas 300g", unit_price="25", stock=5, cost="10")
    )
    services.commands.execute(
        CreateApartadoCommand(
            client_name="María López",
            client_phone="555-1234",
            items=(SaleItemRequest(code="7501055302082", quantity=1),),
        )
    )
    services.commands.execute(
        CreateApartadoCommand(
            client_name="Juan Pérez",
            client_phone="555-9999",
            items=(SaleItemRequest(code="7790895008472", quantity=1),),
            initial_abono="10",
        )
    )
    matches = services.queries.ask(GetApartadosQuery(search="Ma%"))
    assert len(matches) == 1
    assert matches[0].client_name == "María López"
    matches = services.queries.ask(GetApartadosQuery(search="555-9%"))
    assert len(matches) == 1
    assert matches[0].client_name == "Juan Pérez"