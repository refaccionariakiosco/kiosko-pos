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
    VoidValeCommand,
)
from app.application.queries import (
    GetCatalogQuery,
    GetDashboardQuery,
    GetSaleQuery,
    GetSalesHistoryQuery,
    GetValeQuery,
    ListValesQuery,
)
from app.domain.exceptions import (
    DomainError,
    DuplicateProductCodeError,
    InsufficientStockError,
    InsufficientValeBalanceError,
    ProductNotFoundError,
    SaleNotFoundError,
    ValeAlreadyVoidedError,
    ValeNotFoundError,
    ValidationError,
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


# --------------------------------------------------------------------------- #
# Precio por partida persistido
# --------------------------------------------------------------------------- #


def test_venta_persiste_precio_por_partida(services):
    product = make_product(services, price="20", stock=5)
    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=3, unit_price="15"),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="45"),),
        )
    )
    assert result.total.as_decimal() == Decimal("45.00")

    sale = services.queries.ask(GetSaleQuery(sale_id=result.sale_id))
    item = sale.items[0]
    assert item.unit_price.as_decimal() == Decimal("15.00")
    assert item.subtotal.as_decimal() == Decimal("45.00")
    assert item.price_overridden is True


def test_venta_sin_override_no_marca_la_partida(services):
    product = make_product(services, price="20", stock=5)
    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40"),),
        )
    )
    sale = services.queries.ask(GetSaleQuery(sale_id=result.sale_id))
    assert sale.items[0].unit_price.as_decimal() == Decimal("20.00")
    assert sale.items[0].price_overridden is False


def test_override_mas_descuento_global_se_apilan(services):
    """El precio por partida y el ajuste de monto del ticket se combinan."""
    product = make_product(services, price="20", stock=10)
    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=2, unit_price="15"),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="25"),),
            discount="5",
        )
    )
    # 2 x 15 = 30 de subtotal, menos 5 de descuento global.
    assert result.sale.subtotal.as_decimal() == Decimal("30.00")
    assert result.sale.discount.as_decimal() == Decimal("5.00")
    assert result.total.as_decimal() == Decimal("25.00")
    assert result.change_amount.as_decimal() == Decimal("0.00")


def test_venta_rechaza_precio_no_positivo(services):
    from app.domain.value_objects import Money

    product = make_product(services, price="20", stock=5)
    with pytest.raises(DomainError):
        services.commands.execute(
            CompleteSaleCommand(
                items=(SaleItemRequest(code=product.code, quantity=1, unit_price=Money("0")),),
                payments=(SalePaymentRequest(method="EFECTIVO", amount="1"),),
            )
        )
    assert services.queries.ask(GetCatalogQuery())[0].stock == 5


# --------------------------------------------------------------------------- #
# Vales de compra: se emiten con tarjeta y se aplican en compras siguientes
# --------------------------------------------------------------------------- #


def sell_card(services, product, quantity, total, *, issue_vale=False, vale_amount=None):
    """Venta pagada con tarjeta, que es la que puede dejar vale."""
    return services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=quantity),),
            payments=(SalePaymentRequest(method="TARJETA", amount=total),),
            issue_vale=issue_vale,
            vale_amount=vale_amount,
        )
    )


def test_venta_con_tarjeta_emite_vale_si_lo_pide_el_cajero(services):
    product = make_product(services, price="100", stock=10)

    result = sell_card(services, product, 2, "200", issue_vale=True, vale_amount="150")

    assert result.issued_vale is not None
    assert result.issued_vale.code.startswith("V-")
    assert result.issued_vale.amount.as_decimal() == Decimal("150.00")
    assert result.issued_vale.balance.as_decimal() == Decimal("150.00")
    assert result.issued_vale.status == "ACTIVO"
    assert result.issued_vale.receipt_number == result.receipt_number


def test_venta_con_tarjeta_no_emite_vale_si_no_lo_piden(services):
    product = make_product(services, price="100", stock=10)

    result = sell_card(services, product, 2, "200")

    assert result.issued_vale is None
    assert services.queries.ask(ListValesQuery()) == []


