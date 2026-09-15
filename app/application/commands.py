"""Comandos (escritura). Son objetos de datos inmutables dirigidos al bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Union

from app.application.bus import Command

#: Tipos aceptados para importes en la entrada (se normalizan a ``Money``).
InputAmount = Union[str, Decimal, int, float]


@dataclass(frozen=True, slots=True)
class CreateCategoryCommand(Command):
    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class CreateProductCommand(Command):
    code: str
    name: str
    unit_price: InputAmount
    category_id: int | None = None
    cost: InputAmount | None = None
    wholesale_price: InputAmount | None = None
    stock: int = 0
    min_stock: int = 0
    description: str = ""


@dataclass(frozen=True, slots=True)
class UpdateProductCommand(Command):
    product_id: int
    code: str
    name: str
    unit_price: InputAmount
    category_id: int | None = None
    cost: InputAmount | None = None
    wholesale_price: InputAmount | None = None
    min_stock: int = 0
    description: str = ""


@dataclass(frozen=True, slots=True)
class SetProductActiveCommand(Command):
    product_id: int
    active: bool


@dataclass(frozen=True, slots=True)
class AdjustStockCommand(Command):
    product_id: int
    delta: int
    reason: str = "AJUSTE"
    note: str = ""


@dataclass(frozen=True, slots=True)
class SaleItemRequest:
    code: str
    quantity: int


@dataclass(frozen=True, slots=True)
class SalePaymentRequest:
    method: str
    amount: InputAmount


@dataclass(frozen=True, slots=True)
class CompleteSaleCommand(Command):
    items: tuple[SaleItemRequest, ...] = field(default_factory=tuple)
    payments: tuple[SalePaymentRequest, ...] = field(default_factory=tuple)
    tendered: InputAmount | None = None
    discount: InputAmount | None = None

    def __post_init__(self) -> None:
        if isinstance(self.items, SaleItemRequest):
            object.__setattr__(self, "items", (self.items,))
        if isinstance(self.payments, SalePaymentRequest):
            object.__setattr__(self, "payments", (self.payments,))


@dataclass(frozen=True, slots=True)
class VoidSaleCommand(Command):
    sale_id: int
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RefundSaleItemCommand(Command):
    sale_id: int
    item_index: int
    quantity: int
    reason: str = ""
    cash_payout: bool = True


@dataclass(frozen=True, slots=True)
class RegisterCashMovementCommand(Command):
    movement_type: str
    amount: InputAmount
    reason: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class OpenCashDayCommand(Command):
    opening_cash: InputAmount = 0
    opened_by: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class CloseCashDayCommand(Command):
    counted_cash: InputAmount
    note: str = ""


@dataclass(frozen=True, slots=True)
class ApartadoItemRequest:
    code: str
    quantity: int


@dataclass(frozen=True, slots=True)
class CreateApartadoCommand(Command):
    client_name: str
    client_phone: str = ""
    items: tuple[ApartadoItemRequest, ...] = field(default_factory=tuple)
    initial_abono: InputAmount | None = None
    abono_method: str = "EFECTIVO"
    note: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.items, ApartadoItemRequest):
            object.__setattr__(self, "items", (self.items,))


@dataclass(frozen=True, slots=True)
class AddAbonoCommand(Command):
    apartado_id: int
    amount: InputAmount
    method: str = "EFECTIVO"


@dataclass(frozen=True, slots=True)
class CancelApartadoCommand(Command):
    apartado_id: int
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CreateProviderCommand(Command):
    name: str
    phone: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class UpdateProviderCommand(Command):
    provider_id: int
    name: str
    phone: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class DeleteProviderCommand(Command):
    provider_id: int


@dataclass(frozen=True, slots=True)
class ProviderItemRequest:
    code: str
    description: str
    price: InputAmount


@dataclass(frozen=True, slots=True)
class ImportProviderItemsCommand(Command):
    provider_id: int
    items: tuple[ProviderItemRequest, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if isinstance(self.items, ProviderItemRequest):
            object.__setattr__(self, "items", (self.items,))


@dataclass(frozen=True, slots=True)
class ReceiveLineRequest:
    """Renglón de una recepción de pedido: relaciona artículo ↔ producto y suma stock."""

    product_id: int
    quantity: int
    provider_item_id: int | None = None
    unit_price: InputAmount | None = None


@dataclass(frozen=True, slots=True)
class ReceivePurchaseOrderCommand(Command):
    note: str = ""
    lines: tuple[ReceiveLineRequest, ...] = field(default_factory=tuple)
    purchase_order_id: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.lines, ReceiveLineRequest):
            object.__setattr__(self, "lines", (self.lines,))


@dataclass(frozen=True, slots=True)
class PurchaseOrderLineRequest:
    """Renglón para guardar un pedido (precios congelados al guardar)."""

    code: str
    description: str
    provider_name: str
    quantity: int
    unit_price: InputAmount
    provider_item_id: int | None = None
    product_id: int | None = None


@dataclass(frozen=True, slots=True)
class ImportedOrderLineRequest:
    """Renglón leído de un PDF/Excel de pedido antes de convertirse en pedido nuevo.

    ``unit_price`` es el precio de COMPRA del renglón. ``sale_price`` (opcional)
    es el precio de VENTA que alimenta el ``unit_price`` del producto del
    catálogo creado/actualizado.
    """

    code: str
    description: str
    quantity: int
    unit_price: InputAmount
    sale_price: InputAmount | None = None


@dataclass(frozen=True, slots=True)
class LoadPurchaseOrderCommand(Command):
    """Carga un PDF/Excel como NUEVO pedido pendiente.

    Actualiza (o agrega) los artículos del proveedor, crea los productos que
    aún no existan en el inventario y guarda el pedido. El stock solo se afecta
    al recibirlo (ReceivePurchaseOrderCommand).
    """

    provider_name: str
    note: str = ""
    lines: tuple[ImportedOrderLineRequest, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if isinstance(self.lines, ImportedOrderLineRequest):
            object.__setattr__(self, "lines", (self.lines,))


@dataclass(frozen=True, slots=True)
class SavePurchaseOrderCommand(Command):
    """Persiste un pedido pendiente para recibirlo en otra fecha."""

    note: str = ""
    lines: tuple[PurchaseOrderLineRequest, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if isinstance(self.lines, PurchaseOrderLineRequest):
            object.__setattr__(self, "lines", (self.lines,))


@dataclass(frozen=True, slots=True)
class DeletePurchaseOrderCommand(Command):
    purchase_order_id: int


@dataclass(frozen=True, slots=True)
class SaveSettingsCommand(Command):
    """Persiste ajustes locales (local store, seguridad, etiquetas, nube).

    Los valores ``None`` se ignoran: sólo se actualizan los campos incluidos.
    """

    store_name: str | None = None
    store_address: str | None = None
    store_phone: str | None = None
    store_footer: str | None = None
    currency: str | None = None
    login_username: str | None = None
    login_password: str | None = None
    label_printer_kind: str | None = None
    brother_printer_ip: str | None = None
    label_width_mm: float | None = None
    label_height_mm: float | None = None
    label_dpi: int | None = None
    id_sucursal: str | None = None
    terminal_num: str | None = None
    supabase_url: str | None = None
    supabase_anon_key: str | None = None