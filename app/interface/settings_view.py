"""Pantalla de configuración: datos del local, acceso, etiquetas y servidor.

Los ajustes se persisten en la tabla local ``sys_config`` y se aplican en
memoria de inmediato. Los cambios de sucursal/servidor requieren reiniciar la
aplicación (afectan la lectura del inventario y la sincronización).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import CommandBus, QueryBus
from app.application.commands import SaveSettingsCommand
from app.bootstrap import AppServices
from app.infrastructure.local_config import apply_local_config
from app.infrastructure.printers.ticket_esc_pos import list_printers, set_configured_printer
from app.infrastructure.sync.pocketbase_client import DEFAULT_POCKETBASE_URL
from app.interface.widgets import Card, make_label
from app.settings import Settings

PRINTER_KINDS = (
    ("windows", "Windows (controlador QPrinter)"),
    ("brother_ql", "Brother QL (red)"),
    ("null", "Sin impresora (solo vista previa)"),
)


class SettingsView(QWidget):
    def __init__(self, commands: CommandBus, queries: QueryBus, settings: Settings, services: AppServices):
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._settings = settings
        self._services = services

        page = QVBoxLayout(self)
        page.setContentsMargins(18, 14, 18, 14)
        page.setSpacing(12)
        page.addWidget(make_label("Configuración", object_name="pageTitle"))
        page.addWidget(
            make_label(
                "Ajustes persistentes de esta instalación. La sucursal y el servidor se aplican al reiniciar.",
                object_name="muted",
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(12)
        body_layout.addWidget(self._build_local_card())
        body_layout.addWidget(self._build_security_card())
        body_layout.addWidget(self._build_labels_card())
        body_layout.addWidget(self._build_ticket_printer_card())
        body_layout.addWidget(self._build_cloud_card())

        save_btn = QPushButton("Guardar configuración")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(save_btn)
        body_layout.addLayout(buttons)
        body_layout.addStretch(1)

        scroll.setWidget(body)
        page.addWidget(scroll, 1)

    # ------------------------------------------------------------------ #

    def _build_local_card(self) -> Card:
        card = Card("Local / tienda")
        form = QFormLayout()
        self.store_name = QLineEdit(self._settings.store.name)
        self.store_address = QLineEdit(self._settings.store.address)
        self.store_phone = QLineEdit(self._settings.store.phone)
        self.store_footer = QLineEdit(self._settings.store.footer)
        self.currency = QLineEdit(self._settings.currency)
        self.currency.setMaxLength(6)
        form.addRow("Nombre:", self.store_name)
        form.addRow("Dirección:", self.store_address)
        form.addRow("Teléfono:", self.store_phone)
        form.addRow("Pie de ticket:", self.store_footer)
        form.addRow("Moneda:", self.currency)
        card.add_layout(form)
        return card

    def _build_security_card(self) -> Card:
        card = Card("Acceso")
        form = QFormLayout()
        self.login_username = QLineEdit(self._settings.login_username)
        self.login_password = QLineEdit(self._settings.login_password)
        self.login_password.setEchoMode(QLineEdit.Password)
        form.addRow("Usuario:", self.login_username)
        form.addRow("Contraseña:", self.login_password)
        card.add_layout(form)
        return card

    def _build_labels_card(self) -> Card:
        card = Card("Impresora de etiquetas")
        form = QFormLayout()
        self.printer_combo = QComboBox()
        current = self._settings.label_printer_kind
        for value, label in PRINTER_KINDS:
            self.printer_combo.addItem(label, value)
        idx = self.printer_combo.findData(current)
        if idx >= 0:
            self.printer_combo.setCurrentIndex(idx)
        self.printer_ip = QLineEdit(self._settings.brother_printer_ip)
        self.printer_ip.setPlaceholderText("IP de la Brother QL-810W (modo brother_ql)")
        self.print_width = QDoubleSpinBox()
        self.print_width.setRange(20, 300)
        self.print_width.setDecimals(1)
        self.print_width.setValue(float(self._settings.label_width_mm))
        self.print_height = QDoubleSpinBox()
        self.print_height.setRange(10, 300)
        self.print_height.setDecimals(1)
        self.print_height.setValue(float(self._settings.label_height_mm))
        self.print_dpi = QSpinBox()
        self.print_dpi.setRange(150, 1200)
        self.print_dpi.setValue(int(self._settings.label_dpi))
        form.addRow("Tipo:", self.printer_combo)
        form.addRow("IP Brother:", self.printer_ip)
        form.addRow("Ancho etiqueta (mm):", self.print_width)
        form.addRow("Alto etiqueta (mm):", self.print_height)
        form.addRow("Resolución (DPI):", self.print_dpi)
        card.add_layout(form)
        return card

    def _build_ticket_printer_card(self) -> Card:
        card = Card("Impresora de tickets y cajón de dinero")
        form = QFormLayout()
        self.ticket_printer_combo = QComboBox()
        self.ticket_printer_combo.setEditable(True)
        installed = list_printers()
        self.ticket_printer_combo.addItem("Automática (detectar)", "")
        for name in installed:
            self.ticket_printer_combo.addItem(name, name)
        current = self._settings.ticket_printer or ""
        if current:
            idx = self.ticket_printer_combo.findData(current)
            if idx >= 0:
                self.ticket_printer_combo.setCurrentIndex(idx)
            else:
                self.ticket_printer_combo.setCurrentIndex(0)
                self.ticket_printer_combo.setCurrentText(current)
        self.ticket_printer_combo.setToolTip(
            "El nombre exacto de la impresora térmica (ej. 'ZKteco ticket'). "
            "Con 'Automática' la detecta por su nombre."
        )
        form.addRow("Impresora de tickets:", self.ticket_printer_combo)
        card.add_layout(form)

        tests = QHBoxLayout()
        test_print_btn = QPushButton("Probar impresión")
        test_drawer_btn = QPushButton("Abrir cajón de dinero")
        test_print_btn.setObjectName("ghost")
        test_drawer_btn.setObjectName("ghost")
        test_print_btn.clicked.connect(self._test_ticket)
        test_drawer_btn.clicked.connect(self._test_drawer)
        tests.addWidget(test_print_btn)
        tests.addWidget(test_drawer_btn)
        tests.addStretch(1)
        card.add_layout(tests)
        card.add(
            make_label(
                "Si la venta no imprime o el cajón no abre, elige aquí la impresora "
                "térmica y guarda. Usa 'Probar impresión' para verificar.",
                object_name="muted",
            )
        )
        return card

    def _test_ticket(self) -> None:
        from app.infrastructure.printers.ticket_esc_pos import print_test_ticket, set_configured_printer

        printer = self.ticket_printer_combo.currentData() or self.ticket_printer_combo.currentText().strip()
        set_configured_printer(printer)
        if print_test_ticket(printer):
            QMessageBox.information(self, "Impresión", "Ticket de prueba enviado a la impresora.")
        else:
            QMessageBox.warning(
                self,
                "Impresión",
                "No se pudo imprimir. Verifica que la impresora esté instalada y sea "
                "térmica (ESC/POS), o revisa el nombre configurado.",
            )

    def _test_drawer(self) -> None:
        from app.infrastructure.printers.ticket_esc_pos import kick_cash_drawer, set_configured_printer

        printer = self.ticket_printer_combo.currentData() or self.ticket_printer_combo.currentText().strip()
        set_configured_printer(printer)
        if kick_cash_drawer(printer):
            QMessageBox.information(self, "Cajón", "Pulso de apertura enviado al cajón de dinero.")
        else:
            QMessageBox.warning(
                self,
                "Cajón",
                "No se abrió el cajón. Verifica el cable RJ11 conectado a la impresora.",
            )

    def _build_cloud_card(self) -> Card:
        topology = self._services.topology
        current_terminal = getattr(topology, "id_terminal", "") or ""
        card = Card("Sucursal y servidor (se aplican al reiniciar)")
        card.add(
            make_label(
                f"Terminal de caja: {current_terminal or '—'}. El id de terminal no se edita aquí.",
                object_name="muted",
            )
        )
        form = QFormLayout()
        self.branch_edit = QLineEdit(getattr(topology, "id_sucursal", "") or "")
        self.terminal_num_edit = QLineEdit(getattr(topology, "terminal_num", "") or "")
        self.terminal_num_edit.setPlaceholderText("Ej.: 1 (caja 1) o 2 (caja 2)")
        self.pocketbase_url = QLineEdit(getattr(topology, "pocketbase_url", "") or "")
        self.pocketbase_url.setPlaceholderText(DEFAULT_POCKETBASE_URL)
        self.pocketbase_token = QLineEdit(getattr(topology, "pocketbase_token", "") or "")
        self.pocketbase_token.setEchoMode(QLineEdit.Password)
        form.addRow("ID sucursal:", self.branch_edit)
        form.addRow("Nº de caja (prefijo ticket):", self.terminal_num_edit)
        form.addRow("PocketBase URL:", self.pocketbase_url)
        form.addRow("Token de acceso:", self.pocketbase_token)
        card.add_layout(form)

        sync_row = QHBoxLayout()
        self._sync_button = QPushButton("Sincronizar ahora")
        self._sync_button.setObjectName("primary")
        self._sync_button.clicked.connect(self._sync_now)
        self.sync_status = make_label(self._last_sync_text(), object_name="muted")
        self.sync_status.setWordWrap(True)
        sync_row.addWidget(self._sync_button)
        sync_row.addWidget(self.sync_status, 1)
        card.add_layout(sync_row)
        return card

    def _last_sync_text(self) -> str:
        factory = self._services.session_factory
        if factory is None:
            return "Sin sincronizar todavía."
        try:
            from sqlalchemy import text

            with factory() as session:
                value = session.execute(
                    text("SELECT value FROM sys_config WHERE key = 'last_sync_at'")
                ).scalar_one_or_none()
        except Exception:  # noqa: BLE001
            return "Sin sincronizar todavía."
        return f"Última sincronización: {value}" if value else "Sin sincronizar todavía."

    def _sync_now(self) -> None:
        from app.interface.sync_worker import SyncWorker

        if getattr(self, "_sync_worker", None) is not None and self._sync_worker.isRunning():
            return
        self._sync_button.setEnabled(False)
        self._sync_button.setText("Sincronizando…")
        self.sync_status.setText("Conectando con el servidor…")
        worker = SyncWorker(self._services, parent=self)
        worker.finished_ok.connect(self._sync_done)
        worker.failed.connect(self._sync_failed)
        worker.finished.connect(lambda: self._sync_button.setEnabled(True))
        self._sync_worker = worker
        worker.start()

    def _sync_done(self, report) -> None:
        self._sync_button.setText("Sincronizar ahora")
        self.sync_status.setText(self._last_sync_text())
        parts = [f"{k}: {v}" for k, v in report.pushed.items()] + [f"{k}: {v}" for k, v in report.pulled.items()]
        detail = ", ".join(parts) or "sin cambios"
        QMessageBox.information(
            self,
            "Sincronización",
            f"Sincronización completada en {report.duration_ms / 1000:.1f} s.\n\nSubido → {detail}",
        )

    def _sync_failed(self, error: str) -> None:
        self._sync_button.setText("Sincronizar ahora")
        self.sync_status.setText("La última sincronización falló.")
        short = error if len(error) <= 160 else error[:157] + "..."
        QMessageBox.warning(self, "Sincronización", f"No se pudo sincronizar.\n\n{short}")

    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Antepone ``http://`` a la URL del servidor si le falta el esquema."""
        url = (url or "").strip().rstrip("/")
        if url and not url.lower().startswith(("http://", "https://")):
            url = "http://" + url
        return url

    def _save(self) -> None:
        values = dict(
            store_name=self.store_name.text().strip(),
            store_address=self.store_address.text().strip(),
            store_phone=self.store_phone.text().strip(),
            store_footer=self.store_footer.text().strip(),
            currency=self.currency.text().strip() or "$",
            login_username=self.login_username.text().strip(),
            login_password=self.login_password.text().strip(),
            label_printer_kind=self.printer_combo.currentData(),
            brother_printer_ip=self.printer_ip.text().strip(),
            label_width_mm=float(self.print_width.value()),
            label_height_mm=float(self.print_height.value()),
            label_dpi=int(self.print_dpi.value()),
            ticket_printer=self.ticket_printer_combo.currentData()
            or self.ticket_printer_combo.currentText().strip(),
            id_sucursal=self.branch_edit.text().strip(),
            terminal_num=self.terminal_num_edit.text().strip(),
            pocketbase_url=self._normalize_url(self.pocketbase_url.text()),
            pocketbase_token=self.pocketbase_token.text().strip(),
        )
        try:
            self._commands.execute(SaveSettingsCommand(**values))
        except Exception as exc:  # noqa: BLE001 - error de persistencia
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return

        apply_local_config(
            self._settings,
            store_name=values["store_name"],
            store_address=values["store_address"],
            store_phone=values["store_phone"],
            store_footer=values["store_footer"],
            currency=values["currency"],
            login_username=values["login_username"],
            login_password=values["login_password"],
            label_printer_kind=values["label_printer_kind"],
            brother_printer_ip=values["brother_printer_ip"],
            label_width_mm=values["label_width_mm"],
            label_height_mm=values["label_height_mm"],
            label_dpi=values["label_dpi"],
            ticket_printer=values["ticket_printer"],
        )
        set_configured_printer(values["ticket_printer"])

        if self._services.topology is not None:
            from app.infrastructure.topology import Topology

            old = self._services.topology
            self._services.topology = Topology(
                id_sucursal=values["id_sucursal"],
                id_terminal=old.id_terminal,
                terminal_num=values["terminal_num"],
                pocketbase_url=values["pocketbase_url"],
                pocketbase_token=values["pocketbase_token"],
            )

        topology_changed = bool(values["id_sucursal"] or values["pocketbase_url"] or values["pocketbase_token"])
        message = "Configuración guardada."
        if topology_changed:
            message += "\n\nLa sucursal y el servidor se aplicarán al reiniciar la aplicación."
        QMessageBox.information(self, "Guardado", message)