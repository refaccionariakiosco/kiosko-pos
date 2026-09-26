"""Unit of Work sobre SQLAlchemy: transacciones atómicas + despacho de eventos."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.application.ports import UnitOfWork
from app.domain.entities import AggregateEvents
from app.domain.events import DomainEvent
from app.infrastructure.repositories import (
    SqlAlchemyApartadoRepository,
    SqlAlchemyCashDayRepository,
    SqlAlchemyCashMovementRepository,
    SqlAlchemyCategoryRepository,
    SqlAlchemyProductRepository,
    SqlAlchemyProviderRepository,
    SqlAlchemyPurchaseOrderRepository,
    SqlAlchemySaleRepository,
    SqlAlchemyStockMovementRepository,
    SqlAlchemyValeRepository,
)
from app.infrastructure.topology import DEFAULT_BRANCH_ID

log = logging.getLogger(__name__)

EventDispatcherCallable = Callable[[list[DomainEvent]], None]


@dataclass
class SqlAlchemyUnitOfWork(UnitOfWork):
    """Abre una sesión, expone los repositorios y confirma (o revierte) la transacción.

    Los eventos de dominio recolectados por los agregados se despachan después
    del ``commit`` exitoso; si la transacción falla, se descartan.
    """

    session_factory: sessionmaker
    dispatcher: EventDispatcherCallable | None = None

    #: Identidad de la instalación (topología). El repositorio de productos usa
    #: ``branch_id`` para leer/escribir el inventario de ESTA sucursal.
    branch_id: str = ""
    terminal_id: str = ""

    session: Session | None = None
    categories: SqlAlchemyCategoryRepository | None = None
    products: SqlAlchemyProductRepository | None = None
    sales: SqlAlchemySaleRepository | None = None
    movements: SqlAlchemyStockMovementRepository | None = None
    apartados: SqlAlchemyApartadoRepository | None = None
    cash_movements: SqlAlchemyCashMovementRepository | None = None
    cash_days: SqlAlchemyCashDayRepository | None = None
    providers: SqlAlchemyProviderRepository | None = None
    purchase_orders: SqlAlchemyPurchaseOrderRepository | None = None
    vales: SqlAlchemyValeRepository | None = None
    _tracked: list[AggregateEvents] | None = None

    def __enter__(self) -> UnitOfWork:
        self.session = self.session_factory()
        self.categories = SqlAlchemyCategoryRepository(self.session)
        self.products = SqlAlchemyProductRepository(
            self.session, branch_id=self.branch_id or DEFAULT_BRANCH_ID
        )
        self.sales = SqlAlchemySaleRepository(self.session)
        self.movements = SqlAlchemyStockMovementRepository(self.session)
        self.apartados = SqlAlchemyApartadoRepository(self.session)
        self.cash_movements = SqlAlchemyCashMovementRepository(self.session)
        self.cash_days = SqlAlchemyCashDayRepository(self.session)
        self.providers = SqlAlchemyProviderRepository(self.session)
        self.purchase_orders = SqlAlchemyPurchaseOrderRepository(self.session)
        # Un vale sólo se redime en la sucursal que lo emitió.
        self.vales = SqlAlchemyValeRepository(
            self.session, branch_id=self.branch_id or DEFAULT_BRANCH_ID
        )
        self._tracked = []
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self.session is None:
            return
        try:
            if exc_type is not None:
                self.session.rollback()
        finally:
            self.session.close()
            self.session = None

    def track(self, *aggregates: AggregateEvents) -> None:
        if self._tracked is None:
            self._tracked = []
        for agg in aggregates:
            if agg not in self._tracked:
                self._tracked.append(agg)

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("UoW sin sesión activa.")
        self.session.flush()
        self.session.commit()
        self._dispatch_events()

    def _dispatch_events(self) -> None:
        if self.dispatcher is None:
            return
        events: list[DomainEvent] = []
        for agg in self._tracked or ():
            events.extend(agg.pull_events())
        if events:
            try:
                self.dispatcher(events)
            except Exception:  # los side-effects post-commit no deben romper la transacción
                log.exception("Fallo el despacho de eventos de dominio.")