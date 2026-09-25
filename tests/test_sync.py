"""Tests del motor de sincronización (catálogo + inventario + ventas) sin red.

Usan un cliente REST simulado en memoria para validar el push/pull, la
replicación del stock (cardex) y la idempotencia de una segunda corrida.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from app.application.commands import (
    CompleteSaleCommand,
    CreateCategoryCommand,
    CreateProductCommand,
    SaleItemRequest,
    SalePaymentRequest,
)
from app.infrastructure.orm import (
    InventoryRow,
    ProductRow,
    SaleItemRow,
    SaleRow,
    StockMovementRow,
)
from app.infrastructure.sync.engine import SyncEngine
from app.infrastructure.topology import Topology
from app.settings import Settings

CLOUD = {
    "id_sucursal": "SUC-1",
    "pocketbase_url": "http://192.168.100.6:8090",
    "pocketbase_token": "pb-token-1",
}

REMOTE_TS = "2026-09-12T10:00:00+00:00"


class FakeClient:
    """Cliente REST en memoria: upsert por claves naturales + fetch con filtros."""

    def __init__(self, data=None):
        self.data = {key: list(value) for key, value in (data or {}).items()}

    def fetch(self, table, filters=None, order=None, limit=1000):
        rows = self.data.get(table, [])
        if filters:
            for name, value in filters.items():
                rows = [row for row in rows if row.get(name) == value]
        if order:
            column = order.split(".")[0]
            rows = sorted(rows, key=lambda row: row.get(column) or "")
        return list(rows)

    def post(self, table, rows, prefer="resolution=merge-duplicates", on_conflict=None):
        bucket = self.data.setdefault(table, [])
        for row in rows:
            key = self._key(table, row)
            bucket[:] = [old for old in bucket if self._key(table, old) != key]
            bucket.append(dict(row))
        return []

    @staticmethod
    def _key(table, row):
        if table == "pos_categories":
            return row.get("name")
        if table == "pos_products":
            return row.get("code")
        if table == "pos_inventory":
            return (row.get("product_code"), row.get("branch_id"))
        if table == "pos_sales":
            return row.get("receipt_number")
        if table in ("pos_sale_items", "pos_sale_payments"):
            return (row.get("receipt_number"), row.get("seq"))
        return row.get("id")


def make_services(db_path, terminal_num="1"):
    from app.bootstrap import build_services

    return build_services(
        settings=Settings(database_path=db_path),
        topology=Topology(terminal_num=terminal_num, **CLOUD),
    )


def run(services, fake):
    return SyncEngine(services.session_factory, services.topology, lambda *_: fake).run()


def base_remote_data() -> dict:
    return {
        "pos_categories": [{"name": "BEBIDAS", "description": "", "updated_at": REMOTE_TS}],
        "pos_products": [
            {
                "code": "7501055302082",
                "name": "Agua 500ml",
                "description": "",
                "category_name": "BEBIDAS",
                "unit_price": 20,
                "cost": 10,
                "wholesale_price": None,
                "active": True,
                "updated_at": REMOTE_TS,
            }
        ],
        "pos_inventory": [
            {
                "product_code": "7501055302082",
                "branch_id": "SUC-1",
                "stock": 10,
                "min_stock": 2,
                "updated_at": REMOTE_TS,
            }
        ],
        "pos_sales": [],
        "pos_sale_items": [],
        "pos_sale_payments": [],
    }


def remote_sale(receipt, receipt_ts, status="COMPLETADA", items=(), payments=(), void_reason=""):
    subtotal = sum(qty * price for _, _, qty, price, _refunded in items)
    return {
        "receipt_number": receipt,
        "status": status,
        "created_at": receipt_ts,
        "tendered": subtotal,
        "subtotal": subtotal,
        "discount": 0,
        "void_reason": void_reason,
        "voided_at": None,
        "branch_id": "SUC-1",
        "terminal_id": "T-OTHER",
        "terminal_num": "2",
    }


def remote_item(receipt, seq, code, name, qty, price, refunded=0):
    return {
        "receipt_number": receipt,
        "seq": seq,
        "product_code": code,
        "product_name": name,
        "quantity": qty,
        "unit_price": price,
        "subtotal": qty * price,
        "refunded_qty": refunded,
    }


def remote_payment(receipt, seq, method, amount):
    return {"receipt_number": receipt, "seq": seq, "method": method, "amount": amount}


# ---------------------------------------------------------------------------


def test_push_publica_catalogo_inventario_y_ventas(tmp_path):
    services = make_services(tmp_path / "a.db")
    category = services.commands.execute(CreateCategoryCommand(name="BEBIDAS", description=""))
    services.commands.execute(
        CreateProductCommand(
            code="7501055302082",
            name="Agua 500ml",
            unit_price="20",
            stock=5,
            category_id=category.id,
            cost="10",
        )
    )
    services.commands.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code="7501055302082", quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40"),),
            tendered="50",
        )
    )

    fake = FakeClient()
    report = run(services, fake)
    assert report.ok

    assert any(row["name"] == "BEBIDAS" for row in fake.data["pos_categories"])
    products = fake.data["pos_products"]
    assert any(row["code"] == "7501055302082" and row["category_name"] == "BEBIDAS" for row in products)

    inventory = fake.data["pos_inventory"]
    assert any(
        row["product_code"] == "7501055302082"
        and row["branch_id"] == "SUC-1"
        and row["stock"] == 3
        and row["min_stock"] == 0
        for row in inventory
    )

    sales = fake.data["pos_sales"]
    assert len(sales) == 1
    assert sales[0]["receipt_number"] == "R-1-000001"
    assert sales[0]["branch_id"] == "SUC-1"
    assert sales[0]["terminal_num"] == "1"
    assert ("R-1-000001", 0) == (fake.data["pos_sale_items"][0]["receipt_number"], fake.data["pos_sale_items"][0]["seq"])
    assert fake.data["pos_sale_items"][0]["product_code"] == "7501055302082"
    assert fake.data["pos_sale_payments"][0]["method"] == "EFECTIVO"


def test_prefijo_de_recibo_por_caja(tmp_path):
    local = make_services(tmp_path / "box1.db", terminal_num="1")
    other = make_services(tmp_path / "box2.db", terminal_num="2")
    for services in (local, other):
        services.commands.execute(
            CreateProductCommand(code="7501055302082", name="Agua", unit_price="20", stock=5, cost="10")
        )
        result = services.commands.execute(
            CompleteSaleCommand(
                items=(SaleItemRequest(code="7501055302082", quantity=1),),
                payments=(SalePaymentRequest(method="EFECTIVO", amount="20"),),
            )
        )
    assert result.receipt_number == "R-2-000001"
    with local.session_factory() as session:
        receipt = session.execute(
            select(SaleRow.receipt_number).order_by(SaleRow.id.desc()).limit(1)
        ).scalar_one()
    assert receipt == "R-1-000001"


def test_pull_replica_venta_remota_e_idempotente(tmp_path):
    data = base_remote_data()
    data["pos_sales"] = [remote_sale("R-2-000001", "2026-09-12T11:00:00+00:00")]
    data["pos_sale_items"] = [remote_item("R-2-000001", 0, "7501055302082", "Agua 500ml", 2, 20)]
    data["pos_sale_payments"] = [remote_payment("R-2-000001", 0, "EFECTIVO", 40)]
    fake = FakeClient(data)

    services = make_services(tmp_path / "b.db")
    for _ in range(2):  # segunda corrida: no debe duplicar nada
        report = run(services, fake)
        assert report.ok

    with services.session_factory() as session:
        product = session.execute(select(ProductRow)).scalar_one()
        assert product.name == "Agua 500ml"
        inventory = session.execute(select(InventoryRow)).scalar_one()
        assert inventory.stock == 8  # 10 - 2 (venta de la otra caja)

        sales = session.execute(select(SaleRow)).scalars().all()
        assert [sale.receipt_number for sale in sales] == ["R-2-000001"]
        assert len(session.execute(select(SaleItemRow)).scalars().all()) == 1

        movements = session.execute(select(StockMovementRow)).scalars().all()
        assert len(movements) == 1  # sólo VENTA -2, una vez
        assert movements[0].delta == -2
        assert movements[0].document == "R-2-000001"


def test_pull_anulacion_y_devolucion_replican_stock(tmp_path):
    data = base_remote_data()
    data["pos_sales"] = [
        remote_sale("R-2-000001", "2026-09-12T11:00:00+00:00"),
        remote_sale("R-2-000002", "2026-09-12T12:00:00+00:00", status="ANULADA", void_reason="Error de caja"),
        remote_sale("R-2-000003", "2026-09-12T13:00:00+00:00"),
    ]
    data["pos_sale_items"] = [
        remote_item("R-2-000001", 0, "7501055302082", "Agua 500ml", 2, 20),
        remote_item("R-2-000002", 0, "7501055302082", "Agua 500ml", 3, 20),
        remote_item("R-2-000003", 0, "7501055302082", "Agua 500ml", 1, 20, refunded=1),
    ]
    data["pos_sale_payments"] = [
        remote_payment("R-2-000001", 0, "EFECTIVO", 40),
        remote_payment("R-2-000002", 0, "EFECTIVO", 60),
        remote_payment("R-2-000003", 0, "EFECTIVO", 20),
    ]
    fake = FakeClient(data)

    services = make_services(tmp_path / "c.db")
    report = run(services, fake)
    assert report.ok
    assert report.pulled["sales"] == 3

    with services.session_factory() as session:
        inventory = session.execute(select(InventoryRow)).scalar_one()
        assert inventory.stock == 8  # -2 (vendidos netos)

        movements = session.execute(select(StockMovementRow)).scalars().all()
        assert len(movements) == 5  # venta + (venta,anulación) + (venta,devolución)
        assert sum(movement.delta for movement in movements) == -2
        assert {movement.document for movement in movements} == {
            "R-2-000001",
            "R-2-000002",
            "R-2-000003",
        }
        reasons = {movement.reason for movement in movements}
        assert reasons == {"VENTA", "ANULACION", "DEVOLUCION"}
        for receipt in ("R-2-000001", "R-2-000002", "R-2-000003"):
            status = session.execute(
                select(SaleRow.status).where(SaleRow.receipt_number == receipt)
            ).scalar_one()
            assert status == ("ANULADA" if receipt == "R-2-000002" else "COMPLETADA")


def test_sin_servidor_omite_sincronizacion(services):
    from sqlalchemy import text

    services.session_factory  # noqa: B018 - fixture disponible
    report = services.run_sync()
    assert report.ok
    assert report.skipped_reason
    with services.session_factory() as session:
        last_sync = session.execute(text("SELECT value FROM sys_config WHERE key = 'last_sync_at'")).scalar_one_or_none()
    assert last_sync is None