def test_vale_se_aplica_completo_a_la_compra_siguiente(services):
    product = make_product(services, price="100", stock=20)
    issued = sell_card(services, product, 2, "200", issue_vale=True, vale_amount="80").issued_vale

    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=1),),
            payments=(),
            vale_code=issued.code,
        )
    )

    assert result.vale_applied.as_decimal() == Decimal("80.00")
    assert result.vale_remaining.as_decimal() == Decimal("0.00")
    assert result.sale.total.as_decimal() == Decimal("100.00")
    # El pago entra como VALE, así que el corte lo ve como crédito aplicado.
    assert [p.method for p in result.sale.payments] == ["VALE"]
    restante = services.queries.ask(GetValeQuery(code=issued.code))
    assert restante.status == "AGOTADO"


def test_vale_se_aplica_parcialmente_y_deja_saldo_para_despues(services):
    product = make_product(services, price="100", stock=20)
    issued = sell_card(services, product, 2, "200", issue_vale=True, vale_amount="80").issued_vale

    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=1),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="20"),),
            vale_code=issued.code,
        )
    )

    # El vale cubre 80 de los 100; los 20 restantes se cobran en efectivo.
    assert result.vale_applied.as_decimal() == Decimal("80.00")
    assert result.vale_remaining.as_decimal() == Decimal("0.00")
    restante = services.queries.ask(GetValeQuery(code=issued.code))
    assert restante.status == "AGOTADO"
    assert restante.balance.as_decimal() == Decimal("0.00")


def test_vale_no_alcanza_y_se_cobra_el_diferencia_en_efectivo(services):
    product = make_product(services, price="100", stock=20)
    issued = sell_card(services, product, 1, "100", issue_vale=True, vale_amount="30").issued_vale

    result = services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=1),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="70"),),
            vale_code=issued.code,
        )
    )

    assert result.vale_applied.as_decimal() == Decimal("30.00")
    # 30 de vale + 70 en efectivo cubren los 100 del producto.
    pagado = sum(p.amount.as_decimal() for p in result.sale.payments)
    assert pagado == Decimal("100.00")
    assert [p.method for p in result.sale.payments] == ["EFECTIVO", "VALE"]


def test_anular_venta_anula_el_vale_que_emitio(services):
    product = make_product(services, price="100", stock=20)
    result = sell_card(services, product, 2, "200", issue_vale=True, vale_amount="150")

    services.commands.execute(VoidSaleCommand(sale_id=result.sale_id, reason="Error de cajero"))

    vale = services.queries.ask(GetValeQuery(code=result.issued_vale.code))
    assert vale.status == "ANULADO"
    assert vale.balance.as_decimal() == Decimal("0.00")
    assert vale.is_usable is False


def test_anular_venta_con_vale_ya_gastado_no_falla(services):
    """El vale se consumió en otra compra: la anulación no debe romperse."""
    product = make_product(services, price="100", stock=30)
    result = sell_card(services, product, 1, "100", issue_vale=True, vale_amount="100")
    services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=1),),
            payments=(),
            vale_code=result.issued_vale.code,
        )
    )

    anulada = services.commands.execute(
        VoidSaleCommand(sale_id=result.sale_id, reason="Error de cajero")
    )

    assert anulada.status == "ANULADA"
    vale = services.queries.ask(GetValeQuery(code=result.issued_vale.code))
    assert vale.status == "AGOTADO"


def test_vale_de_otra_sucursal_no_se_puede_usar(tmp_path):
    """El vale no viaja entre cajas: sólo lo redime la sucursal que lo emitió."""
    from app.bootstrap import build_services
    from app.infrastructure.topology import Topology
    from app.settings import Settings

    db = tmp_path / "kiosco.db"
    origen = build_services(
        settings=Settings(database_path=str(db)),
        topology=Topology(id_sucursal="SUC-A", id_terminal="T-A", terminal_num="1"),
    )
    product = make_product(origen, price="100", stock=30)
    issued = sell_card(origen, product, 1, "100", issue_vale=True, vale_amount="100").issued_vale
    assert issued.branch_id == "SUC-A"

    # Segunda caja, otra sucursal, misma base compartida. El stock es por
    # sucursal, así que esta caja arranca con su propia existencia.
    otra = build_services(
        settings=Settings(database_path=str(db)),
        topology=Topology(id_sucursal="SUC-B", id_terminal="T-B", terminal_num="1"),
    )
    otra.commands.execute(AdjustStockCommand(product_id=product.id, delta=5, reason="CONTEO"))

    with pytest.raises(ValidationError):
        otra.commands.execute(
            CompleteSaleCommand(
                items=(SaleItemRequest(code=product.code, quantity=1),),
                payments=(SalePaymentRequest(method="EFECTIVO", amount="100"),),
                vale_code=issued.code,
            )
        )

    # Y el vale sigue intacto para su sucursal de origen.
    assert otra.queries.ask(GetValeQuery(code=issued.code)).balance.as_decimal() == Decimal(
        "100.00"
    )


