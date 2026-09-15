"""Pantalla de cardex: detalle del producto y su historial de movimientos con folios."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import QueryBus
from app.application.queries import GetCatalogQuery, GetStockMovementsQuery
from app.application.read_models import ProductDTO, StockMovementDTO
from app.interface.cardex_core import _CAPTIONS, _STATE_COLORS, compute_cardex_rows
from app.interface.widgets import make_label, make_table, search_matches


class CardexView(QWidget):
    """Pantalla independiente con el detalle y todos los movimientos de un producto."""

    def __init__(self, queries: QueryBus):
        super().__init__()
        self._queries = queries
        self._products: list[ProductDTO] = []
        self._movements: list[StockMovementDTO] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        layout.addWidget(make_label("Cardex", object_name="pageTitle"))

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar producto por nombre o código…")
        self.search.textChanged.connect(self._filter_combo)
        self.product_combo = QComboBox()
        self.product_combo.setMinimumWidth(460)
        consult_btn = QPushButton("Consultar")
        consult_btn.setObjectName("accent")
        consult_btn.clicked.connect(self._load_selected)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.product_combo, 2)
        toolbar.addWidget(consult_btn)
        layout.addLayout(toolbar)

        self._fields: dict[str, QLabel] = {}
        detail_grid = QGridLayout()
        detail_grid.setHorizontalSpacing(26)
        for column, caption in enumerate(_CAPTIONS):
            detail_grid.addWidget(make_label(caption, object_name="muted"), 0, column)
            value = QLabel("-")
            value.setStyleSheet("font-weight: 700; color: #111827;")
            self._fields[caption] = value
            detail_grid.addWidget(value, 1, column)
        layout.addLayout(detail_grid)

        self.table = make_table(["Fecha", "Concepto", "Entrada", "Salida", "Saldo", "Documento"], stretch_column=1)
        layout.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.summary_label = make_label("", object_name="muted")
        self.count_label = make_label("", object_name="muted")
        footer.addWidget(self.summary_label)
        footer.addStretch(1)
        footer.addWidget(self.count_label)
        layout.addLayout(footer)

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._products = self._queries.ask(GetCatalogQuery(include_inactive=True))
        self._reload_combo(prefer_id=self.product_combo.currentData())

    def select_product(self, product_id: int) -> None:
        self.search.clear()
        if self.product_combo.findData(product_id) < 0:
            self._products = self._queries.ask(GetCatalogQuery(include_inactive=True))
            self._reload_combo(prefer_id=product_id)
        else:
            self.product_combo.setCurrentIndex(self.product_combo.findData(product_id))
        self._load_selected()

    # ------------------------------------------------------------------ #

    def _filter_combo(self, term: str) -> None:
        term = (term or "").strip().lower()
        current = self.product_combo.currentData()
        products = [p for p in self._products if not term or search_matches(term, p.name, p.code)]
        self._reload_combo(products, prefer_id=current)

    def _reload_combo(self, products: list[ProductDTO] | None = None, prefer_id: int | None = None) -> None:
        products = self._products if products is None else products
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        for product in products:
            self.product_combo.addItem(f"{product.code} — {product.name}", product.id)
        index = self.product_combo.findData(prefer_id) if prefer_id is not None else -1
        if index < 0 and self.product_combo.count() > 0:
            index = 0
        self.product_combo.setCurrentIndex(index)
        self.product_combo.blockSignals(False)
        self._load_selected()

    def _load_selected(self) -> None:
        product_id = self.product_combo.currentData()
        if product_id is None:
            self._movements = []
            self._render_product(None)
            return
        self._movements = self._queries.ask(GetStockMovementsQuery(product_id=product_id, limit=10000))
        product = next((p for p in self._products if p.id == product_id), None)
        self._render_product(product)

    # ------------------------------------------------------------------ #

    def _render_product(self, product: ProductDTO | None) -> None:
        if product is None:
            for caption in _CAPTIONS:
                self._fields[caption].setText("-")
                self._fields[caption].setStyleSheet("font-weight: 700; color: #111827;")
            self.table.setRowCount(0)
            self.summary_label.setText("Seleccione un producto para ver su cardex.")
            self.count_label.setText("")
            return

        self._fields["Código"].setText(product.code)
        self._fields["Nombre"].setText(product.name)
        self._fields["Categoría"].setText(product.category_name)
        self._fields["Precio"].setText(product.unit_price.format())
        self._fields["Costo"].setText(product.cost.format() if product.cost else "-")
        self._fields["Stock"].setText(str(product.stock))
        self._fields["Mín."].setText(str(product.min_stock))
        state = (
            "INACTIVO"
            if not product.active
            else "SIN STOCK"
            if product.out_of_stock
            else "STOCK BAJO"
            if product.low_stock
            else "ACTIVO"
        )
        value = self._fields["Estado"]
        value.setText(state)
        value.setStyleSheet(f"font-weight: 700; color: {_STATE_COLORS.get(state, '#111827')};")

        rows = compute_cardex_rows(self._movements, product.stock)
        self.table.setRowCount(0)
        for row in rows:
            index = self.table.rowCount()
            self.table.insertRow(index)
            self.table.setItem(index, 0, QTableWidgetItem(row.created_at.strftime("%d/%m/%Y %H:%M")))
            item = QTableWidgetItem(row.concept)
            item.setToolTip(row.note)
            self.table.setItem(index, 1, item)
            self.table.setItem(index, 2, QTableWidgetItem(str(row.entrada) if row.entrada else ""))
            self.table.setItem(index, 3, QTableWidgetItem(str(row.salida) if row.salida else ""))
            self.table.setItem(index, 4, QTableWidgetItem(str(row.saldo)))
            self.table.setItem(index, 5, QTableWidgetItem(row.document or "—"))

        entrada_total = sum(row.entrada for row in rows)
        salida_total = sum(row.salida for row in rows)
        if rows:
            self.summary_label.setText(f"Total entradas {entrada_total} · total salidas {salida_total} · saldo {rows[-1].saldo}")
        else:
            self.summary_label.setText("Sin movimientos registrados.")
        self.count_label.setText(f"{len(self._movements)} movimientos")