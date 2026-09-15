"""Vista de caja: estado de la jornada, ingresos/egresos de efectivo y corte."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import CommandBus, QueryBus
from app.application.queries import GetCashMovementsQuery, GetCorteQuery, GetOpenCashDayQuery
from app.application.read_models import CashDayDTO, CorteDTO
from app.interface.dialogs import (
    MOVEMENT_LABELS,
    CashMovementDialog,
    CorteDialog,
    OpenCashDayDialog,
)
from app.interface.widgets import make_label, make_table


class CashView(QWidget):
    def __init__(self, commands: CommandBus, queries: QueryBus, opened_by: str = "", settings=None):
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._opened_by = opened_by
        self._settings = settings
        self._day: CashDayDTO | None = None
        self._corte: CorteDTO | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        layout.addWidget(make_label("Caja", object_name="pageTitle"))
        layout.addWidget(make_label("Fondo inicial, ingresos, egresos y corte de la jornada.", object_name="muted"))

        status = QGridLayout()
        status.setHorizontalSpacing(28)
        self.status_value = make_label("", object_name="kpiValue")
        self.fondo_value = make_label("", object_name="muted")
        self.expected_value = make_label("", object_name="muted")
        self.sales_value = make_label("", object_name="muted")
        status.addWidget(make_label("Estado", object_name="kpiLabel"), 0, 0)
        status.addWidget(make_label("Fondo inicial", object_name="kpiLabel"), 0, 1)
        status.addWidget(make_label("Efectivo esperado", object_name="kpiLabel"), 0, 2)
        status.addWidget(make_label("Ventas del día", object_name="kpiLabel"), 0, 3)
        status.addWidget(self.status_value, 1, 0)
        status.addWidget(self.fondo_value, 1, 1)
        status.addWidget(self.expected_value, 1, 2)
        status.addWidget(self.sales_value, 1, 3)
        layout.addLayout(status)

        actions = QHBoxLayout()
        self.open_btn = QPushButton("Iniciar día de caja")
        self.income_btn = QPushButton("Ingresar efectivo")
        self.withdraw_btn = QPushButton("Retirar efectivo")
        self.corte_btn = QPushButton("Corte de caja")
        self.open_btn.setObjectName("primary")
        for btn in (self.income_btn, self.withdraw_btn, self.corte_btn):
            btn.setObjectName("ghost")
        self.open_btn.clicked.connect(self._open_day)
        self.income_btn.clicked.connect(lambda: self._movement("ENTRADA"))
        self.withdraw_btn.clicked.connect(lambda: self._movement("SALIDA"))
        self.corte_btn.clicked.connect(self._do_corte)
        actions.addWidget(self.open_btn)
        actions.addWidget(self.income_btn)
        actions.addWidget(self.withdraw_btn)
        actions.addWidget(self.corte_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        layout.addWidget(make_label("Movimientos de caja", object_name="sectionTitle"))
        self.table = make_table(
            ["Fecha", "Tipo", "Concepto", "Monto", "Nota"],
            stretch_column=4,
        )
        layout.addWidget(self.table, 1)
        self.count_label = make_label("", object_name="muted")
        layout.addWidget(self.count_label)

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._day = self._queries.ask(GetOpenCashDayQuery())
        self._corte = self._queries.ask(GetCorteQuery())
        movements = self._queries.ask(GetCashMovementsQuery())

        if self._day is not None:
            self.status_value.setText("Abierta")
            self.fondo_value.setText(self._day.opening_cash.format())
            expected = self._corte.expected_cash.format() if self._corte else "—"
            self.expected_value.setText(expected)
            self.sales_value.setText(str(self._corte.sales_count) if self._corte else "0")
            self.open_btn.setEnabled(False)
            self.income_btn.setEnabled(True)
            self.withdraw_btn.setEnabled(True)
            self.corte_btn.setEnabled(True)
        else:
            self.status_value.setText("Cerrada / sin jornada")
            self.fondo_value.setText("—")
            self.expected_value.setText("—")
            self.sales_value.setText("—")
            self.open_btn.setEnabled(True)
            self.income_btn.setEnabled(False)
            self.withdraw_btn.setEnabled(False)
            self.corte_btn.setEnabled(False)

        self.table.setRowCount(0)
        for movement in movements:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(movement.created_at.strftime("%d/%m/%Y %H:%M")))
            self.table.setItem(row, 1, QTableWidgetItem(MOVEMENT_LABELS.get(movement.movement_type, movement.movement_type)))
            self.table.setItem(row, 2, QTableWidgetItem(MOVEMENT_LABELS.get(movement.reason, movement.reason)))
            amount_item = QTableWidgetItem(movement.amount.format())
            amount_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 3, amount_item)
            self.table.setItem(row, 4, QTableWidgetItem(movement.note or ""))
        self.count_label.setText(f"{len(movements)} movimientos")

    # ------------------------------------------------------------------ #

    def _open_day(self) -> None:
        dialog = OpenCashDayDialog(self._commands, opened_by=self._opened_by, parent=self)
        if dialog.exec() == OpenCashDayDialog.Accepted:
            self.refresh()

    def _movement(self, movement_type: str) -> None:
        dialog = CashMovementDialog(self._commands, movement_type, parent=self)
        if dialog.exec() == CashMovementDialog.Accepted:
            self.refresh()

    def _do_corte(self) -> None:
        dialog = CorteDialog(self._commands, self._queries, opened_by=self._opened_by, settings=self._settings, parent=self)
        if dialog.exec() == CorteDialog.Accepted:
            self.refresh()