"""Ventana principal: barra de navegación superior con glifos y atajos F1–F5."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.bootstrap import AppServices
from app.interface.apartado_view import ApartadoView
from app.interface.cardex_view import CardexView
from app.interface.cash_view import CashView
from app.interface.dashboard_view import DashboardView
from app.interface.history_view import HistoryView
from app.interface.inventory_view import InventoryView
from app.interface.products_view import ProductsView
from app.interface.providers_view import ProvidersView
from app.interface.sell_view import SellView
from app.interface.settings_view import SettingsView
from app.interface.widgets import glyph_icon, make_label, with_shortcut
from app.infrastructure.printing.label_printer import LabelPrintingService
from app.settings import Settings

NAV_ITEMS = (
    ("inicio", "Inicio", "\U0001F3E0", "F1"),
    ("vender", "Vender", "\U0001F6D2", "F2"),
    ("productos", "Productos", "\U0001F4E6", "F3"),
    ("cardex", "Cardex", "\U0001F4CB", "F6"),
    ("inventario", "Inventario", "\U0001F4CA", "F4"),
    ("historial", "Historial", "\U0001F9FE", "F5"),
    ("apartados", "Apartados", "\U0001F4B3", "F7"),
    ("proveedores", "Proveedores", "\U0001F69A", "F8"),
    ("caja", "Caja", "\U0001F4B0", "F10"),
    ("config", "Config", "\u2699\ufe0f", "F9"),
)

log = logging.getLogger(__name__)


def build_pages(services: AppServices, settings: Settings, labels: LabelPrintingService) -> dict[str, QWidget]:
    return {
        "inicio": DashboardView(services.queries),
        "vender": SellView(services.commands, services.queries, settings),
        "productos": ProductsView(services.commands, services.queries, labels),
        "cardex": CardexView(services.queries),
        "inventario": InventoryView(services.commands, services.queries),
        "historial": HistoryView(services.commands, services.queries, settings),
        "apartados": ApartadoView(services.commands, services.queries),
        "proveedores": ProvidersView(services.commands, services.queries, settings),
        "caja": CashView(services.commands, services.queries, opened_by=settings.login_username, settings=settings),
        "config": SettingsView(services.commands, services.queries, settings, services),
    }


class MainWindow(QMainWindow):
    def __init__(self, services: AppServices, settings: Settings, labels: LabelPrintingService):
        super().__init__()
        self._services = services
        self._settings = settings
        self._auto_sync_worker: SyncWorker | None = None
        self._sync_timer: QTimer | None = None
        self._realtime_worker = None
        self._sync_coordinator = None
        self.setWindowTitle(settings.app_name)
        self.resize(1280, 780)

        central = QWidget()
        central.setObjectName("centralArea")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_topbar(settings))

        self._pages: dict[str, QWidget] = build_pages(services, settings, labels)
        self._stack = QStackedWidget()
        for key in ("inicio", "vender", "productos", "cardex", "inventario", "historial", "apartados", "proveedores", "caja", "config"):
            self._stack.addWidget(self._pages[key])
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        dashboard = self._pages["inicio"]
        if isinstance(dashboard, DashboardView):
            dashboard.set_inventory_navigator(lambda: self.switch_page("inventario"))

        cardex = self._pages["cardex"]
        if isinstance(cardex, CardexView):
            products = self._pages["productos"]
            if isinstance(products, ProductsView):
                products.set_cardex_navigator(lambda product_id: self._open_cardex(product_id))

        self._nav_buttons["inicio"].setChecked(True)
        self.switch_page("inicio")

        QShortcut(QKeySequence(Qt.CTRL | Qt.Key_Space), self, self._open_drawer_hidden)
        self._schedule_initial_sync()

    def _schedule_initial_sync(self) -> None:
        """Al arrancar, una caja conectada sincroniza una vez y luego cada intervalo."""
        topology = getattr(self._services, "topology", None)
        if not getattr(topology, "is_cloud_configured", False):
            return
        QTimer.singleShot(4000, self._auto_sync_silent)
        interval_ms = max(1, self._settings.sync_interval_seconds) * 1000
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(interval_ms)
        self._sync_timer.timeout.connect(self._auto_sync_silent)
        self._sync_timer.start()
        log.info("Sincronización automática cada %d s.", self._settings.sync_interval_seconds)
        self._start_realtime()

    def _start_realtime(self) -> None:
        """Activa la escucha realtime (PocketBase SSE) y el coordinador de sync.

        Cualquier cambio remoto en la sucursal dispara un pull en caliente; toda
        mutación local (venta/anulación/stock) dispara un push. Si realtime no
        está disponible, el sistema sigue funcionando con el sync periódico.
        """
        try:
            from app.interface.realtime_worker import RealtimeWorker
            from app.interface.sync_coordinator import SyncCoordinator

            topology = self._services.topology
            url = getattr(topology, "pocketbase_url", "")
            token = getattr(topology, "pocketbase_token", "")
            branch = topology.id_sucursal or getattr(topology, "default_branch_id", "")
            terminal = topology.id_terminal or ""
            if not url:
                return

            coordinator = SyncCoordinator(self._services, parent=self)
            worker = RealtimeWorker(url, token, branch, terminal, parent=self)
            worker.change_received.connect(coordinator.notify_remote_change)
            worker.status_changed.connect(self._on_realtime_status)
            self._sync_coordinator = coordinator
            self._realtime_worker = worker
            worker.start()
            self._services.on_mutation = coordinator.notify_local_change
            log.info("Realtime habilitado para sucursal %s (terminal %s).", branch, terminal)
        except Exception:  # noqa: BLE001 - el arranque nunca debe romperse por Realtime
            log.exception("Realtime no pudo iniciarse; queda el sync periódico.")

    def _on_realtime_status(self, status: str) -> None:
        if status == "conectado":
            log.info("Realtime conectado.")
        elif status.startswith("error"):
            log.error("Realtime con problemas: %s", status)

    def _auto_sync_silent(self) -> None:
        from app.interface.sync_worker import SyncWorker

        if self._auto_sync_worker is not None and self._auto_sync_worker.isRunning():
            return
        try:
            worker = SyncWorker(self._services, parent=self)
            worker.finished.connect(lambda: setattr(self, "_auto_sync_worker", None))
            self._auto_sync_worker = worker
            worker.start()
        except Exception:  # noqa: BLE001 - el arranque no debe romperse por el sync
            log.exception("Auto-sincronización no disponible.")

    def _open_drawer_hidden(self) -> None:
        """Atajo oculto global (Ctrl+Barra espaciadora) para abrir el cajón de dinero."""
        try:
            from app.infrastructure.printers.ticket_esc_pos import kick_cash_drawer

            if not kick_cash_drawer():
                log.warning("Cajón: no se abrió con el atajo oculto global.")
        except Exception:  # noqa: BLE001 - el pulso nunca debe romper la app
            log.exception("Cajón: falló la apertura con el atajo oculto global.")

    def _build_topbar(self, settings: Settings) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")

        brand = QHBoxLayout()
        brand.setSpacing(8)
        title = QLabel(settings.store.name or settings.app_name)
        title.setObjectName("appTitle")
        subtitle = QLabel("Punto de venta")
        subtitle.setObjectName("appSubtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)

        nav = QHBoxLayout()
        nav.setSpacing(6)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: dict[str, QToolButton] = {}
        for key, label, glyph, shortcut in NAV_ITEMS:
            button = QToolButton()
            button.setObjectName("topNav")
            button.setText(with_shortcut(label, shortcut))
            button.setIcon(glyph_icon(glyph))
            button.setIconSize(QSize(28, 28))
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setShortcut(shortcut)
            button.setToolTip(f"{label} ({shortcut})")
            self._nav_group.addButton(button)
            button.clicked.connect(lambda _=False, k=key: self.switch_page(k))
            nav.addWidget(button)
            self._nav_buttons[key] = button

        version = make_label("v1.0.0", object_name="appSubtitle")

        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(14, 8, 14, 8)
        bar_layout.setSpacing(16)
        bar_layout.addLayout(brand)
        bar_layout.addStretch(1)
        bar_layout.addLayout(nav)
        bar_layout.addStretch(1)
        bar_layout.addWidget(version)
        return bar

    def switch_page(self, key: str) -> None:
        page = self._pages[key]
        self._stack.setCurrentWidget(page)
        button = self._nav_buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        if hasattr(page, "refresh"):
            page.refresh()

    def _open_cardex(self, product_id: int) -> None:
        self.switch_page("cardex")
        page = self._pages["cardex"]
        if isinstance(page, CardexView):
            page.select_product(product_id)

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            if self._realtime_worker is not None:
                self._realtime_worker.stop()
            if self._sync_coordinator is not None:
                self._sync_coordinator.shutdown()
        finally:
            self._services.on_mutation = None
            event.accept()