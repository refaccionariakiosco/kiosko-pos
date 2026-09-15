"""Vista de gestión de productos (CRUD + etiquetas)."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import CommandBus, QueryBus
from app.application.commands import (
    CreateProductCommand,
    SetProductActiveCommand,
    UpdateProductCommand,
)
from app.application.queries import GetCatalogQuery, GetCategoriesQuery
from app.application.read_models import ProductDTO
from app.domain.exceptions import DomainError
from app.infrastructure.printing.label_printer import LabelPrintingService
from app.interface.dialogs import ProductDialog, show_domain_error
from app.interface.label_dialog import LabelPrintDialog
from app.interface.widgets import make_label, make_table, search_matches

STATE_COLORS = {
    "SIN STOCK": QColor("#9ca3af"),
    "STOCK BAJO": QColor("#d97706"),
    "ACTIVO": QColor("#16a34a"),
}

TABLE_CAP = 500


class ProductsView(QWidget):
    def __init__(self, commands: CommandBus, queries: QueryBus, labels: LabelPrintingService):
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._labels = labels
        self._cardex_navigator = None
        self._products: list[ProductDTO] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        layout.addWidget(make_label("Productos", object_name="pageTitle"))

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar por nombre o código…")
        self.search.textChanged.connect(lambda _: self._render())
        new_btn = QPushButton("Nuevo")
        edit_btn = QPushButton("Editar")
        toggle_btn = QPushButton("Activar / Desactivar")
        label_btn = QPushButton("Imprimir etiqueta")
        for btn in (new_btn, edit_btn, toggle_btn, label_btn):
            btn.setObjectName("ghost")
        new_btn.clicked.connect(self._create)
        edit_btn.clicked.connect(self._edit)
        toggle_btn.clicked.connect(self._toggle_active)
        label_btn.clicked.connect(self._print_label)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(new_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(toggle_btn)
        toolbar.addWidget(label_btn)
        layout.addLayout(toolbar)

        self.table = make_table(
            ["Código", "Nombre", "Categoría", "Precio", "Costo", "Stock", "Min.", "Estado"],
            stretch_column=1,
        )
        self.table.cellDoubleClicked.connect(self._open_cardex)
        layout.addWidget(self.table, 1)
        self.count_label = make_label("", object_name="muted")
        layout.addWidget(self.count_label)

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._products = self._queries.ask(GetCatalogQuery(include_inactive=True))
        self._render()

    def _filtered(self) -> list[ProductDTO]:
        term = self.search.text().strip().lower()
        if not term:
            return self._products
        return [p for p in self._products if search_matches(term, p.name, p.code)]

    def _render(self) -> None:
        products = self._filtered()
        limited = len(products) > TABLE_CAP
        shown = products if not limited else products[:TABLE_CAP]
        self.table.setRowCount(0)
        for product in shown:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(product.code))
            name_item = QTableWidgetItem(product.name)
            if not product.active:
                name_item.setForeground(STATE_COLORS["SIN STOCK"])
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, QTableWidgetItem(product.category_name))
            self.table.setItem(row, 3, QTableWidgetItem(product.unit_price.format()))
            self.table.setItem(row, 4, QTableWidgetItem(product.cost.format() if product.cost else "-"))
            self.table.setItem(row, 5, QTableWidgetItem(str(product.stock)))
            self.table.setItem(row, 6, QTableWidgetItem(str(product.min_stock)))
            state = "INACTIVO" if not product.active else ("SIN STOCK" if product.out_of_stock else "STOCK BAJO" if product.low_stock else "ACTIVO")
            state_item = QTableWidgetItem(state)
            state_item.setForeground(STATE_COLORS.get(state, "#000000"))
            self.table.setItem(row, 7, state_item)
        if limited:
            self.count_label.setText(f"Mostrando {len(shown)} de {len(products)} productos (filtrá para ver el resto)")
        else:
            self.count_label.setText(f"{len(products)} productos")

    def _selected(self) -> ProductDTO | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._filtered()):
            return None
        return self._filtered()[row]

    def set_cardex_navigator(self, callback) -> None:
        self._cardex_navigator = callback

    def _open_cardex(self, row: int, _column: int) -> None:
        products = self._filtered()
        if not (0 <= row < len(products)) or self._cardex_navigator is None:
            return
        self._cardex_navigator(products[row].id)

    def _categories(self) -> list[tuple[int, str]]:
        return [(c.id, c.name) for c in self._queries.ask(GetCategoriesQuery())]

    # ------------------------------------------------------------------ #

    def _create(self) -> None:
        dialog = ProductDialog(self._categories(), None, self)
        if dialog.exec() != ProductDialog.Accepted:
            return
        values = dialog.values()
        try:
            self._commands.execute(
                CreateProductCommand(
                    code=values["code"],
                    name=values["name"],
                    unit_price=values["unit_price"],
                    category_id=values["category_id"],
                    cost=values["cost"],
                    stock=values["stock"],
                    min_stock=values["min_stock"],
                    description=values["description"],
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self.refresh()

    def _edit(self) -> None:
        product = self._selected()
        if product is None:
            QMessageBox.information(self, "Selección", "Seleccione un producto para editar.")
            return
        dialog = ProductDialog(self._categories(), product, self)
        if dialog.exec() != ProductDialog.Accepted:
            return
        values = dialog.values()
        try:
            self._commands.execute(
                UpdateProductCommand(
                    product_id=product.id,
                    code=values["code"],
                    name=values["name"],
                    unit_price=values["unit_price"],
                    category_id=values["category_id"],
                    cost=values["cost"],
                    min_stock=values["min_stock"],
                    description=values["description"],
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self.refresh()

    def _toggle_active(self) -> None:
        product = self._selected()
        if product is None:
            QMessageBox.information(self, "Selección", "Seleccione un producto.")
            return
        try:
            self._commands.execute(SetProductActiveCommand(product_id=product.id, active=not product.active))
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self.refresh()

    def _print_label(self) -> None:
        product = self._selected()
        if product is None:
            QMessageBox.information(self, "Selección", "Seleccione un producto para imprimir su etiqueta.")
            return
        LabelPrintDialog(product, self._labels, self).exec()