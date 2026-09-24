"""Vista de historial de ventas: filtros, detalle, reimpresión y anulación."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import CommandBus, QueryBus
from app.application.commands import VoidSaleCommand
from app.application.queries import GetSaleQuery, GetSalesHistoryQuery
from app.application.read_models import SaleDTO
from app.domain.exceptions import DomainError
from app.infrastructure.printers.receipt import render_receipt_html
from app.settings import Settings
from app.interface.dialogs import ReceiptDialog, RefundLineDialog, show_domain_error
from app.interface.widgets import make_label, make_table


def _terminal_brush(receipt_number: str) -> object:
    """QBrush con el color sólido de la caja (para el texto de la columna 'Caja')."""
    from PySide6.QtGui import QBrush

    from app.interface.terminal_colors import terminal_color, terminal_key

    color = terminal_color(terminal_key(receipt_number))
    color.setAlpha(255)
    return QBrush(color)


class HistoryView(QWidget):
    def __init__(self, commands: CommandBus, queries: QueryBus, settings: Settings):
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._settings = settings
        self._sales: list[SaleDTO] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        layout.addWidget(make_label("Historial de ventas", object_name="pageTitle"))

        self.split = QHBoxLayout()
        self.split.setSpacing(16)
        self.split.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por recibo o producto (usa % como comodín)…")
        self.search.textChanged.connect(lambda _: self._apply())
        toolbar.addWidget(self.search, 2)
        self.since = QDateEdit()
        self.since.setCalendarPopup(True)
        self.since.setDisplayFormat("dd/MM/yyyy")
        self.since.setDate(datetime.now().date() - timedelta(days=30))
        self.until = QDateEdit()
        self.until.setCalendarPopup(True)
        self.until.setDisplayFormat("dd/MM/yyyy")
        self.until.setDate(datetime.now().date())
        self.all_time = QCheckBox("Todo")
        self.all_time.setChecked(False)
        self.status = QComboBox()
        self.status.addItem("Todas", None)
        self.status.addItem("Completadas", "COMPLETADA")
        self.status.addItem("Devueltas", "DEVUELTA")
        self.status.addItem("Anuladas", "ANULADA")
        refresh_btn = QPushButton("Actualizar")
        refresh_btn.setObjectName("primary")
        self.all_time.stateChanged.connect(lambda _: self._apply())
        self.since.dateChanged.connect(lambda _: self._apply())
        self.until.dateChanged.connect(lambda _: self._apply())
        self.status.currentIndexChanged.connect(lambda _: self._apply())
        refresh_btn.clicked.connect(self.refresh)

        toolbar.addWidget(QLabel("Desde:"))
        toolbar.addWidget(self.since)
        toolbar.addWidget(QLabel("Hasta:"))
        toolbar.addWidget(self.until)
        toolbar.addWidget(self.all_time)
        toolbar.addWidget(self.status)
        toolbar.addStretch(1)
        toolbar.addWidget(refresh_btn)
        layout.addLayout(toolbar)

        self.table = make_table(
            ["Recibo", "Caja", "Fecha", "Ítems", "Total", "Método", "Estado"],
            stretch_column=None,
        )
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(3, 60)
        self.table.setColumnWidth(4, 120)
        self.table.setColumnWidth(5, 120)
        self.table.setColumnWidth(6, 100)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.currentCellChanged.connect(lambda *_: self._show_detail())
        self.split.addWidget(self.table)

        self.detail = make_table(
            ["Producto", "Cant.", "Precio", "Subtotal"],
            stretch_column=0,
        )
        self.detail.setMinimumWidth(340)
        self.detail.setSelectionMode(QAbstractItemView.NoSelection)
        self.split.addWidget(self.detail)
        self.split.setStretchFactor(self.table, 1)
        self.split.setStretchFactor(self.detail, 1)
        layout.addLayout(self.split, 1)

        actions = QHBoxLayout()
        detail_btn = QPushButton("Ver detalle / reimprimir")
        refund_btn = QPushButton("Devolver partida")
        void_btn = QPushButton("Anular venta")
        for btn in (detail_btn, refund_btn, void_btn):
            btn.setObjectName("ghost")
        detail_btn.clicked.connect(self._detail)
        refund_btn.clicked.connect(self._refund_line)
        void_btn.clicked.connect(self._void)
        actions.addWidget(detail_btn)
        actions.addWidget(refund_btn)
        actions.addWidget(void_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.count_label = make_label("", object_name="muted")
        layout.addWidget(self.count_label)

    # ------------------------------------------------------------------ #

    def _apply(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        start = None
        end = None
        if not self.all_time.isChecked():
            start = datetime.combine(self.since.date().toPython(), datetime.min.time())
            end = datetime.combine(self.until.date().toPython(), datetime.max.time())
        self._sales = self._queries.ask(
            GetSalesHistoryQuery(
                start=start,
                end=end,
                status=self.status.currentData(),
                search=self.search.text().strip(),
            )
        )
        self._render()

    def _render(self) -> None:
        from app.interface.terminal_colors import terminal_label, terminal_row_color

        self.table.setRowCount(0)
        for sale in self._sales:
            row = self.table.rowCount()
            self.table.insertRow(row)
            recibo_item = QTableWidgetItem(sale.receipt_number)
            caja_item = QTableWidgetItem(terminal_label(sale.receipt_number))
            background = terminal_row_color(sale.receipt_number)
            recibo_item.setBackground(background)
            caja_item.setBackground(background)
            caja_item.setForeground(_terminal_brush(sale.receipt_number))
            self.table.setItem(row, 0, recibo_item)
            self.table.setItem(row, 1, caja_item)
            self.table.setItem(row, 2, QTableWidgetItem(sale.created_at.strftime("%d/%m/%Y %H:%M")))
            self.table.setItem(row, 3, QTableWidgetItem(str(sale.item_count)))
            total_item = QTableWidgetItem(sale.total.format())
            self.table.setItem(row, 4, total_item)
            self.table.setItem(row, 5, QTableWidgetItem(sale.methods_label))
            state_item = QTableWidgetItem(sale.status)
            self.table.setItem(row, 6, state_item)
        self.count_label.setText(f"{len(self._sales)} ventas")

    def _selected(self) -> SaleDTO | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._sales):
            return None
        return self._sales[row]

    # ------------------------------------------------------------------ #

    def _show_detail(self) -> None:
        sale = self._selected()
        if sale is None:
            self.detail.setRowCount(0)
            return
        sale = self._queries.ask(GetSaleQuery(sale_id=sale.id))
        self.detail.setRowCount(0)
        for item in sale.items:
            row = self.detail.rowCount()
            self.detail.insertRow(row)
            self.detail.setItem(row, 0, QTableWidgetItem(item.product_name))
            self.detail.setItem(row, 1, QTableWidgetItem(str(item.quantity)))
            self.detail.setItem(row, 2, QTableWidgetItem(item.unit_price.format()))
            self.detail.setItem(row, 3, QTableWidgetItem(item.subtotal.format()))

    def _detail(self) -> None:
        sale = self._selected()
        if sale is None:
            QMessageBox.information(self, "Selección", "Seleccione una venta.")
            return
        sale = self._queries.ask(GetSaleQuery(sale_id=sale.id))
        html = render_receipt_html(sale, self._settings.store)
        ReceiptDialog(html, self).exec()

    def _refund_line(self) -> None:
        sale = self._selected()
        if sale is None:
            QMessageBox.information(self, "Selección", "Seleccione una venta.")
            return
        if sale.status != "COMPLETADA":
            QMessageBox.information(
                self,
                "Devolución",
                "Solo se pueden devolver partidas de ventas completadas.",
            )
            return
        dialog = RefundLineDialog(self._commands, self._queries, sale.id, self)
        dialog.exec()
        self.refresh()

    def _void(self) -> None:
        sale = self._selected()
        if sale is None:
            QMessageBox.information(self, "Selección", "Seleccione una venta.")
            return
        if sale.status == "ANULADA":
            QMessageBox.information(self, "Anulación", "La venta ya está anulada.")
            return
        question = QMessageBox.question(
            self,
            "Anular venta",
            f"¿Anular la venta {sale.receipt_number} por {sale.total.format()}?\n"
            "El stock será restablecido. Esta acción no se puede deshacer.",
        )
        if question != QMessageBox.Yes:
            return
        try:
            self._commands.execute(VoidSaleCommand(sale_id=sale.id, reason="Anulada por el cajero"))
        except DomainError as exc:
            show_domain_error(self, exc)
        self.refresh()