"""Pruebas de pedidos guardados (guardar/listar/eliminar/recibir) y de la
persistencia de configuración (sys_config)."""

from __future__ import annotations

import pytest

from app.application import (
    CreateProductCommand,
    CreateProviderCommand,
    DeletePurchaseOrderCommand,
    GetCatalogQuery,
    ImportProviderItemsCommand,
    ListPurchaseOrdersQuery,
    ListProviderItemsQuery,
    ProviderItemRequest,
    PurchaseOrderLineRequest,
    ReceiveLineRequest,
    ReceivePurchaseOrderCommand,
    SavePurchaseOrderCommand,
    SaveSettingsCommand,
)
from app.domain.exceptions import ValidationError
from app.infrastructure.local_config import load_local_config
from app.bootstrap import build_services


def _save_sample_order(services, note: str = "Pedido de prueba"):
    provider = services.commands.execute(CreateProviderCommand(name="Distribuidora 9"))
    count = services.commands.execute(
        ImportProviderItemsCommand(
            provider_id=provider.id,
            items=(
                ProviderItemRequest(code="A-1", description="Gaseosa 1.5L", price="1800.50"),
                ProviderItemRequest(code="A-2", description="Agua 500ml", price="700"),
            ),
        )
    )
    assert count == 2
    items = services.queries.ask(ListProviderItemsQuery(provider_id=provider.id))
    by_code = {i.code: i for i in items}
    order = services.commands.execute(
        SavePurchaseOrderCommand(
            note=note,
            lines=(
                PurchaseOrderLineRequest(
                    code="A-1",
                    description="Gaseosa 1.5L",
                    provider_name="Distribuidora 9",
                    quantity=5,
                    unit_price="1800.50",
                    provider_item_id=by_code["A-1"].id,
                ),
                PurchaseOrderLineRequest(
                    code="A-2",
                    description="Agua 500ml",
                    provider_name="Distribuidora 9",
                    quantity=10,
                    unit_price="700",
                    provider_item_id=by_code["A-2"].id,
                ),
            ),
        )
    )
    return order


# --------------------------------------------------------------------------- #
# Guardar y listar
# --------------------------------------------------------------------------- #


def test_guardar_pedido_y_listarlo(services) -> None:
    saved = _save_sample_order(services)
    assert saved.id > 0
    assert saved.order_number == "P-000001"
    assert saved.status == "PENDIENTE"
    assert saved.item_count == 15
    assert saved.total.format() == "$ 16.002,50"
    assert saved.provider_names == ["Distribuidora 9"]

    orders = services.queries.ask(ListPurchaseOrdersQuery())
    assert len(orders) == 1
    order = orders[0]
    assert order.order_number == "P-000001"
    assert len(order.lines) == 2
    assert order.lines[0].subtotal.format() == "$ 9.002,50"


def test_guardar_pedido_sin_renglones_rechazado(services) -> None:
    with pytest.raises(ValidationError):
        services.commands.execute(SavePurchaseOrderCommand(note="", lines=()))


def test_ordenes_numeradas_secuencialmente(services) -> None:
    _save_sample_order(services)
    second = _save_sample_order(services, note="Segundo")
    assert second.order_number == "P-000002"


def test_listar_pedidos_por_estado(services) -> None:
    _save_sample_order(services)
    pending = services.queries.ask(ListPurchaseOrdersQuery(status="PENDIENTE"))
    assert len(pending) == 1
    received = services.queries.ask(ListPurchaseOrdersQuery(status="RECIBIDO"))
    assert received == []


# --------------------------------------------------------------------------- #
# Eliminar
# --------------------------------------------------------------------------- #


def test_eliminar_pedido_pendiente(services) -> None:
    order = _save_sample_order(services)
    services.commands.execute(DeletePurchaseOrderCommand(purchase_order_id=order.id))
    assert services.queries.ask(ListPurchaseOrdersQuery()) == []


def test_eliminar_pedido_inexistente(services) -> None:
    with pytest.raises(ValidationError):
        services.commands.execute(DeletePurchaseOrderCommand(purchase_order_id=9999))


# --------------------------------------------------------------------------- #
# Recibir un pedido guardado
# --------------------------------------------------------------------------- #


def test_recepcion_de_pedido_guardado_lo_marca_recibido(services) -> None:
    provider = services.commands.execute(CreateProviderCommand(name="Distribuidora"))
    services.commands.execute(
        ImportProviderItemsCommand(
            provider_id=provider.id,
            items=(
                ProviderItemRequest(code="A-1", description="Gaseosa 1.5L", price="1800.50"),
                ProviderItemRequest(code="A-2", description="Agua 500ml", price="700"),
            ),
        )
    )
    product = services.commands.execute(
        CreateProductCommand(code="A-1", name="Gaseosa 1.5L", unit_price="2500", stock=0)
    )
    items = services.queries.ask(ListProviderItemsQuery(provider_id=provider.id))
    by_code = {i.code: i for i in items}
    order = services.commands.execute(
        SavePurchaseOrderCommand(
            note="Para el sábado",
            lines=(
                PurchaseOrderLineRequest(
                    code="A-1",
                    description="Gaseosa 1.5L",
                    provider_name="Distribuidora",
                    quantity=4,
                    unit_price="1800.50",
                    provider_item_id=by_code["A-1"].id,
                ),
            ),
        )
    )

    received = services.commands.execute(
        ReceivePurchaseOrderCommand(
            note="Llegó",
            purchase_order_id=order.id,
            lines=(
                ReceiveLineRequest(
                    provider_item_id=by_code["A-1"].id,
                    product_id=product.id,
                    quantity=4,
                    unit_price="1800.50",
                ),
            ),
        )
    )
    assert received == 4

    orders = services.queries.ask(ListPurchaseOrdersQuery(status="RECIBIDO"))
    assert [o.id for o in orders] == [order.id]
    assert orders[0].status == "RECIBIDO"

    catalog = services.queries.ask(GetCatalogQuery())
    updated = next(p for p in catalog if p.id == product.id)
    assert updated.stock == 4


