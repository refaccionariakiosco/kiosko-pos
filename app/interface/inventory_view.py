"""Vista de inventario: nivel de stock, ajustes y auditoría de movimientos."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
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
            self.table.setItem(row, 0, QTableWidgetItem(product.code))
            self.table.setItem(row, 1, QTableWidgetItem(product.name))
            stock_item = QTableWidgetItem(str(product.stock))
            if product.out_of_stock or product.low_stock:
                stock_item.setForeground(QColor("#b91c1c") if product.out_of_stock else QColor("#d97706"))
            self.table.setItem(row, 2, stock_item)
            self.table.setItem(row, 3, QTableWidgetItem(str(product.min_stock)))
            state = "SIN STOCK" if product.out_of_stock else ("STOCK BAJO" if product.low_stock else "OK")
            state_item = QTableWidgetItem(state)
            self.table.setItem(row, 4, state_item)
        if limited:
            self.count_label.setText(f"Mostrando {len(shown)} de {len(products)} productos")
        else:
            self.count_label.setText(f"{len(products)} productos")

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