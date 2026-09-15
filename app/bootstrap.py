"""Composition root: arma los buses, repositorios, UoW y casos de uso sin Qt."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.application import (
    AddAbonoCommand,
    AddAbonoHandler,
    AdjustStockCommand,
    AdjustStockHandler,
    CancelApartadoCommand,
    CancelApartadoHandler,
    CloseCashDayCommand,
    CloseCashDayHandler,
    CommandBus,
    CompleteSaleCommand,
    CompleteSaleHandler,
    CreateApartadoCommand,
    CreateApartadoHandler,
    CreateCategoryCommand,
    CreateCategoryHandler,
    CreateProductCommand,
    CreateProductHandler,
    CreateProviderCommand,
    CreateProviderHandler,
    DeleteProviderCommand,
    DeleteProviderHandler,
    DeletePurchaseOrderCommand,
    DeletePurchaseOrderHandler,
    GetApartadoHandler,
    GetApartadoQuery,
    GetApartadosHandler,
    GetApartadosQuery,
    GetCashMovementsHandler,
    GetCashMovementsQuery,
    GetCatalogHandler,
    GetCatalogQuery,
    GetCategoriesHandler,
    GetCategoriesQuery,
    GetCorteHandler,
    GetCorteQuery,
    GetDashboardHandler,
    GetDashboardQuery,
    GetLastCashDayHandler,
    GetLastCashDayQuery,
    GetOpenCashDayHandler,
    GetOpenCashDayQuery,
    GetProductHandler,
    GetProductQuery,
    GetSaleHandler,
    GetSaleQuery,
    GetSalesHistoryHandler,
    GetSalesHistoryQuery,
    GetStockMovementsHandler,
    GetStockMovementsQuery,
    Handler,
    ImportProviderItemsCommand,
    ImportProviderItemsHandler,
    LoadPurchaseOrderCommand,
    LoadPurchaseOrderHandler,
    ListProviderItemsHandler,
    ListProviderItemsQuery,
    ListProvidersHandler,
    ListProvidersQuery,
    ListPurchaseOrdersHandler,
    ListPurchaseOrdersQuery,
    OpenCashDayCommand,
    OpenCashDayHandler,
    QueryBus,
    ReceiveLineRequest,
    ReceivePurchaseOrderCommand,
    ReceivePurchaseOrderHandler,
    RefundSaleItemCommand,
    RefundSaleItemHandler,
    RegisterCashMovementCommand,
    RegisterCashMovementHandler,
    SavePurchaseOrderCommand,
    SavePurchaseOrderHandler,
    SaveSettingsCommand,
    SaveSettingsHandler,
    SetProductActiveCommand,
    SetProductActiveHandler,
    UpdateProductCommand,
    UpdateProductHandler,
    UpdateProviderCommand,
    UpdateProviderHandler,
    VoidSaleCommand,
    VoidSaleHandler,
)
from app.domain.events import (
    ApartadoAbonado,
    ApartadoCancelled,
    ApartadoCreated,
    ApartadoSettled,
    CashDayClosed,
    CashDayOpened,
    CashMovementRegistered,
    DomainEvent,
    LowStockAlert,
    SaleCompleted,
    SaleItemRefunded,
    SaleVoided,
    StockAdjusted,
)
from app.infrastructure.db import create_engine_for, create_session_factory, init_database
from app.infrastructure.topology import DEFAULT_BRANCH_ID, Topology, ensure_topology
from app.infrastructure.uow import SqlAlchemyUnitOfWork
from app.settings import Settings
from app.seed import seed_demo_data

log = logging.getLogger(__name__)


@dataclass
class AppServices:
    commands: CommandBus
    queries: QueryBus
    settings: Settings
    event_log: list[DomainEvent] = field(default_factory=list)
    topology: object | None = None  # app.infrastructure.topology.Topology (evita import circular)
    session_factory: object | None = None  # sqlalchemy.orm.sessionmaker (para el worker de sync)
    on_mutation: object | None = None  # callable() opcional: se invoca tras toda mutación local

    def run_seed_if_requested(self) -> None:
        if self.settings.seed_demo_data:
            seed_demo_data(self.commands, self.queries)

    def run_sync(self, direction: str = "both"):
        """Ejecuta una sincronización contra el hub (bloqueante).

        ``direction`` puede ser ``"both"`` (subir y bajar), ``"push"`` (sólo
        subir el estado local) o ``"pull"`` (sólo bajar del hub). Se lanza
        desde un QThread en la UI para no congelar la interfaz.
        """
        from app.infrastructure.sync import run_sync

        return run_sync(self.session_factory, self.topology, direction=direction)


def make_event_dispatcher(services: AppServices):
    """Convierte los eventos de dominio en registros de log y advertencias de stock."""

    def dispatch(events: list[DomainEvent]) -> None:
        for event in events:
            services.event_log.append(event)
            if isinstance(event, (SaleCompleted, SaleVoided)):
                log.info("Evento de dominio: %s (recibo %s).", type(event).__name__, event.receipt_number)
            elif isinstance(event, ApartadoCreated):
                log.info("Apartado creado: %s para %s (%s).", event.apartado_id, event.client_name, event.total.format())
            elif isinstance(event, ApartadoAbonado):
                log.info(
                    "Abono de %s en apartado %s (%s). Saldo: %s.",
                    event.amount.format(),
                    event.apartado_id,
                    event.method,
                    event.balance.format(),
                )
            elif isinstance(event, (ApartadoSettled, ApartadoCancelled)):
                log.info("Evento de dominio: %s (apartado %s).", type(event).__name__, event.apartado_id)
            elif isinstance(event, SaleItemRefunded):
                log.info(
                    "Reembolso de %d × %s (recibo %s): %s.",
                    event.quantity,
                    event.product_name,
                    event.receipt_number,
                    event.refund_amount.format(),
                )
            elif isinstance(event, CashMovementRegistered):
                log.info(
                    "Movimiento de caja %s por %s (%s%s).",
                    event.movement_type,
                    event.amount.format(),
                    event.reason,
                    f": {event.note}" if event.note else "",
                )
            elif isinstance(event, CashDayOpened):
                log.info("Jornada de caja %s abierta con %s por %s.", event.day_id, event.opening_cash.format(), event.opened_by)
            elif isinstance(event, CashDayClosed):
                log.info(
                    "Jornada de caja %s cerrada. Esperado: %s, contado: %s, diferencia: %s (%s).",
                    event.day_id,
                    event.expected_cash.format(),
                    event.closing_cash.format(),
                    event.difference.format(),
                    event.difference_kind,
                )
            elif isinstance(event, StockAdjusted):
                log.info(
                    "Stock ajustado %d en %s (razón: %s). Stock actual: %d",
                    event.delta,
                    event.code,
                    event.reason,
                    event.running_stock,
                )
            elif isinstance(event, LowStockAlert):
                log.warning(
                    "Stock bajo: %s (%s) quedó en %d (mínimo %d).",
                    event.name,
                    event.code,
                    event.stock,
                    event.min_stock,
                )
            else:
                log.debug("Evento de dominio: %s", type(event).__name__)
            if callable(services.on_mutation):
                services.on_mutation()

    return dispatch


def build_services(
    settings: Settings | None = None,
    *,
    initialize: bool = True,
    seeds: bool = False,
    event_log: list[DomainEvent] | None = None,
    topology: Topology | None = None,
) -> AppServices:
    """Factory de la aplicación. Idempotente: devuelve servicios listos para usar."""
    settings = settings or Settings()
    settings.ensure_layout()

    engine = create_engine_for(settings.database_path)
    if initialize:
        init_database(engine)
    session_factory = create_session_factory(engine)

    # Identidad de la instalación (topología sucursal/terminal). Si se pasan
    # sobrescrituras (CLI/instalación), quedan persistidas en sys_config.
    overrides = {}
    if topology is not None:
        overrides = {k: v for k, v in topology.as_dict().items() if v}
    with session_factory() as session:
        topology_loaded = ensure_topology(session, overrides=overrides)
        # Ajustes locales (local store, moneda, acceso, etiquetas) --> Settings.
        from app.infrastructure.local_config import load_local_config

        load_local_config(settings, session)

    services = AppServices(
        commands=None,
        queries=None,
        settings=settings,
        event_log=event_log or [],
        topology=topology_loaded,
        session_factory=session_factory,
    )
    dispatcher = make_event_dispatcher(services)

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            session_factory=session_factory,
            dispatcher=dispatcher,
            branch_id=topology_loaded.id_sucursal or DEFAULT_BRANCH_ID,
            terminal_id=topology_loaded.id_terminal,
        )

    command_handlers: dict[type, Handler] = {
        CreateCategoryCommand: CreateCategoryHandler(uow_factory),
        CreateProductCommand: CreateProductHandler(uow_factory),
        UpdateProductCommand: UpdateProductHandler(uow_factory),
        SetProductActiveCommand: SetProductActiveHandler(uow_factory),
        AdjustStockCommand: AdjustStockHandler(uow_factory),
        CompleteSaleCommand: CompleteSaleHandler(uow_factory),
        VoidSaleCommand: VoidSaleHandler(uow_factory),
        CreateApartadoCommand: CreateApartadoHandler(uow_factory),
        AddAbonoCommand: AddAbonoHandler(uow_factory),
        CancelApartadoCommand: CancelApartadoHandler(uow_factory),
        RefundSaleItemCommand: RefundSaleItemHandler(uow_factory),
        RegisterCashMovementCommand: RegisterCashMovementHandler(uow_factory),
        OpenCashDayCommand: OpenCashDayHandler(uow_factory),
        CloseCashDayCommand: CloseCashDayHandler(uow_factory),
        CreateProviderCommand: CreateProviderHandler(uow_factory),
        ImportProviderItemsCommand: ImportProviderItemsHandler(uow_factory),
        LoadPurchaseOrderCommand: LoadPurchaseOrderHandler(uow_factory),
        UpdateProviderCommand: UpdateProviderHandler(uow_factory),
        DeleteProviderCommand: DeleteProviderHandler(uow_factory),
        ReceivePurchaseOrderCommand: ReceivePurchaseOrderHandler(uow_factory),
        SavePurchaseOrderCommand: SavePurchaseOrderHandler(uow_factory),
        DeletePurchaseOrderCommand: DeletePurchaseOrderHandler(uow_factory),
        SaveSettingsCommand: SaveSettingsHandler(uow_factory),
    }
    query_handlers: dict[type, Handler] = {
        GetCategoriesQuery: GetCategoriesHandler(uow_factory),
        GetCatalogQuery: GetCatalogHandler(uow_factory),
        GetProductQuery: GetProductHandler(uow_factory),
        GetSalesHistoryQuery: GetSalesHistoryHandler(uow_factory),
        GetSaleQuery: GetSaleHandler(uow_factory),
        GetDashboardQuery: GetDashboardHandler(uow_factory),
        GetStockMovementsQuery: GetStockMovementsHandler(uow_factory),
        GetApartadosQuery: GetApartadosHandler(uow_factory),
        GetApartadoQuery: GetApartadoHandler(uow_factory),
        GetCashMovementsQuery: GetCashMovementsHandler(uow_factory),
        GetOpenCashDayQuery: GetOpenCashDayHandler(uow_factory),
        GetLastCashDayQuery: GetLastCashDayHandler(uow_factory),
        GetCorteQuery: GetCorteHandler(uow_factory),
        ListProvidersQuery: ListProvidersHandler(uow_factory),
        ListProviderItemsQuery: ListProviderItemsHandler(uow_factory),
        ListPurchaseOrdersQuery: ListPurchaseOrdersHandler(uow_factory),
    }

    services.commands = CommandBus(command_handlers)
    services.queries = QueryBus(query_handlers)

    if seeds or settings.seed_demo_data:
        services.run_seed_if_requested()
    return services