def test_recibir_pedido_ya_recibido_rechazado(services) -> None:
    provider = services.commands.execute(CreateProviderCommand(name="Distribuidora"))
    services.commands.execute(
        ImportProviderItemsCommand(
            provider_id=provider.id,
            items=(ProviderItemRequest(code="A-1", description="Gaseosa 1.5L", price="1800.50"),),
        )
    )
    product = services.commands.execute(
        CreateProductCommand(code="A-1", name="Gaseosa 1.5L", unit_price="2500", stock=0)
    )
    items = services.queries.ask(ListProviderItemsQuery(provider_id=provider.id))
    order = services.commands.execute(
        SavePurchaseOrderCommand(
            lines=(
                PurchaseOrderLineRequest(
                    code="A-1",
                    description="Gaseosa 1.5L",
                    provider_name="Distribuidora",
                    quantity=2,
                    unit_price="1800.50",
                ),
            ),
        )
    )
    line = ReceiveLineRequest(product_id=product.id, quantity=2, unit_price="1800.50")
    services.commands.execute(ReceivePurchaseOrderCommand(note="", purchase_order_id=order.id, lines=(line,)))
    with pytest.raises(ValidationError):
        services.commands.execute(ReceivePurchaseOrderCommand(note="", purchase_order_id=order.id, lines=(line,)))


def test_recibir_con_pedido_inexistente_rechazado(services) -> None:
    product = services.commands.execute(
        CreateProductCommand(code="A-1", name="Gaseosa 1.5L", unit_price="2500", stock=0)
    )
    with pytest.raises(ValidationError):
        services.commands.execute(
            ReceivePurchaseOrderCommand(
                purchase_order_id=9999,
                lines=(ReceiveLineRequest(product_id=product.id, quantity=1, unit_price="10"),),
            )
        )


# --------------------------------------------------------------------------- #
# Configuración persistente (sys_config)
# --------------------------------------------------------------------------- #


def test_guardar_y_recargar_configuracion(tmp_path) -> None:
    from app.settings import Settings

    db = str(tmp_path / "config.db")
    services = build_services(settings=Settings(database_path=db))
    services.commands.execute(
        SaveSettingsCommand(
            store_name="KIOSCO 24",
            store_address="Av. Siempre Viva 742",
            store_phone="4444-5555",
            store_footer="Gracias por su compra",
            currency="US$",
            login_username="admin",
            login_password="s3cret",
            label_printer_kind="brother_ql",
            brother_printer_ip="192.168.1.50",
            label_width_mm=62.0,
            label_height_mm=29.0,
            label_dpi=300,
            id_sucursal="LOCAL-A",
            pocketbase_url="http://192.168.100.6:8090",
            pocketbase_token="pb-token-1",
        )
    )

    settings2 = Settings(database_path=db)
    services2 = build_services(settings=settings2)
    assert services2.settings.store.name == "KIOSCO 24"
    assert services2.settings.store.address == "Av. Siempre Viva 742"
    assert services2.settings.store.phone == "4444-5555"
    assert services2.settings.store.footer == "Gracias por su compra"
    assert services2.settings.currency == "US$"
    assert services2.settings.login_username == "admin"
    assert services2.settings.login_password == "s3cret"
    assert services2.settings.label_printer_kind == "brother_ql"
    assert services2.settings.brother_printer_ip == "192.168.1.50"
    assert services2.settings.label_width_mm == 62.0
    assert services2.settings.label_height_mm == 29.0
    assert services2.settings.label_dpi == 300
    assert services2.topology.id_sucursal == "LOCAL-A"
    assert services2.topology.pocketbase_url == "http://192.168.100.6:8090"
    assert services2.topology.pocketbase_token == "pb-token-1"


def test_configuracion_parcial_no_borra_el_resto(tmp_path) -> None:
    from app.settings import Settings

    db = str(tmp_path / "config2.db")
    services = build_services(settings=Settings(database_path=db))
    services.commands.execute(SaveSettingsCommand(store_name="VIRTUAL", currency="$"))

    settings2 = Settings(database_path=db)
    services2 = build_services(settings=settings2)
    assert services2.settings.store.name == "VIRTUAL"
    assert services2.settings.currency == "$"


def test_load_local_config_aplica_y_respeta_defaults(tmp_path) -> None:
    from sqlalchemy import text

    from app.infrastructure.db import create_engine_for, create_session_factory, init_database

    from app.settings import Settings

    db = str(tmp_path / "config3.db")
    engine = create_engine_for(db)
    init_database(engine)
    with create_session_factory(engine)() as session:
        settings = Settings(database_path=db, currency="EUR")
        load_local_config(settings, session)
        assert settings.currency == "EUR"

        session.execute(
            text("INSERT INTO sys_config (key, value, updated_at) VALUES ('currency', '$', CURRENT_TIMESTAMP)")
        )
        session.commit()
        load_local_config(settings, session)
        assert settings.currency == "$"

        session.execute(text("DELETE FROM sys_config"))
        session.commit()
        load_local_config(settings, session)
        assert settings.currency == "$"