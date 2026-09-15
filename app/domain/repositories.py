"""Contratos (ABC) de persistencia. La infraestructura los implementa."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable

from app.domain.entities import (
    Apartado,
    CashDay,
    CashMovement,
    Category,
    Product,
    Provider,
    ProviderItem,
    PurchaseOrder,
    Sale,
    StockMovement,
)


class CategoryRepository(ABC):
    @abstractmethod
    def add(self, category: Category) -> Category: ...

    @abstractmethod
    def update(self, category: Category) -> None: ...

    @abstractmethod
    def get(self, category_id: int) -> Category | None: ...

    @abstractmethod
    def list_all(self) -> list[Category]: ...


class ProductRepository(ABC):
    def add(self, product: Product) -> Product: ...

    @abstractmethod
    def update(self, product: Product) -> None: ...

    @abstractmethod
    def set_active(self, product_id: int, active: bool) -> None: ...

    @abstractmethod
    def update_stock(self, product_id: int, new_stock: int) -> None: ...

    @abstractmethod
    def get(self, product_id: int) -> Product | None: ...

    @abstractmethod
    def get_by_code(self, code: str) -> Product | None: ...

    @abstractmethod
    def search(
        self, *, search: str = "", include_inactive: bool = False, category_id: int | None = None
    ) -> list[Product]: ...

    @abstractmethod
    def list_extant_codes(self) -> Iterable[str]: ...


class SaleRepository(ABC):
    def add(self, sale: Sale) -> Sale: ...

    @abstractmethod
    def update(self, sale: Sale) -> None: ...

    @abstractmethod
    def get(self, sale_id: int) -> Sale | None: ...

    @abstractmethod
    def get_by_receipt(self, receipt_number: str) -> Sale | None: ...

    @abstractmethod
    def list_sales(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[Sale]: ...

    @abstractmethod
    def next_receipt_number(self) -> str: ...


class StockMovementRepository(ABC):
    @abstractmethod
    def add(self, movement: StockMovement) -> StockMovement: ...

    @abstractmethod
    def list_for_product(self, product_id: int, *, limit: int = 100) -> list[StockMovement]: ...


class ApartadoRepository(ABC):
    def add(self, apartado: Apartado) -> Apartado: ...

    @abstractmethod
    def update(self, apartado: Apartado) -> None: ...

    @abstractmethod
    def get(self, apartado_id: int) -> Apartado | None: ...

    @abstractmethod
    def list(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[Apartado]: ...


class CashMovementRepository(ABC):
    @abstractmethod
    def add(self, movement: CashMovement) -> CashMovement: ...

    @abstractmethod
    def list(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CashMovement]: ...


class CashDayRepository(ABC):
    @abstractmethod
    def add(self, day: CashDay) -> CashDay: ...

    @abstractmethod
    def update(self, day: CashDay) -> None: ...

    @abstractmethod
    def get(self, day_id: int) -> CashDay | None: ...

    @abstractmethod
    def get_open(self) -> CashDay | None: ...

    @abstractmethod
    def list_recent(self, *, limit: int = 20) -> list[CashDay]: ...


class ProviderRepository(ABC):
    @abstractmethod
    def add(self, provider: Provider) -> Provider: ...

    @abstractmethod
    def update(self, provider: Provider) -> None: ...

    @abstractmethod
    def delete(self, provider_id: int) -> None: ...

    @abstractmethod
    def get(self, provider_id: int) -> Provider | None: ...

    @abstractmethod
    def list_all(self) -> list[Provider]: ...

    @abstractmethod
    def count_items(self, provider_id: int) -> int: ...

    @abstractmethod
    def replace_items(self, provider_id: int, items: list[ProviderItem]) -> int: ...

    @abstractmethod
    def upsert_items(
        self, provider_id: int, items: Iterable[ProviderItem]
    ) -> dict[str, ProviderItem]: ...

    @abstractmethod
    def relate_item(self, provider_item_id: int, product_id: int | None) -> None: ...

    @abstractmethod
    def list_items(self, provider_id: int) -> list[ProviderItem]: ...

    @abstractmethod
    def list_all_items(self) -> list[ProviderItem]: ...


class PurchaseOrderRepository(ABC):
    @abstractmethod
    def add(self, order: PurchaseOrder) -> PurchaseOrder: ...

    @abstractmethod
    def update(self, order: PurchaseOrder) -> None: ...

    @abstractmethod
    def delete(self, purchase_order_id: int) -> None: ...

    @abstractmethod
    def get(self, purchase_order_id: int) -> PurchaseOrder | None: ...

    @abstractmethod
    def list(
        self, *, status: str | None = None, provider: str | None = None, limit: int | None = None
    ) -> list[PurchaseOrder]: ...

    @abstractmethod
    def next_order_number(self) -> str: ...