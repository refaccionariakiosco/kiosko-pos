"""Fase 1 (offline-first): topología sucursal/terminal y separación catálogo vs inventario."""

from __future__ import annotations

import sqlalchemy as sa
import pytest
from sqlalchemy import select

from app.application.commands import CreateCategoryCommand, CreateProductCommand
from app.bootstrap import build_services
from app.infrastructure.db import apply_migrations, create_engine_for, create_session_factory, init_database
from app.infrastructure.importers.inventory import import_inventory_rows
from app.infrastructure.orm import InventoryRow, ProductRow, SysConfigRow
from app.infrastructure.topology import DEFAULT_BRANCH_ID, Topology
from app.settings import Settings


def _create_product(services, *, code="P-1", name="Producto", stock=10, min_stock=2):
    cat = services.commands.execute(CreateCategoryCommand(name="TEST"))
    return services.commands.execute(
        CreateProductCommand(
            code=code,
            name=name,
            unit_price="10",
            stock=stock,
            min_stock=min_stock,
            category_id=cat.id,
        )
    )


def test_topology_defaults_crean_identidad_local() -> None:
    services = build_services(settings=Settings(database_path=":memory:"))
    topo = services.topology
    assert topo is not None
    assert topo.id_sucursal == ""
    assert topo.id_terminal  # se genera automáticamente
    assert topo.effective_branch_id == DEFAULT_BRANCH_ID
    assert topo.is_cloud_configured is False


def test_topology_overrides_persisten_en_sys_config() -> None:
    topo = Topology(id_sucursal="SUC-001", id_terminal="CAJA-1", pocketbase_url="http://192.168.100.6:8090")
    services = build_services(settings=Settings(database_path=":memory:"), topology=topo)
    assert services.topology.id_sucursal == "SUC-001"
    assert services.topology.id_terminal == "CAJA-1"
    assert services.topology.is_cloud_configured is True


def test_inventario_es_por_sucursal_y_catalogo_global(tmp_path) -> None:
    db = str(tmp_path / "kiosco.db")
    topo_a = Topology(id_sucursal="SUC-A", id_terminal="T-A")
    services_a = build_services(settings=Settings(database_path=db), topology=topo_a)

    _create_product(services_a, code="GEN-1", stock=7, min_stock=1)

    # La sucursal B (misma base, otro id_sucursal) NO ve el stock de A.
    topo_b = Topology(id_sucursal="SUC-B", id_terminal="T-B")
    services_b = build_services(
        settings=Settings(database_path=db), topology=topo_b, initialize=False, seeds=False
    )
    from app.application.queries import GetCatalogQuery

    catalog_b = services_b.queries.ask(GetCatalogQuery())
    assert len(catalog_b) == 1
    assert catalog_b[0].code == "GEN-1"  # el catálogo es global...
    assert catalog_b[0].stock == 0  # ...pero el inventario es de la sucursal

    catalog_a = services_a.queries.ask(GetCatalogQuery())
    assert catalog_a[0].stock == 7


def test_stock_ajustado_no_se_filtra_entre_sucursales(tmp_path) -> None:
    db = str(tmp_path / "kiosco.db")
    services_a = build_services(
        settings=Settings(database_path=db), topology=Topology(id_sucursal="SUC-A", id_terminal="T-A")
    )
    product = _create_product(services_a, code="AJ-1", stock=10)

    from app.application.commands import AdjustStockCommand
    from app.application.queries import GetCatalogQuery

    services_a.commands.execute(AdjustStockCommand(product_id=product.id, delta=-4, reason="AJUSTE"))

    services_b = build_services(
        settings=Settings(database_path=db),
        topology=Topology(id_sucursal="SUC-B", id_terminal="T-B"),
        initialize=False,
        seeds=False,
    )
    assert services_a.queries.ask(GetCatalogQuery())[0].stock == 6
    assert services_b.queries.ask(GetCatalogQuery())[0].stock == 0


def test_migracion_vacia_mueve_stock_a_inventory(tmp_path) -> None:
    """Una base creada antes de la Fase 1 (stock en products) se migra a inventory."""
    db = str(tmp_path / "legacy.db")
    engine = create_engine_for(db)
    session_factory = create_session_factory(engine)

    # Simula base vieja: esquema completo pero inventario VACÍO y un producto legado.
    init_database(engine)
    with session_factory() as session:
        session.execute(sa.delete(InventoryRow))
        session.add(ProductRow(code="L-1", name="Legacy", unit_price=10, stock=5, min_stock=1, active=True))
        session.commit()

    apply_migrations(engine)

    with session_factory() as session:
        row = session.execute(
            select(InventoryRow).where(InventoryRow.branch_id == DEFAULT_BRANCH_ID)
        ).scalar_one()
        assert row.stock == 5
        assert row.min_stock == 1


def test_importador_escribe_inventory_por_sucursal(tmp_path) -> None:
    from app.infrastructure.importers.xlsx import InventoryRow as ImportRow

    engine = create_engine_for(str(tmp_path / "import.db"))
    result = import_inventory_rows(
        engine,
        [ImportRow(code="I-1", name="Importado", unit_price=5, stock=3, min_stock=1)],
    )
    assert result.products == 1

    with create_session_factory(engine)() as session:
        inv = session.execute(select(InventoryRow)).scalar_one()
        assert inv.branch_id == DEFAULT_BRANCH_ID
        assert inv.stock == 3
        # espejo legado en products se mantiene por compatibilidad
        prod = session.execute(select(ProductRow)).scalar_one()
        assert prod.stock == 3


def test_sys_config_expone_datos_de_sync(tmp_path) -> None:
    db = str(tmp_path / "kiosco.db")
    services = build_services(
        settings=Settings(database_path=db), topology=Topology(id_sucursal="SUC-X", id_terminal="T-X")
    )
    filters = services.topology.sync_filters()
    assert filters["id_sucursal"] == "SUC-X"
    assert filters["id_terminal"] == "T-X"

    from app.infrastructure.db import create_engine_for, create_session_factory

    engine = create_engine_for(db)
    with create_session_factory(engine)() as session:
        rows = session.execute(select(SysConfigRow)).all()
        keys = {r[0].key for r in rows}
        assert {"id_sucursal", "id_terminal"} <= keys