"""Eventos de dominio.

Los agregados recolectan eventos durante una transacción; la Unit of Work los
despacha después de confirmar la persistencia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.value_objects import Money


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    occurred_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True, slots=True)
class ProductCreated(DomainEvent):
    product_id: int | None = None
    code: str = ""
    name: str = ""


@dataclass(frozen=True, slots=True)
class ProductUpdated(DomainEvent):
    product_id: int
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class ProductDeactivated(DomainEvent):
    product_id: int
    code: str


@dataclass(frozen=True, slots=True)
class StockAdjusted(DomainEvent):
    product_id: int
    code: str
    delta: int
    reason: str
    running_stock: int


@dataclass(frozen=True, slots=True)
class LowStockAlert(DomainEvent):
    product_id: int
    code: str
    name: str
    stock: int
    min_stock: int


@dataclass(frozen=True, slots=True)
class SaleCompleted(DomainEvent):
    sale_id: int
    receipt_number: str
    total: Money
    method: str


@dataclass(frozen=True, slots=True)
class SaleVoided(DomainEvent):
    sale_id: int
    receipt_number: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SaleItemRefunded(DomainEvent):
    sale_id: int
    receipt_number: str
    item_index: int
    product_id: int
    product_name: str
    quantity: int
    refund_amount: Money
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CashMovementRegistered(DomainEvent):
    movement_type: str
    amount: Money
    reason: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class CashDayOpened(DomainEvent):
    day_id: int
    opening_cash: Money
    opened_by: str = ""


@dataclass(frozen=True, slots=True)
class CashDayClosed(DomainEvent):
    day_id: int
    closing_cash: Money
    expected_cash: Money
    difference: Money
    difference_kind: str = ""


@dataclass(frozen=True, slots=True)
class ApartadoCreated(DomainEvent):
    apartado_id: int
    client_name: str
    total: Money


@dataclass(frozen=True, slots=True)
class ApartadoAbonado(DomainEvent):
    apartado_id: int
    method: str
    amount: Money
    balance: Money


@dataclass(frozen=True, slots=True)
class ApartadoSettled(DomainEvent):
    apartado_id: int
    client_name: str


@dataclass(frozen=True, slots=True)
class ApartadoCancelled(DomainEvent):
    apartado_id: int
    client_name: str
    reason: str = ""


class AggregateEvents:
    """Contenedor de eventos por agregado (mixin reutilizable)."""

    def __init__(self) -> None:
        self._events: list[DomainEvent] = []

    def record_event(self, event: DomainEvent) -> None:
        self._events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        events, self._events = self._events, []
        return events