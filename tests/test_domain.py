"""Tests de la capa de dominio (sin infraestructura)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.entities import Category, PaymentMethod, Product, Sale, StockMovement
from app.domain.exceptions import (
    InsufficientStockError,
    InvalidQuantityError,
    SaleAlreadyVoidedError,
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