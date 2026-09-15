"""Tests del sistema de apartados (CQRS sobre SQLite en memoria)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.application.commands import (
    AddAbonoCommand,
    ApartadoItemRequest,
    CancelApartadoCommand,
    CreateApartadoCommand,
    CreateCategoryCommand,
    CreateProductCommand,
)
from app.application.queries import GetApartadoQuery, GetApartadosQuery, GetCatalogQuery
from app.domain.exceptions import DomainError


@pytest.fixture
def category(services):
    return services.commands.execute(CreateCategoryCommand(name="BEBIDAS"))


@pytest.fixture
def agua(services, category):
    return services.commands.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=category.id)
    )


def make_apartado(services, agua, client="Juan", abono=None, qty=2):
    return services.commands.execute(
        CreateApartadoCommand(
            client_name=client,
            client_phone="11-5555-5555",
            items=(ApartadoItemRequest(code=agua.code, quantity=qty),),
            initial_abono=abono,
        )
    )


def test_crear_apartado_reserva_stock(services, agua):
    apartado = make_apartado(services, agua, qty=3)
    assert apartado.status == "ACTIVO"
    assert apartado.total.as_decimal() == Decimal("60.00")
    assert apartado.amount_paid.as_decimal() == Decimal("0.00")
    assert apartado.balance.as_decimal() == Decimal("60.00")
    assert apartado.item_count == 3
    assert apartado.client_phone == "11-5555-5555"
    assert services.queries.ask(GetCatalogQuery())[0].stock == 7


def test_apartado_sin_items_rechazado(services):
    with pytest.raises(DomainError):
        services.commands.execute(CreateApartadoCommand(client_name="Juan"))


def test_apartado_requiere_cliente(services, agua):
    with pytest.raises(DomainError):
        services.commands.execute(
            CreateApartadoCommand(client_name="", items=(ApartadoItemRequest(code=agua.code, quantity=1),))
        )


def test_apartado_con_abono_inicial(services, agua):
    apartado = make_apartado(services, agua, abono="20")
    assert apartado.amount_paid.as_decimal() == Decimal("20.00")
    assert apartado.balance.as_decimal() == Decimal("20.00")
    assert apartado.abonos_count == 1


def test_abono_liquida_apartado(services, agua):
    apartado = make_apartado(services, agua, abono="30", qty=3)  # total 60
    assert apartado.status == "ACTIVO"

    updated = services.commands.execute(AddAbonoCommand(apartado_id=apartado.id, amount="30"))
    assert updated.status == "LIQUIDADO"
    assert updated.balance.as_decimal() == Decimal("0.00")
    assert updated.abonos_count == 2

    data = services.queries.ask(GetApartadoQuery(apartado_id=apartado.id))
    assert data.status == "LIQUIDADO"


def test_abono_no_puede_superar_saldo(services, agua):
    apartado = make_apartado(services, agua, qty=1)  # total 20
    with pytest.raises(DomainError):
        services.commands.execute(AddAbonoCommand(apartado_id=apartado.id, amount="30"))


def test_abono_en_apartado_liquidado_rechazado(services, agua):
    apartado = make_apartado(services, agua, abono="20", qty=1)  # se liquida al crear
    assert apartado.status == "LIQUIDADO"
    with pytest.raises(DomainError):
        services.commands.execute(AddAbonoCommand(apartado_id=apartado.id, amount="5"))


def test_cancelar_apartado_restituye_stock(services, agua):
    apartado = make_apartado(services, agua, abono="20", qty=2)  # total 40, abonado 20
    assert services.queries.ask(GetCatalogQuery())[0].stock == 8

    cancelled = services.commands.execute(CancelApartadoCommand(apartado_id=apartado.id, reason="Cliente no vino"))
    assert cancelled.status == "CANCELADO"
    assert services.queries.ask(GetCatalogQuery())[0].stock == 10

    assert len(services.queries.ask(GetApartadosQuery(status="ACTIVO"))) == 0
    assert len(services.queries.ask(GetApartadosQuery(status="CANCELADO"))) == 1


def test_apartado_stock_insuficiente(services, agua):
    with pytest.raises(DomainError):
        services.commands.execute(
            CreateApartadoCommand(
                client_name="Juan",
                items=(ApartadoItemRequest(code=agua.code, quantity=99),),
            )
        )


def test_apartados_listados_por_estado(services, agua):
    make_apartado(services, agua, client="Ana", qty=1)
    make_apartado(services, agua, client="Leo", qty=2)  # total 40
    liquidado_apartado = services.commands.execute(
        CreateApartadoCommand(
            client_name="Betty",
            items=(ApartadoItemRequest(code=agua.code, quantity=1),),
            initial_abono="20",
        )
    )
    assert liquidado_apartado.status == "LIQUIDADO"

    activos = services.queries.ask(GetApartadosQuery(status="ACTIVO"))
    liquidados = services.queries.ask(GetApartadosQuery(status="LIQUIDADO"))
    todos = services.queries.ask(GetApartadosQuery(status=None))
    assert {a.client_name for a in activos} == {"Ana", "Leo"}
    assert [a.client_name for a in liquidados] == ["Betty"]
    assert len(todos) == 3