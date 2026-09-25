"""Motor de sincronización offline-first (v1: catálogo, inventario y ventas).

Dirección del flujo (misma sucursal, inventario COMPARTIDO, última escritura
gana):

- SUBIR (push): el terminal publica su catálogo local (categorías + productos),
  el inventario de su sucursal y todas sus ventas (con anulaciones y
  devoluciones). Los upserts usan claves naturales y son idempotentes.
- BAJAR (pull): el terminal recibe el catálogo global, el inventario SOLO de su
  sucursal y las ventas de sus cajas hermanas; las ventas nuevas se insertan y
  se REPLICAN localmente su efecto de stock en ``stock_movements`` y en el
  inventario (el cardex refleja el movimiento de la otra caja), con
  idempotencia garantizada por el número de recibo.

El hub es un servidor PocketBase self-hosted en la red local (LAN). Si el
servidor no está accesible (``check_connection()`` falla o hay un error de
red), la corrida se reporta como fallida pero las ventas permanecen en SQLite
y se reintentan en el siguiente ciclo sin interrumpir la interfaz del POS.

El ticket de cada caja lleva prefijo propio (``R-1-000004`` vs ``R-2-000004``),
así los folios nunca colisionan aunque dos cajas vendan sin conexión.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from threading import Lock

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.orm import (
    CategoryRow,
    InventoryRow,
    ProductRow,
    SaleItemRow,
    SalePaymentRow,
    SaleRow,
    StockMovementRow,
)
from app.infrastructure.sync.pocketbase_client import PocketBaseClient
from app.infrastructure.topology import Topology

log = logging.getLogger(__name__)

REASON_SALE = "VENTA"
REASON_VOID = "ANULACION"
REASON_REFUND = "DEVOLUCION"


@dataclass
class SyncReport:
    """Resumen de una corrida de sincronización."""

    ok: bool = False
    error: str = ""
    skipped_reason: str = ""
    pushed: dict[str, int] = field(default_factory=dict)
    pulled: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0


def _iso(value: datetime) -> str:
    return value.astimezone().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def _money(value: object) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


class SyncEngine:
    def __init__(self, session_factory: sessionmaker, topology: Topology, rest_client_factory=PocketBaseClient):
        self._session_factory = session_factory
        self._topology = topology
        self._client_factory = rest_client_factory or PocketBaseClient

    def run(self, direction: str = "both") -> SyncReport:
        if not self._topology.is_cloud_configured:
            return SyncReport(skipped_reason="Sin sucursal ni URL de PocketBase configuradas.", ok=True)

        started = time.perf_counter()
        report = SyncReport()
        client = self._client_factory(self._topology.pocketbase_url, self._topology.pocketbase_token)
        check_connection = getattr(client, "check_connection", None)
        if callable(check_connection):
            try:
                if not check_connection():
                    report.ok = False
                    report.error = (
                        f"Servidor PocketBase no accesible en "
                        f"{self._topology.pocketbase_url or getattr(client, 'base_url', '')}."
                    )
                    report.duration_ms = int((time.perf_counter() - started) * 1000)
                    return report
            except Exception as exc:  # noqa: BLE001 - nunca caducar por el chequeo
                report.ok = False
                report.error = f"check_connection: {type(exc).__name__}: {exc}"
                report.duration_ms = int((time.perf_counter() - started) * 1000)
                return report
        with self._session_factory() as session:
            try:
                if direction in ("both", "push"):
                    self._push_categories(session, client, report)
                    self._push_products(session, client, report)
                    self._push_inventory(session, client, report)
                    self._push_sales(session, client, report)
                if direction in ("both", "pull"):
                    self._pull_categories(session, client, report)
                    self._pull_products(session, client, report)
                    self._pull_inventory(session, client, report)
                    self._pull_sales(session, client, report)
                session.execute(
                    text(
                        "INSERT INTO sys_config (key, value, updated_at) VALUES ('last_sync_at', :now, CURRENT_TIMESTAMP) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
                    ),
                    {"now": _iso(datetime.now())},
                )
                session.commit()
                report.ok = True
            except Exception as exc:  # noqa: BLE001 - cualquier fallo revierte todo y se reporta
                session.rollback()
                log.exception("Sincronización falló y se revirtió.")
                report.error = f"{type(exc).__name__}: {exc}"
                report.ok = False
        report.duration_ms = int((time.perf_counter() - started) * 1000)
        return report

    # ------------------------------------------------------------------ PUSH

    def _push_categories(self, session: Session, client: PocketBaseClient, report: SyncReport) -> None:
        rows = session.execute(select(CategoryRow)).scalars().all()
        payload = [
            {"name": row.name, "description": row.description or "", "updated_at": _iso(datetime.now())}
            for row in rows
        ]
        client.post("pos_categories", payload, on_conflict="name")
        report.pushed["categories"] = len(payload)

    def _push_products(self, session: Session, client: PocketBaseClient, report: SyncReport) -> None:
        categories = {row.id: row.name for row in session.execute(select(CategoryRow)).scalars().all()}
        rows = session.execute(select(ProductRow)).scalars().all()
        payload = [
            {
                "code": row.code,
                "name": row.name,
                "description": row.description or "",
                "category_name": categories.get(row.category_id, "") or "",
                "unit_price": float(row.unit_price or 0),
                "cost": None if row.cost is None else float(row.cost),
                "wholesale_price": None if row.wholesale_price is None else float(row.wholesale_price),
                "active": bool(row.active),
                "updated_at": _iso(row.updated_at),
            }
            for row in rows
        ]
        client.post("pos_products", payload, on_conflict="code")
        report.pushed["products"] = len(payload)

    def _push_inventory(self, session: Session, client: PocketBaseClient, report: SyncReport) -> None:
        branch = self._topology.id_sucursal
        code_by_id = {
            row.id: row.code
            for row in session.execute(select(ProductRow.id, ProductRow.code)).all()
        }
        rows = session.execute(select(InventoryRow).where(InventoryRow.branch_id == branch)).scalars().all()
        payload = [
            {
                "product_code": code_by_id.get(row.product_id, ""),
                "branch_id": row.branch_id,
                "stock": int(row.stock),
                "min_stock": int(row.min_stock),
                "updated_at": _iso(row.updated_at),
            }
            for row in rows
        ]
        client.post("pos_inventory", payload, on_conflict="product_code,branch_id")
        report.pushed["inventory"] = len(payload)

    def _push_sales(self, session: Session, client: PocketBaseClient, report: SyncReport) -> None:
        code_by_id = {
            row.id: row.code
            for row in session.execute(select(ProductRow.id, ProductRow.code)).all()
        }
        sales = session.execute(select(SaleRow)).scalars().all()
        sale_payload = []

        items_payload: list[dict] = []
        payments_payload: list[dict] = []
        for sale in sales:
            items = sorted(sale.items, key=lambda it: it.id if it.id is not None else 0)
            payments = sorted(sale.payments, key=lambda it: it.id if it.id is not None else 0)
            sale_payload.append(
                {
                    "receipt_number": sale.receipt_number,
                    "status": sale.status,
                    "created_at": _iso(sale.created_at),
                    "tendered": None if sale.tendered is None else float(sale.tendered),
                    "subtotal": float(sale.subtotal or 0),
                    "discount": float(sale.discount or 0),
                    "void_reason": sale.void_reason or "",
                    "voided_at": None if sale.voided_at is None else _iso(sale.voided_at),
                    "branch_id": self._topology.id_sucursal,
                    "terminal_id": self._topology.id_terminal,
                    "terminal_num": self._topology.receipt_prefix,
                }
            )
            for seq, item in enumerate(items):
                items_payload.append(
                    {
                        "receipt_number": sale.receipt_number,
                        "seq": seq,
                        "product_code": code_by_id.get(item.product_id, ""),
                        "product_name": item.product_name,
                        "quantity": int(item.quantity),
                        "unit_price": float(item.unit_price or 0),
                        "subtotal": float(item.subtotal or (item.quantity * item.unit_price)),
                        "refunded_qty": int(item.refunded_qty or 0),
                    }
                )
            for seq, payment in enumerate(payments):
                payments_payload.append(
                    {
                        "receipt_number": sale.receipt_number,
                        "seq": seq,
                        "method": payment.method,
                        "amount": float(payment.amount or 0),
                    }
                )

        client.post("pos_sales", sale_payload, on_conflict="receipt_number")
        client.post("pos_sale_items", items_payload, on_conflict="receipt_number,seq")
        client.post("pos_sale_payments", payments_payload, on_conflict="receipt_number,seq")
        report.pushed["sales"] = len(sale_payload)
        report.pushed["sale_items"] = len(items_payload)

    # ------------------------------------------------------------------ PULL

    def _pull_categories(self, session: Session, client: PocketBaseClient, report: SyncReport) -> None:
        existing = {row.name for row in session.execute(select(CategoryRow)).scalars().all()}
        added = 0
        for remote in client.fetch("pos_categories"):
            name = (remote.get("name") or "").strip()
            if not name or name in existing:
                continue
            session.add(CategoryRow(name=name, description=remote.get("description") or ""))
            existing.add(name)
            added += 1
        session.flush()
        report.pulled["categories"] = added

    def _pull_products(self, session: Session, client: PocketBaseClient, report: SyncReport) -> None:
        session.flush()  # materializa categorías creadas en _pull_categories
        existing = {row.code: row for row in session.execute(select(ProductRow)).scalars().all()}
        cat_rows = {row.name: row for row in session.execute(select(CategoryRow)).scalars().all()}
        created = 0
        updated = 0
        for remote in client.fetch("pos_products"):
            code = (remote.get("code") or "").strip()
            if not code:
                continue
            local = existing.get(code)
            remote_dt = _parse_iso(remote.get("updated_at"))
            if local is not None and not (remote_dt and (local.updated_at is None or remote_dt > local.updated_at)):
                continue  # el local es más reciente o igual: última escritura gana
            category_name = (remote.get("category_name") or "").strip()
            category = cat_rows.get(category_name) if category_name else None
            if category_name and category is None:
                category = CategoryRow(name=category_name)
                session.add(category)
                session.flush()
                cat_rows[category_name] = category
            if local is None:
                local = ProductRow(code=code)
                session.add(local)
                existing[code] = local
                created += 1
            else:
                updated += 1
            local.name = (remote.get("name") or "").strip() or local.name
            local.description = remote.get("description") or ""
            local.category_id = category.id if category is not None else None
            local.unit_price = _money(remote.get("unit_price"))
            local.cost = _money(remote.get("cost")) if remote.get("cost") is not None else None
            local.wholesale_price = _money(remote.get("wholesale_price")) if remote.get("wholesale_price") is not None else None
            local.active = bool(remote.get("active", True))
            if remote_dt is not None:
                local.updated_at = remote_dt
        session.flush()  # expone los productos nuevos a las siguientes etapas
        report.pulled["products"] = created
        report.pulled["products_updated"] = updated

    def _pull_inventory(self, session: Session, client: PocketBaseClient, report: SyncReport) -> None:
        branch = self._topology.id_sucursal
        products = {row.code: row for row in session.execute(select(ProductRow)).scalars().all()}
        inv_by_product: dict[int, InventoryRow] = {
            row.product_id: row
            for row in session.execute(select(InventoryRow).where(InventoryRow.branch_id == branch)).scalars().all()
        }
        created = 0
        updated = 0
        for remote in client.fetch("pos_inventory", filters={"branch_id": branch}):
            product = products.get(remote.get("product_code") or "")
            if product is None:
                continue
            local = inv_by_product.get(product.id)
            remote_dt = _parse_iso(remote.get("updated_at"))
            if local is not None and not (remote_dt and (local.updated_at is None or remote_dt > local.updated_at)):
                product.stock = local.stock  # espejo legado
                continue
            if local is None:
                local = InventoryRow(product_id=product.id, branch_id=branch)
                session.add(local)
                inv_by_product[product.id] = local
                created += 1
            else:
                updated += 1
            local.stock = int(remote.get("stock", 0))
            local.min_stock = int(remote.get("min_stock", 0))
            if remote_dt is not None:
                local.updated_at = remote_dt
            product.stock = local.stock
            product.min_stock = local.min_stock
        session.flush()  # expone el inventario a la fase de ventas
        report.pulled["inventory"] = created
        report.pulled["inventory_updated"] = updated

    def _pull_sales(self, session: Session, client: PocketBaseClient, report: SyncReport) -> None:
        branch = self._topology.id_sucursal
        remote_sales = client.fetch("pos_sales", filters={"branch_id": branch}, order="created_at.asc")
        items_by_receipt: dict[str, list[dict]] = {}
        for item in client.fetch("pos_sale_items"):
            items_by_receipt.setdefault(item.get("receipt_number") or "", []).append(item)
        payments_by_receipt: dict[str, list[dict]] = {}
        for payment in client.fetch("pos_sale_payments"):
            payments_by_receipt.setdefault(payment.get("receipt_number") or "", []).append(payment)

        existing = set(session.execute(select(SaleRow.receipt_number)).scalars().all())
        products = {row.code: row for row in session.execute(select(ProductRow)).scalars().all()}
        inv_by_product: dict[int, InventoryRow] = {
            row.product_id: row
            for row in session.execute(select(InventoryRow).where(InventoryRow.branch_id == branch)).scalars().all()
        }

        added = 0
        for sale_row in remote_sales:
            receipt = (sale_row.get("receipt_number") or "").strip()
            if not receipt or receipt in existing:
                continue
            created_at = _parse_iso(sale_row.get("created_at")) or datetime.now()
            status = (sale_row.get("status") or "COMPLETADA").strip() or "COMPLETADA"
            sale = SaleRow(
                receipt_number=receipt,
                status=status,
                created_at=created_at,
                tendered=_money(sale_row.get("tendered")) if sale_row.get("tendered") is not None else None,
                subtotal=_money(sale_row.get("subtotal")),
                discount=_money(sale_row.get("discount")),
                void_reason=(sale_row.get("void_reason") or "").strip(),
                voided_at=_parse_iso(sale_row.get("voided_at")),
            )
            session.add(sale)
            session.flush()
            existing.add(receipt)

            for item in items_by_receipt.get(receipt, []):
                product = products.get(item.get("product_code") or "")
                product_id = product.id if product is not None else 0
                quantity = int(item.get("quantity", 0))
                refunded = int(item.get("refunded_qty", 0))
                session.add(
                    SaleItemRow(
                        sale_id=sale.id,
                        product_id=product_id,
                        product_name=(item.get("product_name") or "").strip(),
                        quantity=quantity,
                        unit_price=_money(item.get("unit_price")),
                        subtotal=_money(item.get("subtotal")) or (_money(item.get("unit_price")) * quantity),
                        refunded_qty=refunded,
                    )
                )
                self._replay_stock(
                    session,
                    branch,
                    product,
                    inv_by_product,
                    quantity,
                    refunded,
                    status,
                    receipt,
                    created_at,
                    sale.voided_at,
                    (item.get("product_name") or "").strip(),
                )
            for payment in payments_by_receipt.get(receipt, []):
                session.add(
                    SalePaymentRow(
                        sale_id=sale.id,
                        method=(payment.get("method") or "EFECTIVO").strip() or "EFECTIVO",
                        amount=_money(payment.get("amount")),
                    )
                )
            added += 1
        report.pulled["sales"] = added

    @staticmethod
    def _replay_stock(
        session: Session,
        branch: str,
        product: ProductRow | None,
        inv_by_product: dict[int, InventoryRow],
        quantity: int,
        refunded: int,
        status: str,
        document: str,
        created_at: datetime,
        voided_at: datetime | None,
        product_name: str,
    ) -> None:
        """Replica en esta terminal el efecto de una venta ocurrida en otra caja.

        Idempotente: sólo se invoca para recibos que no existían localmente.
        El efecto es exactamente el que originó la caja remota:
        VENTA -qty; anulación completa +qty; devolución parcial +refunded.
        """
        if product is None:
            return
        inv = inv_by_product.get(product.id)
        if inv is None:
            inv = session.execute(
                select(InventoryRow).where(InventoryRow.product_id == product.id, InventoryRow.branch_id == branch)
            ).scalar_one_or_none()
            if inv is None:
                inv = InventoryRow(product_id=product.id, branch_id=branch, stock=0, min_stock=0, updated_at=datetime.now())
                session.add(inv)
            inv_by_product[product.id] = inv

        inv.stock -= quantity
        session.add(
            StockMovementRow(
                product_id=product.id,
                delta=-quantity,
                reason=REASON_SALE,
                note=f"Venta {document}",
                document=document,
                created_at=created_at,
            )
        )
        if status == "ANULADA":
            inv.stock += quantity
            session.add(
                StockMovementRow(
                    product_id=product.id,
                    delta=quantity,
                    reason=REASON_VOID,
                    note=f"Anulación {document}",
                    document=document,
                    created_at=voided_at or created_at,
                )
            )
        elif refunded:
            inv.stock += refunded
            session.add(
                StockMovementRow(
                    product_id=product.id,
                    delta=refunded,
                    reason=REASON_REFUND,
                    note=f"Devolución {document}: {product_name}",
                    document=document,
                    created_at=created_at,
                )
            )
        product.stock = inv.stock


_SYNC_LOCK = Lock()

def run_sync(session_factory: sessionmaker, topology: Topology, direction: str = "both") -> SyncReport:
    """Ejecuta una sincronización (helper para la UI): ``both``, ``push`` o ``pull``.

    Un candado global serializa las corridas: coexisten los syncs periódicos
    (5 min), el pull/manual de la UI y los que dispara el coordinador Realtime
    sin pisarse entre sí (evita "database is locked" en SQLite).
    """
    with _SYNC_LOCK:
        return SyncEngine(session_factory, topology).run(direction=direction)