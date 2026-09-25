"""Vista de inventario: nivel de stock, ajustes y auditoría de movimientos."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import CommandBus, QueryBus
from app.application.commands import AdjustStockCommand
from app.application.queries import GetCatalogQuery, GetStockMovementsQuery
from app.application.read_models import ProductDTO
from app.domain.exceptions import DomainError
from app.interface.dialogs import AdjustStockDialog, MovementsDialog, show_domain_error
from app.interface.widgets import make_label, make_table, search_matches

TABLE_CAP = 500
STOCK_COLUMN = 2
REASON_COUNT = "CONTEO"
STOCK_HINT = "Doble clic para fijar la cantidad."

log = logging.getLogger(__name__)


def _read_only_item(text: str) -> QTableWidgetItem:
    """Ítem de solo lectura: el delegado inline debe quedar sólo en Stock."""
    item = QTableWidgetItem(str(text))
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    return item


class StockSpinDelegate(QStyledItemDelegate):
    """Editor inline de la columna Stock: spin box acotado a enteros >= 0.

    Se emite ``stockEdited(fila, cantidad)`` al cerrar la edición, no en cada
    tecla, para no disparar un ajuste por cada dígito tecleado.
    """

    stockEdited = Signal(int, int)

    def createEditor(self, parent, option, index):  # noqa: N802 - firma de Qt
        editor = QSpinBox(parent)
        editor.setRange(0, 1_000_000)
        editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        editor.setKeyboardTracking(False)
        editor.valueChanged.connect(lambda value: self.stockEdited.emit(index.row(), value))
        return editor

    def setEditorData(self, editor, index):  # noqa: N802 - firma de Qt
        try:
            editor.setValue(int(index.data(Qt.EditRole) or 0))
        except (TypeError, ValueError):
            editor.setValue(0)
        editor.selectAll()

    def setModelData(self, editor, model, index):  # noqa: N802 - firma de Qt
        model.setData(index, editor.value(), Qt.EditRole)


class InventoryView(QWidget):
    def __init__(self, commands: CommandBus, queries: QueryBus):
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._products: list[ProductDTO] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        layout.addWidget(make_label("Inventario", object_name="pageTitle"))

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar producto por nombre o código…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._render())
        self.low_only = QCheckBox("Solo stock bajo / agotado")
        self.low_only.stateChanged.connect(lambda _: self._render())
        adjust_btn = QPushButton("Ajustar stock")
        movements_btn = QPushButton("Movimientos")
        for btn in (adjust_btn, movements_btn):
            btn.setObjectName("ghost")
        adjust_btn.clicked.connect(self._adjust_stock)
        movements_btn.clicked.connect(self._show_movements)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.low_only)
        toolbar.addStretch(1)
        toolbar.addWidget(movements_btn)
        toolbar.addWidget(adjust_btn)
        layout.addLayout(toolbar)

        self.table = make_table(
            ["Código", "Producto", "Stock", "Stock mínimo", "Estado"],
            stretch_column=1,
        )
        # La única columna editable es Stock: el resto queda como lectura.
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.stock_delegate = StockSpinDelegate(self.table)
        self.table.setItemDelegateForColumn(STOCK_COLUMN, self.stock_delegate)
        self.stock_delegate.stockEdited.connect(self._on_stock_edited)
        self.table.horizontalHeaderItem(STOCK_COLUMN).setToolTip(
            "Doble clic (o F2) para fijar la cantidad en inventario."
        )
        layout.addWidget(self.table, 1)
        self.count_label = make_label("", object_name="muted")
        layout.addWidget(self.count_label)

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._products = self._queries.ask(GetCatalogQuery(include_inactive=False))
        self._render()

    def _visible(self) -> list[ProductDTO]:
        term = self.search.text().strip()
        products = list(self._products)
        if term:
            products = [p for p in products if search_matches(term, p.name, p.code)]
        if self.low_only.isChecked():
            products = [p for p in products if p.low_stock or p.out_of_stock]
        return products

    def _render(self) -> None:
        products = self._visible()
        limited = len(products) > TABLE_CAP
        shown = products if not limited else products[:TABLE_CAP]
        self.table.setRowCount(0)
        for product in shown:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, _read_only_item(product.code))
            self.table.setItem(row, 1, _read_only_item(product.name))
            stock_item = QTableWidgetItem(str(product.stock))
            # La fila se resuelve por id, no por índice: el buscador puede reordenar.
            stock_item.setData(Qt.UserRole, product.id)
            stock_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            stock_item.setToolTip(STOCK_HINT)
            if product.out_of_stock or product.low_stock:
                stock_item.setForeground(QColor("#b91c1c") if product.out_of_stock else QColor("#d97706"))
            self.table.setItem(row, STOCK_COLUMN, stock_item)
            self.table.setItem(row, 3, _read_only_item(str(product.min_stock)))
            state = "SIN STOCK" if product.out_of_stock else ("STOCK BAJO" if product.low_stock else "OK")
            state_item = _read_only_item(state)
            if product.out_of_stock:
                state_item.setForeground(QColor("#b91c1c"))
            elif product.low_stock:
                state_item.setForeground(QColor("#d97706"))
            self.table.setItem(row, 4, state_item)
        if limited:
            self.count_label.setText(f"Mostrando {len(shown)} de {len(products)} productos")
        else:
            self.count_label.setText(f"{len(products)} productos")

    def _on_stock_edited(self, row: int, new_stock: int) -> None:
        """Fija la cantidad contada de una fila y lo asienta como movimiento.

        El inventario local es la suma de deltas, así que editar la cantidad se
        traduce en un ``delta = nueva - actual`` que deja rastro en el cardex y
        se replica al hub como cualquier otro ajuste.
        """
        item = self.table.item(row, STOCK_COLUMN)
        if item is None:
            return
        product_id = item.data(Qt.UserRole)
        product = next((p for p in self._visible() if p.id == product_id), None)
        if product is None or new_stock == product.stock:
            return
        previous = product.stock
        try:
            self._commands.execute(
                AdjustStockCommand(
                    product_id=product.id,
                    delta=new_stock - previous,
                    reason=REASON_COUNT,
                    note=f"Conteo: {previous} -> {new_stock}",
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            self.refresh()
            return
        self.refresh()
        log.info("Conteo inline: %s pasó de %d a %d (%+d).", product.name, previous, new_stock, new_stock - previous)

    def _selected(self) -> ProductDTO | None:
        row = self.table.currentRow()
        visible = self._visible()
        if row < 0 or row >= len(visible):
            return None
        return visible[row]

    # ------------------------------------------------------------------ #

    def _adjust_stock(self) -> None:
        product = self._selected()
        if product is None:
            QMessageBox.information(self, "Selección", "Seleccione un producto para ajustar su stock.")
            return
        dialog = AdjustStockDialog(product, self)
        if dialog.exec() != AdjustStockDialog.Accepted:
            return
        delta, reason, note = dialog.values()
        try:
            self._commands.execute(
                AdjustStockCommand(product_id=product.id, delta=delta, reason=reason, note=note)
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self.refresh()

    def _show_movements(self) -> None:
        product = self._selected()
        if product is None:
            QMessageBox.information(self, "Selección", "Seleccione un producto para ver sus movimientos.")
            return
        movements = self._queries.ask(GetStockMovementsQuery(product_id=product.id))
        MovementsDialog(f"Movimientos: {product.name}", movements, self).exec()