def test_codigo_de_vale_inexistente_falla(services):
    product = make_product(services, price="100", stock=10)

    with pytest.raises(ValeNotFoundError):
        services.commands.execute(
            CompleteSaleCommand(
                items=(SaleItemRequest(code=product.code, quantity=1),),
                payments=(SalePaymentRequest(method="EFECTIVO", amount="100"),),
                vale_code="V-XXXXXX",
            )
        )


def test_emision_de_vale_exige_importe(services):
    product = make_product(services, price="100", stock=10)

    with pytest.raises(ValidationError):
        sell_card(services, product, 1, "100", issue_vale=True, vale_amount=None)
    assert services.queries.ask(ListValesQuery()) == []


def test_anular_vale_lo_deja_sin_saldo(services):
    product = make_product(services, price="100", stock=10)
    issued = sell_card(services, product, 1, "100", issue_vale=True, vale_amount="50").issued_vale

    anulado = services.commands.execute(
        VoidValeCommand(vale_id=issued.id, reason="Se emitió por error")
    )

    assert anulado.status == "ANULADO"
    assert anulado.balance.as_decimal() == Decimal("0.00")


def test_anular_vale_dos_veces_falla(services):
    product = make_product(services, price="100", stock=10)
    issued = sell_card(services, product, 1, "100", issue_vale=True, vale_amount="50").issued_vale
    services.commands.execute(VoidValeCommand(vale_id=issued.id, reason="Error"))

    with pytest.raises(ValeAlreadyVoidedError):
        services.commands.execute(VoidValeCommand(vale_id=issued.id, reason="Error"))


def test_vale_agotado_no_se_puede_volver_a_usar(services):
    product = make_product(services, price="100", stock=30)
    issued = sell_card(services, product, 1, "100", issue_vale=True, vale_amount="100").issued_vale
    services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=product.code, quantity=1),),
            payments=(),
            vale_code=issued.code,
        )
    )

    with pytest.raises(InsufficientValeBalanceError):
        services.commands.execute(
            CompleteSaleCommand(
                items=(SaleItemRequest(code=product.code, quantity=1),),
                payments=(),
                vale_code=issued.code,
            )
        )


def test_vale_buscado_por_codigo_ignora_mayusculas(services):
    product = make_product(services, price="100", stock=10)
    issued = sell_card(services, product, 1, "100", issue_vale=True, vale_amount="50").issued_vale

    encontrado = services.queries.ask(GetValeQuery(code=issued.code.lower()))

    assert encontrado is not None
    assert encontrado.code == issued.code


def test_listado_de_vales_filtra_por_estado(services):
    product = make_product(services, price="100", stock=10)
    primer = sell_card(services, product, 1, "100", issue_vale=True, vale_amount="50").issued_vale
    segundo = sell_card(services, product, 1, "100", issue_vale=True, vale_amount="30").issued_vale
    services.commands.execute(VoidValeCommand(vale_id=segundo.id, reason="Error"))

    activos = services.queries.ask(ListValesQuery(status="ACTIVO"))
    anulados = services.queries.ask(ListValesQuery(status="ANULADO"))

    assert [v.code for v in activos] == [primer.code]
    assert [v.code for v in anulados] == [segundo.code]


def test_codigos_de_vale_no_se_repiten(services):
    product = make_product(services, price="100", stock=40)
    emitidos = {
        sell_card(services, product, 1, "100", issue_vale=True, vale_amount="10").issued_vale.code
        for _ in range(8)
    }

    assert len(emitidos) == 8
