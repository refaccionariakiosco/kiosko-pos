"""Tests de casos de uso (CQRS) sobre SQLite en memoria."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.application.commands import (
    AdjustStockCommand,
    CompleteSaleCommand,
    CreateCategoryCommand,
    CreateProductCommand,
    SaleItemRequest,
    SalePaymentRequest,
    SetProductActiveCommand,
    UpdateProductCommand,
    VoidSaleCommand,
)
from app.application.queries import (
    GetCatalogQuery,
    GetDashboardQuery,
    GetSaleQuery,
    GetSalesHistoryQuery,
)
from app.domain.exceptions import (
    DomainError,
    DuplicateProductCodeError,
    InsufficientStockError,
    ProductNotFoundError,
)


@pytest.fixture
def category(services):
    return services.commands.execute(CreateCategoryCommand(name="BEBIDAS", description="Bebidas"))


def make_product(services, code="7501055302082", name="Agua 500ml", price="20", stock=5, category_id=None):
    return services.commands.execute(
        CreateProductCommand(
            code=code,
            name=name,
            unit_price=price,
            stock=stock,
            category_id=category_id,
            cost="10",
        )
    )


def test_crear_producto_y_consultarlo(services):
    created = make_product(services)
    catalog = services.queries.ask(GetCatalogQuery())
    assert len(catalog) == 1
    assert catalog[0].code == "7501055302082"
    assert catalog[0].unit_price.as_decimal() == Decimal("20.00")
    assert catalog[0].stock == 5


def test_codigo_duplicado(services):
    make_product(services)
    with pytest.raises(DuplicateProductCodeError):
        make_product(services)


def test_actualizar_producto(services):
    product = make_product(services)
    services.commands.execute(
        UpdateProductCommand(
            product_id=product.id,
            code=product.code,
            name="Agua 600ml",
            unit_price="25",
            min_stock=3,
        )
    )
    data = services.queries.ask(GetCatalogQuery())[0]
    assert data.name == "Agua 600ml"
    assert data.unit_price.as_decimal() == Decimal("25.00")


def test_desactivar_producto(services):
    product = make_product(services)
    services.commands.execute(SetProductActiveCommand(product_id=product.id, active=False))
    assert services.queries.ask(GetCatalogQuery()) == []
    assert len(services.queries.ask(GetCatalogQuery(include_inactive=True))) == 1


def test_venta_completa_efectivo(services):
    product = make_product(services, stock=5, category_id=None)
    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=3),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="60"),),
            tendered="100",
        )
    )
    assert result.receipt_number.startswith("R-")
    assert result.total.as_decimal() == Decimal("60.00")
    assert result.change_amount.as_decimal() == Decimal("40.00")

    sale = services.queries.ask(GetSaleQuery(sale_id=result.sale_id))
    assert sale.status == "COMPLETADA"
    assert sale.item_count == 3
    assert sale.methods_label == "EFECTIVO"

    catalog = services.queries.ask(GetCatalogQuery())
    assert catalog[0].stock == 2


def test_venta_con_stock_insuficiente(services):
    product = make_product(services, stock=2)
    with pytest.raises(InsufficientStockError):
        services.commands.execute(
            CompleteSaleCommand(
                items=(SaleItemRequest(code=product.code, quantity=9),),
                payments=(SalePaymentRequest(method="TARJETA", amount="9999")),
            )
        )
    # la transacción debe revertirse: el stock queda intacto
    assert services.queries.ask(GetCatalogQuery())[0].stock == 2


def test_venta_sin_productos(services):
    with pytest.raises(DomainError):
        services.commands.execute(CompleteSaleCommand(payments=(SalePaymentRequest(method="TARJETA", amount="1"),)))


def test_venta_producto_inexistente(services):
    with pytest.raises(ProductNotFoundError):
        services.commands.execute(
            CompleteSaleCommand(
                items=(SaleItemRequest(code="000", quantity=1),),
                payments=(SalePaymentRequest(method="TARJETA", amount="10")),
            )
        )


def test_venta_mixta_dos_metodos(services):
    product = make_product(services, price="100", stock=5, category_id=None)
    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2),),
            payments=(
                SalePaymentRequest(method="EFECTIVO", amount="120"),
                SalePaymentRequest(method="TARJETA", amount="80"),
            ),
            tendered="120",
        )
    )
    assert result.total.as_decimal() == Decimal("200.00")
    sale = services.queries.ask(GetSaleQuery(sale_id=result.sale_id))
    assert sale.status == "COMPLETADA"
    assert sale.methods_label == "EFECTIVO / TARJETA"
    assert sale.change_amount.as_decimal() == Decimal("0.00")
    assert services.queries.ask(GetCatalogQuery())[0].stock == 3


def test_venta_credito_fiado(services):
    product = make_product(services, price="40", stock=4, category_id=None)
    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2),),
            payments=(SalePaymentRequest(method="CREDITO", amount="80"),),
        )
    )
    sale = services.queries.ask(GetSaleQuery(sale_id=result.sale_id))
    assert sale.status == "COMPLETADA"
    assert sale.methods_label == "CREDITO"
    assert sale.change_amount.as_decimal() == Decimal("0.00")


def test_venta_con_descuento(services):
    product = make_product(services, price="40", stock=6, category_id=None)
    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=3),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="100")),
            tendered="120",
            discount="20",
        )
    )
    assert result.total.as_decimal() == Decimal("100.00")
    assert result.change_amount.as_decimal() == Decimal("20.00")
    sale = services.queries.ask(GetSaleQuery(sale_id=result.sale_id))
    assert sale.discount.as_decimal() == Decimal("20.00")
    assert sale.total.as_decimal() == Decimal("100.00")
    assert len(services.queries.ask(GetCatalogQuery())) == 1


def test_descuento_no_puede_superar_subtotal(services):
    product = make_product(services, price="40", stock=1, category_id=None)
    with pytest.raises(DomainError):
        services.commands.execute(
            CompleteSaleCommand(
                items=(SaleItemRequest(code=product.code, quantity=1),),
                payments=(SalePaymentRequest(method="TARJETA", amount="40")),
                discount="999",
            )
        )


def test_anular_venta_restaura_stock(services):
    product = make_product(services, stock=4)
    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40")),
        )
    )
    assert services.queries.ask(GetCatalogQuery())[0].stock == 2

    sales = services.queries.ask(GetSalesHistoryQuery(status="COMPLETADA"))
    voided = services.commands.execute(VoidSaleCommand(sale_id=sales[0].id))
    assert voided.status == "ANULADA"
    assert services.queries.ask(GetCatalogQuery())[0].stock == 4
    assert len(services.queries.ask(GetSalesHistoryQuery(status="COMPLETADA"))) == 0


def test_ajuste_de_stock_con_registro(services):
    product = make_product(services, stock=5)
    result = services.commands.execute(AdjustStockCommand(product_id=product.id, delta=+15, reason="COMPRA"))
    assert result.stock == 20
    assert product.code in [p.code for p in services.queries.ask(GetCatalogQuery())]


def test_dashboard_mide_bien_el_dia(services):
    agua = make_product(services, code="7501055302082", name="Agua", price="20", stock=10)
    papas = make_product(services, code="7790895008472", name="Papas", price="30", stock=10)
    services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=agua.code, quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40"),),
        )
    )
    services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=papas.code, quantity=1),),
            payments=(SalePaymentRequest(method="TARJETA", amount="30"),),
        )
    )
    dash = services.queries.ask(GetDashboardQuery())
    assert dash.today_sales_count == 2
    assert dash.today_revenue.as_decimal() == Decimal("70.00")
    assert dash.today_revenue_by_method["EFECTIVO"].as_decimal() == Decimal("40.00")
    assert dash.today_revenue_by_method["TARJETA"].as_decimal() == Decimal("30.00")
    assert dash.top_products[0].name == "Agua"