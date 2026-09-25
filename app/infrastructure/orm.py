"""Modelos de tablas (SQLAlchemy ORM).

Los repositorios traducen entre estas filas y las entidades del dominio.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db import Base

GLOBAL_METADATA = Base.metadata


class SysConfigRow(Base):
    """Configuración clave/valor local: identidad de topología y opciones de sync.

    Guarda ``id_sucursal``, ``id_terminal``, ``pocketbase_url`` y el token de
    acceso del servidor PocketBase (LAN).
    """

    __tablename__ = "sys_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class InventoryRow(Base):
    """Existencias físicas, SIEMPRE vinculadas a una sucursal (stock físico).

    El catálogo (``products``) es global; el inventario es local a la sucursal
    ``branch_id``. Una sucursal nunca ve ni mezcla las existencias de otra.
    """

    __tablename__ = "inventory"
    __table_args__ = (UniqueConstraint("product_id", "branch_id", name="uq_inventory_product_branch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    branch_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    product: Mapped["ProductRow"] = relationship(back_populates="inventory")


class CategoryRow(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    products: Mapped[list["ProductRow"]] = relationship(back_populates="category")


class ProductRow(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True, index=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    wholesale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    category: Mapped[CategoryRow | None] = relationship(back_populates="products")
    inventory: Mapped[list["InventoryRow"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class SaleRow(Base):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="COMPLETADA")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)
    tendered: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    void_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["SaleItemRow"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan", order_by="SaleItemRow.id"
    )
    payments: Mapped[list["SalePaymentRow"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan", order_by="SalePaymentRow.id"
    )


class SaleItemRow(Base):
    __tablename__ = "sale_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    refunded_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    sale: Mapped[SaleRow] = relationship(back_populates="items")


class SalePaymentRow(Base):
    __tablename__ = "sale_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    sale: Mapped[SaleRow] = relationship(back_populates="payments")


class StockMovementRow(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    document: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)


class ApartadoRow(Base):
    __tablename__ = "apartados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    client_phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVO", index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)

    items: Mapped[list["ApartadoItemRow"]] = relationship(
        back_populates="apartado", cascade="all, delete-orphan", order_by="ApartadoItemRow.id"
    )
    abonos: Mapped[list["ApartadoAbonoRow"]] = relationship(
        back_populates="apartado", cascade="all, delete-orphan", order_by="ApartadoAbonoRow.id"
    )


class ApartadoItemRow(Base):
    __tablename__ = "apartado_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apartado_id: Mapped[int] = mapped_column(ForeignKey("apartados.id"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    apartado: Mapped[ApartadoRow] = relationship(back_populates="items")


class ApartadoAbonoRow(Base):
    __tablename__ = "apartado_abonos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    apartado_id: Mapped[int] = mapped_column(ForeignKey("apartados.id"), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    apartado: Mapped[ApartadoRow] = relationship(back_populates="abonos")


class CashMovementRow(Base):
    __tablename__ = "cash_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    movement_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)


class CashDayRow(Base):
    __tablename__ = "cash_days"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)
    opened_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    opening_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closing_cash: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    expected_cash: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    difference: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ABIERTA", index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ProviderRow(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)

    items: Mapped[list["ProviderItemRow"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan", order_by="ProviderItemRow.code"
    )


class ProviderItemRow(Base):
    __tablename__ = "provider_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    provider: Mapped[ProviderRow] = relationship(back_populates="items")


class PurchaseOrderRow(Base):
    """Pedido a proveedor persistido: se guarda pendiente y se recibe luego.

    Los precios y descripciones quedan congelados al momento de guardar el
    pedido, independientemente de las listas del proveedor.
    """

    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDIENTE", index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)

    lines: Mapped[list["PurchaseOrderLineRow"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="PurchaseOrderLineRow.id"
    )


class PurchaseOrderLineRow(Base):
    __tablename__ = "purchase_order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False, index=True)
    provider_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[PurchaseOrderRow] = relationship(back_populates="lines")