"""Vista de apartados: listado, creación, abonos, detalle y cancelación."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import CommandBus, QueryBus
from app.application.commands import (
    AddAbonoCommand,
    ApartadoItemRequest,
    CancelApartadoCommand,
    CreateApartadoCommand,
)
from app.application.queries import GetApartadoQuery, GetApartadosQuery
from app.application.read_models import ApartadoDTO
from app.domain.exceptions import DomainError
from app.interface.dialogs import (
    AbonoDialog,
    ApartadoDetailDialog,
    ApartadoDialog,
    show_domain_error,
)
from app.interface.widgets import make_label, make_table

STATUS_OPTIONS = (
    ("ACTIVO", "Activos"),
    ("LIQUIDADO", "Liquidados"),
    ("CANCELADO", "Cancelados"),
    (None, "Todos"),
)


class ApartadoView(QWidget):
    def __init__(self, commands: CommandBus, queries: QueryBus):
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._apartados: list[ApartadoDTO] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        layout.addWidget(make_label("Apartados", object_name="pageTitle"))

        toolbar = QHBoxLayout()
        self.status = QComboBox()
        for code, label in STATUS_OPTIONS:
            self.status.addItem(label, code)
        self.status.currentIndexChanged.connect(lambda _: self.refresh())
        new_btn = QPushButton("Nuevo apartado")
        abono_btn = QPushButton("Registrar abono")
        detail_btn = QPushButton("Ver detalle")
        cancel_btn = QPushButton("Cancelar apartado")
        for btn in (new_btn, abono_btn, detail_btn, cancel_btn):
            btn.setObjectName("ghost")
        new_btn.clicked.connect(self._create)
        abono_btn.clicked.connect(self._abono)
        detail_btn.clicked.connect(self._detail)
        cancel_btn.clicked.connect(self._cancel)

        toolbar.addWidget(QLabel("Estado:"))
        toolbar.addWidget(self.status)
        toolbar.addStretch(1)
        toolbar.addWidget(new_btn)
        toolbar.addWidget(abono_btn)
        toolbar.addWidget(detail_btn)
        toolbar.addWidget(cancel_btn)
        layout.addLayout(toolbar)

        self.table = make_table(
            ["N°", "Cliente", "Teléfono", "Fecha", "Ítems", "Total", "Abonado", "Saldo", "Estado"],
            stretch_column=1,
        )
        layout.addWidget(self.table, 1)
        self.count_label = make_label("", object_name="muted")
        layout.addWidget(self.count_label)

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._apartados = self._queries.ask(GetApartadosQuery(status=self.status.currentData()))
        self.table.setRowCount(0)
        for apartado in self._apartados:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(f"#{apartado.id}"))
            self.table.setItem(row, 1, QTableWidgetItem(apartado.client_name))
            self.table.setItem(row, 2, QTableWidgetItem(apartado.client_phone))
            self.table.setItem(row, 3, QTableWidgetItem(apartado.created_at.strftime("%d/%m/%Y %H:%M")))
            self.table.setItem(row, 4, QTableWidgetItem(str(apartado.item_count)))
            self.table.setItem(row, 5, QTableWidgetItem(apartado.total.format()))
            self.table.setItem(row, 6, QTableWidgetItem(apartado.amount_paid.format()))
            self.table.setItem(row, 7, QTableWidgetItem(apartado.balance.format()))
            self.table.setItem(row, 8, QTableWidgetItem(apartado.status))
        self.count_label.setText(f"{len(self._apartados)} apartados")

    def _selected(self) -> ApartadoDTO | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._apartados):
            return self._apartados[row]
        return None

    def _refresh_and_message(self, title: str, message: str) -> None:
        self.refresh()
        QMessageBox.information(self, title, message)

    # ------------------------------------------------------------------ #

    def _create(self) -> None:
        dialog = ApartadoDialog(self._queries, self)
        if dialog.exec() != ApartadoDialog.Accepted:
            return
        values = dialog.values()
        try:
            created = self._commands.execute(
                CreateApartadoCommand(
                    client_name=values["client_name"],
                    client_phone=values["client_phone"],
                    items=tuple(ApartadoItemRequest(code=code, quantity=qty) for code, qty in values["items"]),
                    initial_abono=values["initial_abono"],
                    abono_method=values["abono_method"],
                    note=values["note"],
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self._refresh_and_message(
            "Apartado creado",
            f"Apartado para {created.client_name} por {created.total.format()}\n"
            f"Saldo pendiente: {created.balance.format()}",
        )

    def _abono(self) -> None:
        apartado = self._selected()
        if apartado is None:
            QMessageBox.information(self, "Selección", "Seleccione un apartado.")
            return
        if apartado.status != "ACTIVO":
            QMessageBox.information(self, "Apartado", "Solo se pueden abonar apartados activos.")
            return
        dialog = AbonoDialog(apartado, self)
        if dialog.exec() != AbonoDialog.Accepted:
            return
        amount, method = dialog.values()
        try:
            updated = self._commands.execute(AddAbonoCommand(apartado_id=apartado.id, amount=amount, method=method))
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        message = f"Abono de {amount.format()} registrado."
        if updated.status == "LIQUIDADO":
            message += "\nEl apartado quedó liquidado."
        self._refresh_and_message("Abono registrado", message)

    def _detail(self) -> None:
        apartado = self._selected()
        if apartado is None:
            QMessageBox.information(self, "Selección", "Seleccione un apartado.")
            return
        apartado = self._queries.ask(GetApartadoQuery(apartado_id=apartado.id))
        ApartadoDetailDialog(apartado, self).exec()

    def _cancel(self) -> None:
        apartado = self._selected()
        if apartado is None:
            QMessageBox.information(self, "Selección", "Seleccione un apartado.")
            return
        if apartado.status != "ACTIVO":
            QMessageBox.information(self, "Apartado", "Solo se puede cancelar un apartado activo.")
            return
        question = QMessageBox.question(
            self,
            "Cancelar apartado",
            f"¿Cancelar el apartado de {apartado.client_name}?\n"
            f"Saldo pendiente: {apartado.balance.format()}\n"
            "El stock de los productos será restituido. Esta acción no se puede deshacer.",
        )
        if question != QMessageBox.Yes:
            return
        try:
            self._commands.execute(CancelApartadoCommand(apartado_id=apartado.id, reason="Cancelado por el cajero"))
        except DomainError as exc:
            show_domain_error(self, exc)
        self.refresh()