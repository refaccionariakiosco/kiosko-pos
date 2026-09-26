"""Tests de la capa de dominio (sin infraestructura)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.entities import (
    Apartado,
    Category,
    PaymentMethod,
    Product,
    Sale,
    StockMovement,
    Vale,
)
from app.domain.exceptions import (
    InsufficientStockError,
    InsufficientValeBalanceError,
    InvalidQuantityError,
    SaleAlreadyVoidedError,
    ValeAlreadyVoidedError,
    ValidationError,
)
from app.domain.value_objects import Money


def test_money_normaliza_dos_decimales() -> None:
    assert Money.from_input("12.345").as_decimal() == Decimal("12.35")
    assert Money.from_input(0.1 + 0.2).as_decimal() == Decimal("0.30")


def test_money_no_acepta_negativos() -> None:
    with pytest.raises(ValidationError):
        Money.from_input("-1")


def test_money_aritmetica() -> None:
    a = Money.from_input("10.50")
    b = Money.from_input("5.25")
    assert (a + b).as_decimal() == Decimal("15.75")
    assert (a - b).as_decimal() == Decimal("5.25")
    assert (a * 3).as_decimal() == Decimal("31.50")


def test_money_formato_es() -> None:
    assert Money.from_input("1234.5").format() == "$ 1.234,50"


def test_categoria_obligatoria() -> None:
    with pytest.raises(ValidationError):
        Category(id=None, name="   ")


def test_producto_invariantes() -> None:
    with pytest.raises(ValidationError):
        Product(code="", name="X", unit_price=Money.zero(), stock=0)
    with pytest.raises(ValidationError):
        Product(code="A1", name="X", unit_price=Money.zero(), stock=-3)


def test_producto_stock_negativo() -> None:
    product = Product(code="A1", name="X", unit_price=Money("10"), stock=2)
    with pytest.raises(InsufficientStockError):
        product.adjust_stock(-5, reason="TEST")


def test_producto_low_stock() -> None:
    product = Product(code="A1", name="X", unit_price=Money("10"), stock=2, min_stock=5)
    assert product.low_stock is True
    product.adjust_stock(+10, reason="COMPRA")
    assert product.low_stock is False


def test_sale_descuenta_stock_y_calcula_total() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    sale.add_item(product, 2)
    sale.add_item(product, 1)
    assert product.stock == 7
    assert len(sale.items) == 1
    assert sale.items[0].quantity == 3
    assert sale.subtotal.as_decimal() == Decimal("120.00")
    assert sale.total.as_decimal() == Decimal("120.00")


def test_sale_pago_insuficiente_rechazado() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    sale.add_item(product, 1)
    with pytest.raises(ValidationError):
        sale.add_payment(PaymentMethod.CASH, Money("0"))


def test_sale_con_cantidad_invalida() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    with pytest.raises(InvalidQuantityError):
        sale.add_item(product, 0)


def test_sale_void_y_reverse() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001", tendered=Money("100"))
    sale.add_item(product, 2)
    sale.add_payment(PaymentMethod.CASH, Money("80"))
    sale.finalize()
    sale.void("error")
    assert sale.status == "ANULADA"
    with pytest.raises(SaleAlreadyVoidedError):
        sale.void()
    assert sale.change().as_decimal() == Decimal("20.00")


def test_payment_method_credito_fiado() -> None:
    assert PaymentMethod.from_value("credito") is PaymentMethod.CREDIT
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    sale.add_item(product, 1)
    sale.add_payment(PaymentMethod.CREDIT, Money("40"))
    sale.finalize()
    assert sale.status == "COMPLETADA"
    assert PaymentMethod.CREDIT in sale.payment_methods


def test_sale_descuento_ajusta_total() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    sale.add_item(product, 3)
    sale.apply_discount(Money("20"))
    assert sale.subtotal.as_decimal() == Decimal("120.00")
    assert sale.total.as_decimal() == Decimal("100.00")
    sale.add_payment(PaymentMethod.CASH, Money("100"))
    sale.finalize()
    assert sale.is_fully_paid is True
    assert sale.change().as_decimal() == Decimal("0.00")
    with pytest.raises(ValidationError):
        sale.apply_discount(Money("200"))
    with pytest.raises(ValidationError):
        Sale(receipt_number="R-1").apply_discount(Money("-1"))


def test_stock_movement_valido() -> None:
    movement = StockMovement(product_id=1, delta=-2, reason=StockMovement.REASON_SALE, note="Venta R-000001")
    assert movement.delta == -2
    with pytest.raises(ValidationError):
        StockMovement(product_id=1, delta=0, reason="AJUSTE")


# --------------------------------------------------------------------------- #
# Precio por partida (override inline del cajero)
# --------------------------------------------------------------------------- #


def test_sale_acepta_precio_por_partida() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    sale.add_item(product, 2, unit_price=Money("30"))

    item = sale.items[0]
    assert item.unit_price.as_decimal() == Decimal("30.00")
    assert item.subtotal.as_decimal() == Decimal("60.00")
    assert sale.subtotal.as_decimal() == Decimal("60.00")
    assert item.price_overridden is True


def test_sale_sin_override_usa_precio_de_catalogo() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    sale.add_item(product, 2)
    assert sale.items[0].price_overridden is False

    # Mismo importe que catálogo: no es un override real.
    sale2 = Sale(receipt_number="R-000002")
    sale2.add_item(product, 2, unit_price=Money("40"))
    assert sale2.items[0].unit_price.as_decimal() == Decimal("40.00")
    assert sale2.items[0].price_overridden is False


def test_sale_rechaza_precio_no_positivo() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    with pytest.raises(ValidationError):
        sale.add_item(product, 1, unit_price=Money("0"))


def test_sale_fusiona_conservando_el_precio_ajustado() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    sale.add_item(product, 1, unit_price=Money("30"))
    sale.add_item(product, 2)

    assert len(sale.items) == 1
    assert sale.items[0].quantity == 3
    assert sale.items[0].unit_price.as_decimal() == Decimal("30.00")
    assert sale.subtotal.as_decimal() == Decimal("90.00")


def test_sale_rechaza_conflicto_de_precio_al_fusionar() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    sale = Sale(receipt_number="R-000001")
    sale.add_item(product, 1, unit_price=Money("30"))
    with pytest.raises(ValidationError):
        sale.add_item(product, 1, unit_price=Money("25"))


def test_apartado_acepta_precio_por_partida() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    apartado = Apartado(client_name="Ana", client_phone="")
    apartado.add_item(product, 2, unit_price=Money("35"))

    item = apartado.items[0]
    assert item.unit_price.as_decimal() == Decimal("35.00")
    assert item.subtotal.as_decimal() == Decimal("70.00")
    assert item.price_overridden is True
    assert apartado.total.as_decimal() == Decimal("70.00")


def test_apartado_rechaza_precio_no_positivo() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    apartado = Apartado(client_name="Ana", client_phone="")
    with pytest.raises(ValidationError):
        apartado.add_item(product, 1, unit_price=Money("0"))


def test_apartado_rechaza_conflicto_de_precio_al_fusionar() -> None:
    product = Product(code="A1", name="Gaseosa", unit_price=Money("40"), stock=10)
    apartado = Apartado(client_name="Ana", client_phone="")
    apartado.add_item(product, 1, unit_price=Money("35"))
    with pytest.raises(ValidationError):
        apartado.add_item(product, 1, unit_price=Money("20"))


# --------------------------------------------------------------------------- #
# Vales de compra
# --------------------------------------------------------------------------- #


def _vale(amount: str = "50") -> Vale:
    return Vale(code="V-7KQ4M2", amount=Money(amount), branch_id="SUC-1")


def test_vale_arranca_activo_con_saldo_inicial() -> None:
    vale = _vale("50")

    assert vale.status == Vale.STATUS_ACTIVE
    assert vale.balance.as_decimal() == Decimal("50.00")
    assert vale.is_usable() is True


def test_vale_reduce_saldo_al_redimir() -> None:
    vale = _vale("50")

    applied = vale.redeem(Money("20"))

    assert applied.as_decimal() == Decimal("20.00")
    assert vale.balance.as_decimal() == Decimal("30.00")
    assert vale.status == Vale.STATUS_ACTIVE


def test_vale_queda_agotado_al_consumir_el_saldo() -> None:
    vale = _vale("50")

    vale.redeem(Money("50"))

    assert vale.balance.as_decimal() == Decimal("0.00")
    assert vale.status == Vale.STATUS_USED_UP
    assert vale.is_usable() is False


def test_vale_rechaza_redimir_mas_que_el_saldo() -> None:
    vale = _vale("50")

    with pytest.raises(InsufficientValeBalanceError):
        vale.redeem(Money("80"))


def test_vale_rechaza_redimir_importe_no_positivo() -> None:
    vale = _vale("50")

    with pytest.raises(ValidationError):
        vale.redeem(Money("0"))


def test_vale_anulado_deja_saldo_en_cero_y_no_es_redimible() -> None:
    vale = _vale("50")
    vale.redeem(Money("20"))

    vale.void("Error de carga")

    assert vale.status == Vale.STATUS_VOID
    assert vale.balance.as_decimal() == Decimal("0.00")
    assert vale.is_usable() is False
    assert vale.voided_at is not None


def test_vale_rechaza_anular_dos_veces() -> None:
    vale = _vale("50")
    vale.void("Error de carga")

    with pytest.raises(ValeAlreadyVoidedError):
        vale.void("Otra vez")


def test_vale_agotado_no_se_anula_al_desvincular_la_venta() -> None:
    """Si el cliente ya gastó el vale, anular la venta original no puede fallar.

    El saldo se consumió en compras reales: no hay nada que devolver, así que el
    vale queda ``AGOTADO`` como registro histórico en vez de romperse la anulación.
    """
    vale = _vale("50")
    vale.redeem(Money("50"))

    vale.void("Se anuló la venta que lo emitió")

    assert vale.status == Vale.STATUS_USED_UP
    assert vale.voided_at is None


def test_vale_conserva_saldo_al_crearse_ya_redimido() -> None:
    """Al releer de la base el saldo viene explícito y no debe pisarse por el monto."""
    vale = Vale(code="V-ABC123", amount=Money("50"), balance=Money("12"), branch_id="SUC-1")

    assert vale.balance.as_decimal() == Decimal("12.00")
    assert vale.status == Vale.STATUS_ACTIVE


def test_vale_agotado_no_revive_al_releerse_de_la_base() -> None:
    """Regresión: un saldo en cero es real, no 'saldo sin informar'.

    Si se tomara como centinela, el vale volvería a valer el monto original y el
    cliente podría redimir el mismo crédito otra vez.
    """
    vale = Vale(code="V-ABC123", amount=Money("50"), balance=Money("0"), branch_id="SUC-1")

    assert vale.balance.as_decimal() == Decimal("0.00")
    assert vale.status == Vale.STATUS_USED_UP
    assert vale.is_usable() is False


def test_vale_nuevo_toma_el_monto_como_saldo_inicial() -> None:
    vale = Vale(code="V-ABC123", amount=Money("50"), branch_id="SUC-1")

    assert vale.balance.as_decimal() == Decimal("50.00")
    assert vale.status == Vale.STATUS_ACTIVE
