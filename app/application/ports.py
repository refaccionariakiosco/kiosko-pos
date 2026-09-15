"""Ports (contratos) que usa la capa de aplicación para persistir y despachar eventos."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from app.domain.entities import (
    AggregateEvents,
    Apartado,
    CashDay,
    CashMovement,
    Category,
    Product,
    Sale,
    StockMovement,
)
from app.domain.events import DomainEvent
from app.domain.repositories import (
    ApartadoRepository,
    CashDayRepository,
    CashMovementRepository,
    CategoryRepository,
    ProductRepository,
    ProviderRepository,
    PurchaseOrderRepository,
    SaleRepository,
    StockMovementRepository,
)


@runtime_checkable
class UnitOfWork(Protocol):
    """Transacción de escritura: agrupa repositorios y confirma de forma atómica."""

    categories: CategoryRepository
    products: ProductRepository
    sales: SaleRepository
    movements: StockMovementRepository
    apartados: ApartadoRepository
    cash_movements: CashMovementRepository
    cash_days: CashDayRepository
    providers: ProviderRepository
    purchase_orders: PurchaseOrderRepository

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None: ...
    def commit(self) -> None: ...
    def track(self, *aggregates: AggregateEvents) -> None: ...


class EventDispatcher(ABC):
    @abstractmethod
    def dispatch(self, events: list[DomainEvent]) -> None: ...


#: Factoría de unit of work (una por transacción de comando).
UowFactory = Callable[[], UnitOfWork]