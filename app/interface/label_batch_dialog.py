"""Etiquetas con checklist: elegir renglones y copias antes de imprimir en lote."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.domain.value_objects import Money
from app.infrastructure.printing.label_printer import LabelPrintingService
from app.interface.widgets import make_label, make_table, search_matches


@dataclass(slots=True)
class LabelBatchEntry:
    """Un candidato a etiqueta (código, nombre, precio) con copias por defecto."""

    code: str
    name: str
    price: Money
    copies: int = 1


class LabelBatchDialog(QDialog):
    """Muestra una checklist de etiquetas y las imprime en lote de una vez.

    Cada fila trae un spinbox de copias (inicializado normalmente a la cantidad
    del renglón de pedido/recepción) y el usuario marca cuáles imprimir.
    """

    def __init__(self, service: LabelPrintingService, entries: Iterable[LabelBatchEntry], parent=None):
        super().__init__(parent)
        self._service = service
        self._entries = list(entries)
        self.setWindowTitle("Imprimir etiquetas")
        self.resize(760, 480)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(make_label("Imprimir etiquetas", object_name="pageTitle"))
        layout.addWidget(
            make_label(
                "Marque los renglones e indique cuántas etiquetas imprime cada uno. "
                "El lote se envía en una sola pasada.",
                object_name="muted",
            )
        )

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar por código o descripción…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._render())
        toolbar.addWidget(self.search, 1)
        self.count_label = make_label("", object_name="muted")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.table = make_table(
            ["", "Código", "Descripción", "Copias"],
            stretch_column=2,
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 34)
        self.table.setColumnWidth(1, 100)
        self.table.setColumnWidth(3, 74)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        close_btn = QPushButton("Cerrar")
        close_btn.setObjectName("ghost")
        close_btn.clicked.connect(self.reject)
        self.print_btn = QPushButton("Imprimir etiquetas")
        self.print_btn.setObjectName("accent")
        self.print_btn.clicked.connect(self._print_selected)
        buttons.addWidget(close_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.print_btn)
        layout.addLayout(buttons)

        self._render()

    # ------------------------------------------------------------------ #

    def _visible(self) -> list[int]:
        term = self.search.text().strip()
        if not term:
            return list(range(len(self._entries)))
        return [
            i for i, entry in enumerate(self._entries) if search_matches(term, entry.name, entry.code)
        ]

    def _render(self) -> None:
        rows = self._visible()
        self.table.setRowCount(0)
        for index in rows:
            entry = self._entries[index]
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked)
            check.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(entry.code))
            self.table.setItem(row, 2, QTableWidgetItem(entry.name))
            spin = QSpinBox()
            spin.setRange(1, 9999)
            spin.setValue(max(1, entry.copies))
            self.table.setCellWidget(row, 3, spin)
            self.table.setRowHeight(row, 36)
        checked = self._count_checked()
        total = sum(
            int(self.table.cellWidget(r, 3).value())
            for r in range(self.table.rowCount())
            if self.table.item(r, 0) and self.table.item(r, 0).checkState() == Qt.Checked
        )
        self.count_label.setText(f"{checked} renglones · {total} etiquetas")

    def _count_checked(self) -> int:
        return sum(
            1
            for r in range(self.table.rowCount())
            if self.table.item(r, 0) and self.table.item(r, 0).checkState() == Qt.Checked
        )

    def _print_selected(self) -> None:
        labels: list[tuple[str, str, Money, int]] = []
        for r in range(self.table.rowCount()):
            check = self.table.item(r, 0)
            if check is None or check.checkState() != Qt.Checked:
                continue
            entry = self._entries[self._visible()[r]]
            spin = self.table.cellWidget(r, 3)
            copies = int(spin.value()) if spin else entry.copies
            labels.append((entry.code, entry.name, entry.price, copies))
        if not labels:
            QMessageBox.information(self, "Selección", "No hay etiquetas seleccionadas.")
            return
        try:
            total = self._service.print_batch(labels)
        except Exception:  # noqa: BLE001 - la impresora no debe romper el diálogo
            QMessageBox.warning(self, "Etiquetas", "No se pudo enviar el lote a la impresora.")
            return
        QMessageBox.information(
            self,
            "Etiquetas enviadas",
            f"Se enviaron {total} etiqueta(s) a la impresora.\n"
            "Si no salen físicamente, revisá la impresora y el papel.",
        )
        self.accept()