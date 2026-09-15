"""Modelos de lectura (DTOs). La interfaz consume estos objetos, nunca entidades."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.domain.value_objects import Money
from app.domain.entities import PaymentMethod


@dataclass(slots=True)
class CategoryDTO:
    id: int
    name: str
    description: str = ""


@dataclass(slots=True)
class ProductDTO:
    id: int
    code: str
    name: str
    unit_price: Money
    stock: int
    min_stock: int
    active: bool
    category_id: int | None = None
    category_name: str = ""
    description: str = ""
    cost: Optional[Money] = None
    wholesale_price: Optional[Money] = None

    @property
    def low_stock(self) -> bool:
        return self.active and self.stock > 0 and self.stock <= self.min_stock

    @property
    def out_of_stock(self) -> bool:
        return self.active and self.stock == 0


@dataclass(slots=True)
class SaleItemDTO:
    product_id: int
    product_name: str
    quantity: int
    unit_price: Money
    subtotal: Money
    refunded_qty: int = 0

    @property
    def remaining_qty(self) -> int:
        return self.quantity - self.refunded_qty


@dataclass(slots=True)
class PaymentDTO:
    method: PaymentMethod
    amount: Money


@dataclass(slots=True)
class SaleDTO:
    id: int
    receipt_number: str
    created_at: datetime
    status: str
    items: list[SaleItemDTO] = field(default_factory=list)
    payments: list[PaymentDTO] = field(default_factory=list)
    subtotal: Money = field(default_factory=Money.zero)
    total: Money = field(default_factory=Money.zero)
    discount: Money = field(default_factory=Money.zero)
    tendered: Optional[Money] = None
    change_amount: Money = field(default_factory=Money.zero)
    void_reason: str = ""

    @property
    def item_count(self) -> int:
        return sum(i.quantity for i in self.items)

    @property
    def refunded_count(self) -> int:
        return sum(i.refunded_qty for i in self.items)

    @property
    def methods_label(self) -> str:
        return " / ".join(p.method.value for p in self.payments) or "-"


@dataclass(slots=True)
class StockMovementDTO:
    id: int
    delta: int
    reason: str
    note: str
    created_at: datetime
    document: str = ""


@dataclass(slots=True)
class TopProductDTO:
    name: str
    code: str
    quantity: int
    revenue: Money


@dataclass(slots=True)
class DashboardDTO:
    today_sales_count: int
    today_revenue: Money
    today_revenue_by_method: dict[str, Money]
    active_products: int
    low_stock_count: int
    out_of_stock_count: int
    top_products: list[TopProductDTO] = field(default_factory=list)


@dataclass(slots=True)
class CompleteSaleResult:
    sale_id: int
    receipt_number: str
    total: Money
    change_amount: Money
    sale: SaleDTO


@dataclass(slots=True)
class AdjustStockResult:
    product_id: int
    stock: int
    delta: int


@dataclass(slots=True)
class ApartadoItemDTO:
    product_id: int
    product_name: str
    quantity: int
    unit_price: Money
    subtotal: Money


@dataclass(slots=True)
class AbonoDTO:
    id: int
    method: PaymentMethod
    amount: Money
    created_at: datetime


@dataclass(slots=True)
class ApartadoDTO:
    id: int
    client_name: str
    client_phone: str
    created_at: datetime
    status: str
    items: list[ApartadoItemDTO] = field(default_factory=list)
    abonos: list[AbonoDTO] = field(default_factory=list)
    total: Money = field(default_factory=Money.zero)
    amount_paid: Money = field(default_factory=Money.zero)
    balance: Money = field(default_factory=Money.zero)
    note: str = ""

    @property
    def item_count(self) -> int:
        return sum(i.quantity for i in self.items)

    @property
    def abonos_count(self) -> int:
        return len(self.abonos)


@dataclass(slots=True)
class CashMovementDTO:
    id: int
    movement_type: str
    amount: Money
    reason: str
    note: str
    created_at: datetime


@dataclass(slots=True)
class CashDayDTO:
    id: int
    opened_at: datetime
    opening_cash: Money
    opened_by: str
    note: str
    status: str
    closed_at: datetime | None = None
    closing_cash: Money | None = None
    expected_cash: Money | None = None
    difference: Money | None = None
    difference_kind: str = ""


@dataclass(slots=True)
class CorteDTO:
    day: CashDayDTO
    sales_count: int
    sales_total: Money
    by_method: dict[str, Money]
    credit_total: Money
    cash_in_total: Money
    cash_out_total: Money
    refunds_total: Money
    expected_cash: Money


@dataclass(slots=True)
class ProviderDTO:
    id: int
    name: str
    phone: str = ""
    note: str = ""
    created_at: datetime | None = None
    item_count: int = 0


@dataclass(slots=True)
class ProviderItemDTO:
    id: int
    provider_id: int
    code: str
    description: str
    price: Money
    provider_name: str = ""
    product_id: int | None = None


@dataclass(slots=True)
class PurchaseLineDTO:
    """Renglón de una lista de compra manual (pedido a proveedor)."""

    code: str
    description: str
    provider_name: str
    quantity: int
    unit_price: Money
    subtotal: Money


@dataclass(slots=True)
class PurchaseOrderLineDTO:
    """Renglón de un pedido guardado (con validaciones para recepción)."""

    id: int
    code: str
    description: str
    provider_name: str
    quantity: int
    unit_price: Money
    provider_item_id: int | None = None
    product_id: int | None = None

    @property
    def subtotal(self) -> Money:
        return self.unit_price * self.quantity


@dataclass(slots=True)
class PurchaseOrderDTO:
    """Pedido persistido: se guarda pendiente y se recibe en otra fecha."""

    id: int
    order_number: str
    created_at: datetime
    status: str = "PENDIENTE"
    note: str = ""
    lines: list[PurchaseOrderLineDTO] = field(default_factory=list)

    @property
    def total(self) -> Money:
        return sum((line.subtotal for line in self.lines), Money.zero())

    @property
    def item_count(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def provider_names(self) -> list[str]:
        return sorted({line.provider_name for line in self.lines if line.provider_name})