"""Diálogos del área de proveedores: alta, importe mapeado, artículos y pedido."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LIST_CAP = 500

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import CommandBus, QueryBus
from app.application.commands import (
    CreateProductCommand,
    CreateProviderCommand,
    DeletePurchaseOrderCommand,
    ImportedOrderLineRequest,
    ImportProviderItemsCommand,
    LoadPurchaseOrderCommand,
    ProviderItemRequest,
    PurchaseOrderLineRequest,
    ReceiveLineRequest,
    ReceivePurchaseOrderCommand,
    SavePurchaseOrderCommand,
    UpdateProviderCommand,
)
from app.application.queries import (
    GetCatalogQuery,
    GetCategoriesQuery,
    ListProviderItemsQuery,
    ListProvidersQuery,
    ListPurchaseOrdersQuery,
)
from app.application.read_models import (
    ProductDTO,
    ProviderDTO,
    ProviderItemDTO,
    PurchaseLineDTO,
    PurchaseOrderDTO,
)
from app.domain.entities import PurchaseOrder
from app.domain.exceptions import DomainError
from app.domain.value_objects import Money
from app.infrastructure.exporters.purchase_list import write_purchase_list_excel
from app.infrastructure.importers.order_excel import parse_order_excel
from app.infrastructure.importers.order_pdf import OrderPdfLine, OrderPdfResult, parse_order_pdf
from app.infrastructure.importers.provider_list import (
    guess_column,
    preview_worksheet,
    read_provider_items_xlsx,
)
from app.infrastructure.printers.receipt import render_purchase_list_html
from app.infrastructure.printing.label_printer import LabelPrintingService
from app.interface.dialogs import ProductDialog, ReceiptDialog, show_domain_error
from app.interface.label_batch_dialog import LabelBatchDialog, LabelBatchEntry
from app.interface.widgets import make_label, make_table, search_matches
from app.settings import Settings, StoreInfo

log = logging.getLogger(__name__)


class ProviderDialog(QDialog):
    """Alta o edición de un proveedor (el área no toca el inventario)."""

    def __init__(self, commands: CommandBus, parent=None, provider: ProviderDTO | None = None):
        super().__init__(parent)
        self._commands = commands
        self._provider = provider
        self.setWindowTitle("Editar proveedor" if provider else "Nuevo proveedor")
        self.setMinimumWidth(420)

        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Nombre del proveedor")
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("Teléfono (opcional)")
        self.note = QLineEdit()
        self.note.setPlaceholderText("Nota (opcional)")
        form.addRow("Nombre:", self.name)
        form.addRow("Teléfono:", self.phone)
        form.addRow("Nota:", self.note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Guardar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(make_label(self.windowTitle(), object_name="pageTitle"))
        layout.addSpacing(4)
        layout.addLayout(form)
        layout.addWidget(buttons)
        if provider:
            self.name.setText(provider.name)
            self.phone.setText(provider.phone)
            self.note.setText(provider.note)
        self.name.setFocus()

    def _save(self) -> None:
        try:
            if self._provider is not None:
                self._commands.execute(
                    UpdateProviderCommand(
                        provider_id=self._provider.id,
                        name=self.name.text(),
                        phone=self.phone.text(),
                        note=self.note.text(),
                    )
                )
            else:
                self._commands.execute(
                    CreateProviderCommand(name=self.name.text(), phone=self.phone.text(), note=self.note.text())
                )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        QMessageBox.information(self, "Proveedor guardado", f"Proveedor “{self.name.text().strip()}” guardado.")
        self.accept()


class ProviderItemsDialog(QDialog):
    """Lista los artículos (código/descripción/precio) importados de un proveedor."""

    def __init__(self, queries: QueryBus, provider: ProviderDTO, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._provider = provider
        self.setWindowTitle(f"Artículos — {provider.name}")
        self.resize(680, 460)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(make_label(provider.name, object_name="pageTitle"))
        layout.addWidget(
            make_label(
                f"Lista de precios para cotizar · {provider.item_count} artículos",
                object_name="muted",
            )
        )

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por descripción o código…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._render())
        toolbar.addWidget(self.search, 1)
        layout.addLayout(toolbar)

        self.table = make_table(
            ["Código", "Descripción", "Precio"],
            stretch_column=1,
        )
        layout.addWidget(self.table, 1)

        self.count_label = make_label("", object_name="muted")
        layout.addWidget(self.count_label)

        close_btn = QPushButton("Cerrar")
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._items = self._queries.ask(ListProviderItemsQuery(provider_id=provider.id))
        self._render()

    def _visible(self) -> list[ProviderItemDTO]:
        term = self.search.text().strip()
        if not term:
            return self._items
        return [i for i in self._items if search_matches(term, i.description, i.code)]

    def _render(self) -> None:
        items = self._visible()
        self.table.setRowCount(0)
        shown = items[:LIST_CAP]
        for item in shown:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(item.code))
            self.table.setItem(row, 1, QTableWidgetItem(item.description))
            self.table.setItem(row, 2, QTableWidgetItem(item.price.format()))
        if len(items) > LIST_CAP:
            self.count_label.setText(
                f"Mostrando {LIST_CAP} de {len(items)} artículos — escriba en el buscador para filtrar"
            )
        else:
            self.count_label.setText(f"{len(items)} artículos")
        self.table.resizeColumnToContents(0)


class ProviderImportDialog(QDialog):
    """Importa la lista del proveedor mapeando qué columna es cada campo."""

    FIELDS = (("code", "Código"), ("description", "Descripción"), ("price", "Precio"))

    def __init__(self, commands: CommandBus, queries: QueryBus, provider: ProviderDTO, parent=None):
        super().__init__(parent)
        self._commands = commands
        self._queries = queries
        self._provider = provider
        self._path = ""
        self.setWindowTitle(f"Importar artículos — {provider.name}")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(make_label(f"Importar artículos — {provider.name}", object_name="pageTitle"))
        layout.addWidget(
            make_label(
                "Elige el archivo .xlsx y mapea las columnas. La lista se guarda solo para cotizar "
                "(no afecta el inventario).",
                object_name="muted",
            )
        )

        file_row = QHBoxLayout()
        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setPlaceholderText("Sin archivo seleccionado")
        browse_btn = QPushButton("Examinar…")
        browse_btn.setObjectName("ghost")
        browse_btn.clicked.connect(self._pick_file)
        file_row.addWidget(self.file_path, 1)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        form = QFormLayout()
        self.mapping = {}
        for key, label in self.FIELDS:
            combo = QComboBox()
            combo.setEnabled(False)
            combo.addItem("— Elegir columna —", None)
            self.mapping[key] = combo
            form.addRow(f"{label}:", combo)
        layout.addLayout(form)

        self.preview_info = make_label("", object_name="muted")
        layout.addWidget(self.preview_info)

        grid_row = QHBoxLayout()
        self.preview_table = make_table([], stretch_column=1)
        self.preview_table.setMinimumHeight(160)
        grid_row.addWidget(self.preview_table, 1)
        layout.addLayout(grid_row, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Importar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        self.import_btn = buttons.button(QDialogButtonBox.Ok)
        self.import_btn.setEnabled(False)
        buttons.accepted.connect(self._import)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Lista del proveedor", "", "Libro de Excel (*.xlsx)")
        if not path:
            return
        try:
            preview = preview_worksheet(path)
        except Exception:  # noqa: BLE001 - archivo inválido
            QMessageBox.warning(self, "Archivo inválido", "No se pudo leer el archivo como .xlsx.")
            log.exception("Importe: archivo inválido.")
            return
        self._path = path
        self.file_path.setText(path)
        columns = preview.columns
        self._headers = {column.letter: column.header for column in columns}
        for key, combo in self.mapping.items():
            combo.clear()
            combo.addItem("— Elegir columna —", None)
            for column in columns:
                combo.addItem(f"{column.letter}: {column.header}", column.letter)
            if columns:
                combo.setEnabled(True)
        if columns:
            guessed = {
                "code": guess_column(columns, code=True),
                "description": guess_column(columns, description=True),
                "price": guess_column(columns, price=True),
            }
            for key, letter in guessed.items():
                if letter:
                    index = self.mapping[key].findData(letter)
                    self.mapping[key].setCurrentIndex(index)
            self.preview_info.setText(
                f"{preview.row_count} filas con datos · columnas detectadas: {len(columns)}"
            )
            self._render_preview(preview)
            self.import_btn.setEnabled(True)

    def _render_preview(self, preview) -> None:
        letters = [column.letter for column in preview.columns]
        self.preview_table.setColumnCount(len(letters))
        self.preview_table.setHorizontalHeaderLabels(
            [f"{letter}: {self._headers.get(letter, '')}" for letter in letters]
        )
        self.preview_table.setRowCount(0)
        for raw in preview.sample[:5]:
            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            for col, letter in enumerate(letters):
                self.preview_table.setItem(row, col, QTableWidgetItem(raw.cells.get(letter, "")))
        self.preview_table.resizeColumnsToContents()

    # ------------------------------------------------------------------ #

    def _mapping_letters(self) -> dict[str, str]:
        return {key: combo.currentData() or "" for key, combo in self.mapping.items()}

    def _import(self) -> None:
        mapping = self._mapping_letters()
        if not self._path or not all(mapping.values()):
            QMessageBox.warning(self, "Mapeo incompleto", "Elige una columna para Código, Descripción y Precio.")
            return
        if len(set(mapping.values())) < 3:
            QMessageBox.warning(self, "Mapeo inválido", "Código, Descripción y Precio deben ser columnas distintas.")
            return
        try:
            rows, skipped = read_provider_items_xlsx(
                self._path,
                code_col=mapping["code"],
                description_col=mapping["description"],
                price_col=mapping["price"],
            )
        except Exception:  # noqa: BLE001 - archivo inválido
            QMessageBox.warning(self, "Archivo inválido", "No se pudo leer la lista del archivo .xlsx.")
            log.exception("Importe: lectura del archivo fallida.")
            return
        if not rows:
            QMessageBox.warning(
                self,
                "Sin artículos",
                "No se encontraron artículos válidos con el mapeo elegido.",
            )
            return
        if self._provider.item_count > 0:
            answer = QMessageBox.question(
                self,
                "Reemplazar lista",
                f"El proveedor ya tiene {self._provider.item_count} artículos.\n"
                f"¿Reemplazarlos con los {len(rows)} importados?",
            )
            if answer != QMessageBox.Yes:
                return
        try:
            total = self._commands.execute(
                ImportProviderItemsCommand(
                    provider_id=self._provider.id,
                    items=tuple(
                        ProviderItemRequest(code=row.code, description=row.description, price=str(row.price))
                        for row in rows
                    ),
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        QMessageBox.information(
            self,
            "Lista importada",
            f"Se importaron {total} artículos para {self._provider.name}."
            + (f"\nSe omitieron {skipped} filas sin datos." if skipped else ""),
        )
        self.accept()


@dataclass
class _CartLine:
    item: ProviderItemDTO
    quantity: int


class PurchaseListDialog(QDialog):
    """Arma una lista de compra 100% manual y la imprime como ticket.

    Los artículos se eligen de las listas de precios ya importadas; no usa
    existencias del inventario ni afecta stock.
    """

    def __init__(
        self,
        commands: CommandBus,
        queries: QueryBus,
        store: StoreInfo | None = None,
        parent=None,
        provider: ProviderDTO | None = None,
        settings: Settings | None = None,
    ):
        super().__init__(parent)
        self._commands = commands
        self._queries = queries
        self._store = store or StoreInfo()
        self._settings = settings or Settings()
        self._provider = provider
        self._providers = queries.ask(ListProvidersQuery())
        self._available: list[ProviderItemDTO] = []
        self._cart: list[_CartLine] = []
        self.setWindowTitle("Lista de compra (pedido)")
        self.resize(1280, 620)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(make_label("Lista de compra", object_name="pageTitle"))
        layout.addWidget(
            make_label("Armada a mano desde las listas de precios. El ticket no afecta el inventario.", object_name="muted")
        )

        split = QHBoxLayout()
        split.addWidget(self._build_available_card(), 3)
        split.addWidget(self._build_cart_card(), 2)
        layout.addLayout(split, 1)

        buttons = QHBoxLayout()
        close_btn = QPushButton("Cerrar")
        close_btn.setObjectName("ghost")
        close_btn.clicked.connect(self.accept)
        clear_btn = QPushButton("Limpiar")
        clear_btn.setObjectName("ghost")
        clear_btn.clicked.connect(self._clear_cart)
        self.print_btn = QPushButton("Imprimir ticket")
        self.print_btn.setObjectName("primary")
        self.print_btn.clicked.connect(self._print_ticket)
        self.export_btn = QPushButton("Exportar a Excel")
        self.export_btn.setObjectName("ghost")
        self.export_btn.clicked.connect(self._export_excel)
        self.save_order_btn = QPushButton("Guardar pedido")
        self.save_order_btn.setObjectName("ghost")
        self.save_order_btn.clicked.connect(self._save_order)
        self.orders_btn = QPushButton("Pedidos")
        self.orders_btn.setObjectName("accent")
        self.orders_btn.setToolTip("Ver los pedidos del proveedor seleccionado")
        self.orders_btn.clicked.connect(self._provider_orders)
        self.receive_btn = QPushButton("Recibir pedido a inventario")
        self.receive_btn.setObjectName("primary")
        self.receive_btn.setEnabled(False)
        self.receive_btn.clicked.connect(self._receive_order)
        buttons.addWidget(close_btn)
        buttons.addStretch(1)
        buttons.addWidget(clear_btn)
        buttons.addWidget(self.save_order_btn)
        buttons.addWidget(self.export_btn)
        buttons.addWidget(self.print_btn)
        buttons.addWidget(self.orders_btn)
        buttons.addWidget(self.receive_btn)
        layout.addLayout(buttons)

        self._refresh_available()

    # ------------------------------------------------------------------ #

    def _build_available_card(self) -> QWidget:
        card = QWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(8)
        card_layout.addWidget(make_label("Artículos disponibles (listas de precios)", object_name="sectionTitle"))

        filter_row = QHBoxLayout()
        filter_row.addWidget(make_label("Proveedor:", object_name="muted"))
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Todos los proveedores", None)
        for provider in self._providers:
            self.provider_combo.addItem(provider.name, provider.id)
        if self._provider is not None:
            index = self.provider_combo.findData(self._provider.id)
            if index > 0:
                self.provider_combo.setCurrentIndex(index)
        self.provider_combo.currentIndexChanged.connect(lambda _: self._refresh_available())
        filter_row.addWidget(self.provider_combo, 1)
        card_layout.addLayout(filter_row)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar artículo…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._render_available())
        card_layout.addWidget(self.search)
        self.available = make_table(
            ["Proveedor", "Descripción", "Código", "Precio", "Cant", ""],
            stretch_column=1,
        )
        self.available.setMinimumHeight(300)
        header = self.available.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.available.setColumnWidth(0, 130)
        self.available.setColumnWidth(2, 90)
        self.available.setColumnWidth(3, 90)
        self.available.setColumnWidth(4, 70)
        self.available.setColumnWidth(5, 84)
        card_layout.addWidget(self.available, 1)
        self.available_hint = make_label("", object_name="muted")
        card_layout.addWidget(self.available_hint)
        return card

    def _build_cart_card(self) -> QWidget:
        card = QWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(8)
        card_layout.addWidget(make_label("Pedido", object_name="sectionTitle"))
        self.cart = make_table(
            ["Proveedor", "Descripción", "Código", "Precio", "Cant", "Subtotal", ""],
            stretch_column=1,
        )
        self.cart.setMinimumHeight(300)
        header = self.cart.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.cart.setColumnWidth(0, 130)
        self.cart.setColumnWidth(2, 90)
        self.cart.setColumnWidth(3, 90)
        self.cart.setColumnWidth(4, 70)
        self.cart.setColumnWidth(5, 90)
        self.cart.setColumnWidth(6, 84)
        card_layout.addWidget(self.cart, 1)
        self.total_label = make_label("", object_name="totalAmount")
        card_layout.addWidget(self.total_label, alignment=Qt.AlignRight)
        return card

    # ------------------------------------------------------------------ #

    def _refresh_available(self) -> None:
        provider_id = self._provider_combo_id()
        if provider_id is None:
            self._available = self._queries.ask(ListProviderItemsQuery())
        else:
            self._available = self._queries.ask(ListProviderItemsQuery(provider_id=provider_id))
        self._render_available()

    def _provider_combo_id(self) -> int | None:
        return self.provider_combo.currentData()

    def _provider_orders(self) -> None:
        provider_id = self._provider_combo_id()
        if provider_id is None:
            QMessageBox.information(
                self,
                "Selección",
                "Elija un proveedor en el filtro superior para ver sus pedidos.",
            )
            return
        provider = next((p for p in self._providers if p.id == provider_id), None)
        if provider is None:
            return
        dialog = ProviderOrdersDialog(
            self._commands, self._queries, provider, self._store, self, settings=self._settings
        )
        dialog.exec()

    def _visible_available(self) -> list[ProviderItemDTO]:
        term = self.search.text().strip()
        if not term:
            return self._available
        return [
            i for i in self._available if search_matches(term, i.description, i.code, i.provider_name)
        ]

    def _render_available(self) -> None:
        items = self._visible_available()
        self.available.setRowCount(0)
        shown = items[:LIST_CAP]
        for item in shown:
            row = self.available.rowCount()
            self.available.insertRow(row)
            self.available.setItem(row, 0, QTableWidgetItem(item.provider_name))
            self.available.setItem(row, 1, QTableWidgetItem(item.description))
            self.available.setItem(row, 2, QTableWidgetItem(item.code))
            self.available.setItem(row, 3, QTableWidgetItem(item.price.format()))
            qty = QSpinBox()
            qty.setRange(1, 9999)
            qty.setValue(1)
            self.available.setCellWidget(row, 4, qty)
            add_btn = QPushButton("+")
            add_btn.setObjectName("addButton")
            add_btn.setToolTip("Agregar al pedido")
            add_btn.clicked.connect(lambda _=False, i=item.id, q=qty: self._add_to_cart(i, q))
            self.available.setCellWidget(row, 5, add_btn)
            self.available.setRowHeight(row, 38)
        if len(items) > LIST_CAP:
            self.available_hint.setText(
                f"Mostrando {LIST_CAP} de {len(items)} artículos — escriba en el buscador para filtrar"
            )
        else:
            self.available_hint.setText(f"{len(items)} artículos")
        self.available.resizeColumnToContents(4)
        self.available.resizeColumnToContents(5)

    def _add_to_cart(self, item_id: int, qty_spin: QSpinBox) -> None:
        item = next((i for i in self._available if i.id == item_id), None)
        if item is None:
            return
        existing = next((line for line in self._cart if line.item.id == item_id), None)
        if existing:
            existing.quantity += qty_spin.value()
        else:
            self._cart.append(_CartLine(item=item, quantity=qty_spin.value()))
        self._render_cart()

    def _render_cart(self) -> None:
        self.cart.setRowCount(0)
        total = Money.zero()
        for line in self._cart:
            subtotal = line.item.price * line.quantity
            total += subtotal
            row = self.cart.rowCount()
            self.cart.insertRow(row)
            self.cart.setItem(row, 0, QTableWidgetItem(line.item.provider_name))
            self.cart.setItem(row, 1, QTableWidgetItem(line.item.description))
            self.cart.setItem(row, 2, QTableWidgetItem(line.item.code))
            self.cart.setItem(row, 3, QTableWidgetItem(line.item.price.format()))
            qty = QSpinBox()
            qty.setRange(1, 9999)
            qty.setValue(line.quantity)
            qty.valueChanged.connect(lambda value, l=line: self._update_qty(l, value))
            self.cart.setCellWidget(row, 4, qty)
            self.cart.setItem(row, 5, QTableWidgetItem((line.item.price * line.quantity).format()))
            remove_btn = QPushButton("Quitar")
            remove_btn.setObjectName("ghost")
            remove_btn.clicked.connect(lambda _=False, l=line: self._remove_from_cart(l))
            self.cart.setCellWidget(row, 6, remove_btn)
            self.cart.setRowHeight(row, 38)
        self.total_label.setText(f"TOTAL: {total.format()}")
        self.cart.resizeColumnToContents(6)
        self.receive_btn.setEnabled(bool(self._cart))
        self.save_order_btn.setEnabled(bool(self._cart))

    def _update_qty(self, line: _CartLine, value: int) -> None:
        line.quantity = value
        self._render_cart()

    def _remove_from_cart(self, line: _CartLine) -> None:
        self._cart.remove(line)
        self._render_cart()

    def _clear_cart(self) -> None:
        self._cart.clear()
        self._render_cart()

    def _order_requests(self) -> tuple:
        return tuple(
            PurchaseOrderLineRequest(
                provider_item_id=line.item.id,
                product_id=line.item.product_id,
                code=line.item.code,
                description=line.item.description,
                provider_name=line.item.provider_name,
                quantity=line.quantity,
                unit_price=line.item.price,
            )
            for line in self._cart
        )

    def _save_order(self) -> None:
        if not self._cart:
            QMessageBox.information(self, "Pedido vacío", "Agregue artículos a la lista antes de guardar.")
            return
        note, ok = QInputDialog.getMultiLineText(
            self,
            "Guardar pedido",
            "Nota del pedido (opcional):\nEl pedido queda pendiente para recibirlo en otra fecha.",
            "",
        )
        if not ok:
            return
        try:
            saved = self._commands.execute(
                SavePurchaseOrderCommand(note=note.strip(), lines=self._order_requests())
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self._clear_cart()
        QMessageBox.information(
            self,
            "Pedido guardado",
            f"Pedido {saved.order_number} guardado como pendiente.\n"
            "Podrá recibirlo más adelante desde “Pedidos guardados” en Proveedores.",
        )

    def _receive_order(self) -> None:
        if not self._cart:
            QMessageBox.information(self, "Pedido vacío", "Agregue artículos a la lista antes de recibir.")
            return
        dialog = ReceiveOrderDialog(self._commands, self._queries, list(self._cart), self, settings=self._settings)
        if dialog.exec() != ReceiveOrderDialog.Accepted:
            return
        received = dialog.received_count()
        self._clear_cart()
        QMessageBox.information(
            self,
            "Recepción registrada",
            f"Se recibieron {received} unidades en inventario.\n"
            "Los artículos quedaron relacionados con sus productos.",
        )

    # ------------------------------------------------------------------ #

    def _lines(self) -> list[PurchaseLineDTO]:
        return [
            PurchaseLineDTO(
                code=line.item.code,
                description=line.item.description,
                provider_name=line.item.provider_name,
                quantity=line.quantity,
                unit_price=line.item.price,
                subtotal=line.item.price * line.quantity,
            )
            for line in self._cart
        ]

    def _print_ticket(self) -> None:
        if not self._cart:
            QMessageBox.information(self, "Pedido vacío", "Agregue artículos a la lista antes de imprimir.")
            return
        try:
            from app.infrastructure.printers.ticket_esc_pos import print_receipt_ticket

            html = render_purchase_list_html(self._lines(), self._store)
            if print_receipt_ticket(html):
                return
            ReceiptDialog(html, self).exec()
        except Exception:  # noqa: BLE001 - el ticket del pedido nunca debe romper el cierre
            log.exception("Lista de compra: no se pudo imprimir el ticket.")

    def _export_excel(self) -> None:
        if not self._cart:
            QMessageBox.information(self, "Pedido vacío", "Agregue artículos a la lista antes de exportar.")
            return
        default_name = f"ListaDeCompra_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar lista de compra",
            str(Path.home() / default_name),
            "Libro Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            target = write_purchase_list_excel(self._lines(), path, self._store)
            QMessageBox.information(self, "Exportado", f"Lista de compra guardada en:\n{target}")
        except Exception:  # noqa: BLE001
            log.exception("Lista de compra: no se pudo exportar a Excel.")
            QMessageBox.critical(self, "Error", "No se pudo exportar la lista a Excel.")


def _normalize_match(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def find_inventory_match(item: ProviderItemDTO, products: list[ProductDTO]) -> ProductDTO | None:
    """Busca el producto del inventario que corresponde a un artículo del pedido.

    1. Si el artículo ya fue relacionado (product_id persistido), se usa ese.
    2. Si no, por igualdad de código.
    3. Si no, por igualdad de descripción contra el nombre del producto.
    Devuelve None cuando no hay coincidencia.
    """
    if item.product_id:
        for product in products:
            if product.id == item.product_id:
                return product
    code = (item.code or "").strip().upper()
    description = _normalize_match(item.description)
    for product in products:
        if code and product.code.upper() == code:
            return product
    if description:
        for product in products:
            if description and _normalize_match(product.name) == description:
                return product
    return None


class ReceiveOrderDialog(QDialog):
    """Recibe un pedido y afecta el inventario.

    Cada renglón del pedido se asocia a un producto del inventario. La
    coincidencia es automática por código/descripción (o por la relación previa);
    si un artículo no coincide, se permite crearlo o relacionarlo a mano. Al
    confirmar, el stock se suma al inventario y la relación queda guardada.
    """

    def __init__(
        self,
        commands: CommandBus,
        queries: QueryBus,
        cart: list[_CartLine],
        parent=None,
        order_id: int | None = None,
        settings: Settings | None = None,
    ):
        super().__init__(parent)
        self._commands = commands
        self._queries = queries
        self._order_id = order_id
        self._settings = settings or Settings()
        self._labels = LabelPrintingService(self._settings, self)
        self._received = 0
        self._catalog: list[ProductDTO] = queries.ask(GetCatalogQuery(include_inactive=False))
        self._rows: list[dict] = []
        for line in cart:
            self._rows.append(
                {
                    "item": line.item,
                    "quantity": max(1, line.quantity),
                    "product": find_inventory_match(line.item, self._catalog),
                }
            )

        self.setWindowTitle("Recepción de pedido")
        self.resize(1120, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(make_label("Recepción de pedido", object_name="pageTitle"))
        layout.addWidget(
            make_label(
                "Cada renglón se asocia a un producto del inventario. Los que coinciden por código o "
                "descripción se relacionan solos; los demás se pueden crear o elegir a mano. "
                "Al confirmar, el stock se suma al inventario.",
                object_name="muted",
            )
        )

        self.table = make_table(
            ["Estado", "Proveedor", "Código", "Descripción", "Cant.", "Producto en inventario", "Acción"],
            stretch_column=3,
        )
        layout.addWidget(self.table, 1)

        self.summary = make_label("", object_name="muted")
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Recibir a inventario")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._receive)
        buttons.rejected.connect(self.reject)

        label_row = QHBoxLayout()
        labels_btn = QPushButton("Imprimir etiquetas")
        labels_btn.setObjectName("accent")
        labels_btn.setToolTip("Imprimir etiquetas de los renglones (con checklist)")
        labels_btn.clicked.connect(self._print_labels)
        label_row.addWidget(labels_btn)
        label_row.addStretch(1)
        label_row.addWidget(buttons)
        layout.addLayout(label_row)

        self._render()
        self._update_summary()

    # ------------------------------------------------------------------ #

    def received_count(self) -> int:
        return self._received

    def _categories(self) -> list[tuple[int, str]]:
        return [(c.id, c.name) for c in self._queries.ask(GetCategoriesQuery())]

    def _render(self) -> None:
        table = self.table
        table.setRowCount(0)
        for idx, row in enumerate(self._rows):
            row_index = table.rowCount()
            table.insertRow(row_index)
            item = row["item"]
            product = row["product"]

            estado = QTableWidgetItem("✓" if product else "—")
            estado.setTextAlignment(Qt.AlignCenter)
            estado.setForeground(QColor("#16a34a") if product else QColor("#b45309"))
            table.setItem(row_index, 0, estado)
            table.setItem(row_index, 1, QTableWidgetItem(item.provider_name))
            table.setItem(row_index, 2, QTableWidgetItem(item.code))
            table.setItem(row_index, 3, QTableWidgetItem(item.description))

            qty = QSpinBox()
            qty.setRange(1, 99999)
            qty.setValue(row["quantity"])
            table.setCellWidget(row_index, 4, qty)

            table.setCellWidget(row_index, 5, self._build_combo(idx))
            self._set_action_cell(row_index)
            table.setRowHeight(row_index, 40)

        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        table.setColumnWidth(0, 56)
        table.setColumnWidth(1, 130)
        table.setColumnWidth(2, 100)
        table.setColumnWidth(4, 80)
        table.setColumnWidth(6, 160)

    def _build_combo(self, row_idx: int) -> QComboBox:
        row = self._rows[row_idx]
        combo = QComboBox()
        combo.addItem("— Sin relación —", None)
        product = row["product"]
        if product is not None:
            combo.addItem(f"{product.code} — {product.name}", product.id)
            combo.setCurrentIndex(1)
        combo.currentIndexChanged.connect(lambda _i, r=row_idx, c=combo: self._on_combo(r, c))
        return combo

    def _set_action_cell(self, row_idx: int) -> None:
        product = self._rows[row_idx]["product"]
        cell = QWidget()
        buttons_row = QHBoxLayout(cell)
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(4)
        if product is None:
            new_btn = QPushButton("＋ Nuevo")
            new_btn.setObjectName("ghost")
            new_btn.setToolTip("Crear el producto en el inventario")
            new_btn.clicked.connect(lambda _=False, r=row_idx: self._create_product(r))
            relate_btn = QPushButton("Relacionar…")
            relate_btn.setObjectName("ghost")
            relate_btn.setToolTip("Buscar y relacionar un producto existente")
            relate_btn.clicked.connect(lambda _=False, r=row_idx: self._pick_product(r))
            buttons_row.addWidget(new_btn)
            buttons_row.addWidget(relate_btn)
        else:
            label = QLabel(f"✓ {product.code}")
            label.setObjectName("muted")
            buttons_row.addWidget(label)
        buttons_row.addStretch(1)
        self.table.setCellWidget(row_idx, 6, cell)

    def _on_combo(self, row_idx: int, combo: QComboBox) -> None:
        product_id = combo.currentData()
        row = self._rows[row_idx]
        row["product"] = next((p for p in self._catalog if p.id == product_id), None) if product_id else None
        estado = self.table.item(row_idx, 0)
        estado.setText("✓" if row["product"] else "—")
        estado.setForeground(QColor("#16a34a") if row["product"] else QColor("#b45309"))
        self._set_action_cell(row_idx)
        self._update_summary()

    def _pick_product(self, row_idx: int) -> None:
        row = self._rows[row_idx]
        dialog = QDialog(self)
        dialog.setWindowTitle("Relacionar producto")
        dialog.resize(540, 480)
        box = QVBoxLayout(dialog)
        search = QLineEdit()
        search.setPlaceholderText("Buscar por nombre o código…")
        box.addWidget(search)
        list_widget = QListWidget()
        box.addWidget(list_widget, 1)

        def _fill(term: str) -> None:
            term = (term or "").strip().lower()
            matches = [p for p in self._catalog if not term or search_matches(term, p.name, p.code)]
            list_widget.clear()
            for product in matches:
                item = QListWidgetItem(f"{product.code} — {product.name}")
                item.setData(Qt.UserRole, product)
                list_widget.addItem(item)

        search.textChanged.connect(_fill)
        _fill("")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return
        current = list_widget.currentItem()
        if current is None or current.data(Qt.UserRole) is None:
            return
        row["product"] = current.data(Qt.UserRole)
        combo = self.table.cellWidget(row_idx, 5)
        if isinstance(combo, QComboBox):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("— Sin relación —", None)
            product = row["product"]
            combo.addItem(f"{product.code} — {product.name}", product.id)
            combo.setCurrentIndex(1)
            combo.blockSignals(False)
        self._set_action_cell(row_idx)
        self._update_summary()

    def _create_product(self, row_idx: int) -> None:
        row = self._rows[row_idx]
        item = row["item"]
        dialog = ProductDialog(self._categories(), None, self)
        dialog.code.setText(item.code)
        dialog.name.setText(item.description)
        dialog.price.setValue(float(item.price.as_decimal()))
        dialog.cost.setValue(float(item.price.as_decimal()))
        if dialog.exec() != ProductDialog.Accepted:
            return
        values = dialog.values()
        try:
            product = self._commands.execute(
                CreateProductCommand(
                    code=values["code"],
                    name=values["name"],
                    unit_price=values["unit_price"],
                    category_id=values["category_id"],
                    cost=values["cost"] or values["unit_price"],
                    stock=0,
                    min_stock=values["min_stock"],
                    description=values["description"],
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self._catalog.append(product)
        row["product"] = product
        self.table.setCellWidget(row_idx, 5, self._build_combo(row_idx))
        self._set_action_cell(row_idx)
        self._update_summary()

    def _update_summary(self) -> None:
        total = len(self._rows)
        matched = sum(1 for row in self._rows if row["product"] is not None)
        units = sum(row["quantity"] for row in self._rows)
        if matched == total:
            self.summary.setText(f"{total} renglones · {units} unidades listas para sumar al inventario.")
        else:
            self.summary.setText(
                f"{matched} de {total} renglones relacionados · "
                f"{total - matched} pendientes (crear el producto o elegirlo de la lista)."
            )

    def _print_labels(self) -> None:
        entries = []
        for row in self._rows:
            item = row["item"]
            product = row["product"]
            copies = max(1, int(row["quantity"]))
            if product is not None:
                entries.append(
                    LabelBatchEntry(code=product.code, name=product.name, price=product.unit_price, copies=copies)
                )
            else:
                entries.append(
                    LabelBatchEntry(code=item.code, name=item.description, price=item.price, copies=copies)
                )
        if not entries:
            return
        LabelBatchDialog(self._labels, entries, self).exec()

    def _receive(self) -> None:
        pending = [row for row in self._rows if row["product"] is None]
        if pending:
            QMessageBox.warning(
                self,
                "Recepción incompleta",
                f"Todavía hay {len(pending)} artículo(s) sin relación.\n"
                "Cree el producto o elija uno del inventario antes de recibir.",
            )
            return
        quantities = [
            int(self.table.cellWidget(row_idx, 4).value()) if self.table.cellWidget(row_idx, 4) else row["quantity"]
            for row_idx, row in enumerate(self._rows)
        ]
        lines = tuple(
            ReceiveLineRequest(
                provider_item_id=row["item"].id,
                product_id=row["product"].id,
                quantity=quantities[idx],
                unit_price=row["item"].price,
            )
            for idx, row in enumerate(self._rows)
        )
        provider_names = ", ".join(sorted({row["item"].provider_name for row in self._rows if row["item"].provider_name}))
        note = "Recepción de pedido" + (f" ({provider_names})" if provider_names else "")
        try:
            self._received = self._commands.execute(
                ReceivePurchaseOrderCommand(note=note, lines=lines, purchase_order_id=self._order_id)
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self.accept()


def _cart_lines_from_order(order: PurchaseOrderDTO) -> list[_CartLine]:
    """Reconstruye un carrito editable a partir de un pedido guardado."""
    lines: list[_CartLine] = []
    for line in order.lines:
        item = ProviderItemDTO(
            id=line.provider_item_id or 0,
            provider_id=0,
            code=line.code,
            description=line.description,
            price=line.unit_price,
            provider_name=line.provider_name,
            product_id=line.product_id,
        )
        lines.append(_CartLine(item=item, quantity=line.quantity))
    return lines


def _purchase_lines_from_order(order: PurchaseOrderDTO) -> list[PurchaseLineDTO]:
    return [
        PurchaseLineDTO(
            code=line.code,
            description=line.description,
            provider_name=line.provider_name,
            quantity=line.quantity,
            unit_price=line.unit_price,
            subtotal=line.subtotal,
        )
        for line in order.lines
    ]


class SavedOrdersDialog(QDialog):
    """Lista los pedidos guardados y permite recibirlos en otra fecha.

    Un pedido ``PENDIENTE`` puede recibirse a inventario (pasa a ``RECIBIDO``),
    imprimirse, exportarse a Excel o eliminarse. Los recibidos/cancelados son
    sólo consulta.
    """

    STATUSES = (("TODOS", "Todos"), ("PENDIENTE", "Pendientes"), ("RECIBIDO", "Recibidos"), ("CANCELADO", "Cancelados"))

    def __init__(
        self, commands: CommandBus, queries: QueryBus, store: StoreInfo | None = None, parent=None,
        settings: Settings | None = None,
    ):
        super().__init__(parent)
        self._commands = commands
        self._queries = queries
        self._store = store or StoreInfo()
        self._settings = settings or Settings()
        self._orders: list[PurchaseOrderDTO] = []
        self.setWindowTitle("Pedidos guardados")
        self.resize(1080, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(make_label("Pedidos guardados", object_name="pageTitle"))
        layout.addWidget(
            make_label(
                "Los pedidos pendientes se reciben a inventario en cualquier momento.",
                object_name="muted",
            )
        )

        toolbar = QHBoxLayout()
        self.status_combo = QComboBox()
        for value, text in self.STATUSES:
            self.status_combo.addItem(text, value)
        self.status_combo.currentIndexChanged.connect(lambda _: self.refresh())
        self.count_label = make_label("", object_name="muted")
        toolbar.addWidget(make_label("Estado:", object_name="muted"))
        toolbar.addWidget(self.status_combo)
        toolbar.addStretch(1)
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.table = make_table(
            ["Nº", "Fecha", "Estado", "Proveedores", "Artículos", "Total", "Nota"],
            stretch_column=3,
        )
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        close_btn = QPushButton("Cerrar")
        close_btn.setObjectName("ghost")
        close_btn.clicked.connect(self.accept)
        self.delete_btn = QPushButton("Eliminar")
        self.delete_btn.setObjectName("ghost")
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(self._delete_order)
        print_btn = QPushButton("Imprimir")
        print_btn.setObjectName("ghost")
        print_btn.clicked.connect(self._print_order)
        excel_btn = QPushButton("Exportar a Excel")
        excel_btn.setObjectName("ghost")
        excel_btn.clicked.connect(self._export_excel)
        receive_btn = QPushButton("Recibir a inventario")
        receive_btn.setObjectName("primary")
        receive_btn.clicked.connect(self._receive_order)
        buttons.addWidget(close_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(excel_btn)
        buttons.addWidget(print_btn)
        buttons.addWidget(receive_btn)
        layout.addLayout(buttons)

        self.refresh()

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        status = self.status_combo.currentData()
        self._orders = self._queries.ask(ListPurchaseOrdersQuery(status=status))
        self._render()

    def _visible(self) -> list[PurchaseOrderDTO]:
        return self._orders

    def _render(self) -> None:
        orders = self._visible()
        self.table.setRowCount(0)
        for order in orders:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(order.order_number))
            self.table.setItem(row, 1, QTableWidgetItem(order.created_at.strftime("%d/%m/%Y %H:%M")))
            self.table.setItem(row, 2, QTableWidgetItem(order.status))
            self.table.setItem(row, 3, QTableWidgetItem(", ".join(order.provider_names) or "—"))
            self.table.setItem(row, 4, QTableWidgetItem(str(order.item_count)))
            self.table.setItem(row, 5, QTableWidgetItem(order.total.format()))
            self.table.setItem(row, 6, QTableWidgetItem(order.note or "—"))
            self.table.setRowHeight(row, 34)
        pending = sum(1 for o in orders if o.status == PurchaseOrder.STATUS_PENDING)
        self.count_label.setText(
            f"{len(orders)} pedido(s)" + (f" · {pending} pendiente(s)" if pending else "")
        )
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(6)

    def _selected(self) -> PurchaseOrderDTO | None:
        row = self.table.currentRow()
        visible = self._visible()
        if row < 0 or row >= len(visible):
            return None
        return visible[row]

    # ------------------------------------------------------------------ #

    def _receive_order(self) -> None:
        order = self._selected()
        if order is None:
            QMessageBox.information(self, "Selección", "Seleccione un pedido para recibir.")
            return
        if order.status == PurchaseOrder.STATUS_RECEIVED:
            QMessageBox.information(self, "Pedido recibido", f"El pedido {order.order_number} ya fue recibido.")
            return
        if order.status == PurchaseOrder.STATUS_CANCELLED:
            QMessageBox.information(self, "Pedido cancelado", f"El pedido {order.order_number} está cancelado.")
            return
        dialog = ReceiveOrderDialog(
            self._commands,
            self._queries,
            _cart_lines_from_order(order),
            self,
            order_id=order.id,
            settings=self._settings,
        )
        if dialog.exec() != ReceiveOrderDialog.Accepted:
            return
        received = dialog.received_count()
        self.refresh()
        QMessageBox.information(
            self,
            "Recepción registrada",
            f"Pedido {order.order_number}: se recibieron {received} unidades en inventario.\n"
            "Los artículos quedaron relacionados con sus productos.",
        )

    def _delete_order(self) -> None:
        order = self._selected()
        if order is None:
            QMessageBox.information(self, "Selección", "Seleccione un pedido para eliminar.")
            return
        if order.status != PurchaseOrder.STATUS_PENDING:
            QMessageBox.information(
                self, "No editable", f"El pedido {order.order_number} está {order.status}; no se puede eliminar."
            )
            return
        answer = QMessageBox.question(
            self,
            "Eliminar pedido",
            f"¿Eliminar el pedido {order.order_number} ({order.item_count} unidades)?\nEsta acción no se puede deshacer.",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self._commands.execute(DeletePurchaseOrderCommand(purchase_order_id=order.id))
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self.refresh()

    def _print_order(self) -> None:
        order = self._selected()
        if order is None:
            QMessageBox.information(self, "Selección", "Seleccione un pedido para imprimir.")
            return
        try:
            from app.infrastructure.printers.ticket_esc_pos import print_receipt_ticket

            html = render_purchase_list_html(_purchase_lines_from_order(order), self._store)
            if print_receipt_ticket(html):
                return
            ReceiptDialog(html, self).exec()
        except Exception:  # noqa: BLE001 - la impresión nunca debe romper el diálogo
            log.exception("Pedido guardado: no se pudo imprimir.")

    def _export_excel(self) -> None:
        order = self._selected()
        if order is None:
            QMessageBox.information(self, "Selección", "Seleccione un pedido para exportar.")
            return
        default_name = f"{order.order_number}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar pedido",
            str(Path.home() / default_name),
            "Libro Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            target = write_purchase_list_excel(_purchase_lines_from_order(order), path, self._store)
            QMessageBox.information(self, "Exportado", f"Pedido {order.order_number} guardado en:\n{target}")
        except Exception:  # noqa: BLE001
            log.exception("Pedido guardado: no se pudo exportar a Excel.")
            QMessageBox.critical(self, "Error", "No se pudo exportar el pedido a Excel.")


class ImportedOrderPreviewDialog(QDialog):
    """Vista previa de un pedido leído de PDF/Excel antes de guardarlo como nuevo.

    Permite confirmar el proveedor, ajustar cantidades/precios y anotar un
    comentario. Al guardar se ejecuta LoadPurchaseOrderCommand: actualiza la
    lista del proveedor, crea los productos que falten y deja el pedido
    PENDIENTE (el stock no se toca hasta la recepción).
    """

    def __init__(
        self,
        commands: CommandBus,
        queries: QueryBus,
        result: OrderPdfResult,
        default_provider: str,
        parent=None,
    ):
        super().__init__(parent)
        self._commands = commands
        self._queries = queries
        self._lines: list[OrderPdfLine] = list(result.lines)
        self._existing_codes = {
            p.code.upper() for p in queries.ask(GetCatalogQuery(include_inactive=True))
        }
        self._providers = queries.ask(ListProvidersQuery())

        self.setWindowTitle("Cargar pedido (PDF/Excel)")
        self.resize(900, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(make_label("Cargar pedido como NUEVO", object_name="pageTitle"))
        layout.addWidget(
            make_label(
                f"Se detectaron {len(self._lines)} renglones"
                + (f" · proveedor sugerido: “{result.meta.supplier}”" if result.meta.supplier else "")
                + " · el stock solo se afecta al recibir el pedido.",
                object_name="muted",
            )
        )

        form = QFormLayout()
        self.provider_combo = QComboBox()
        self.provider_combo.setEditable(True)
        for provider in self._providers:
            self.provider_combo.addItem(provider.name)
        if default_provider:
            self.provider_combo.setCurrentText(default_provider)
        self.provider_combo.setMinimumWidth(320)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Nota del pedido (opcional)")
        form.addRow("Proveedor:", self.provider_combo)
        form.addRow("Nota:", self.note)
        layout.addLayout(form)

        self.table = make_table(
            ["Código", "Descripción", "Nuevo", "Cantidad", "Costo", "Venta"],
            stretch_column=1,
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(2, 64)
        self.table.setColumnWidth(3, 84)
        self.table.setColumnWidth(4, 96)
        self.table.setColumnWidth(5, 96)
        layout.addWidget(self.table, 1)

        self.summary = make_label("", object_name="muted")
        layout.addWidget(self.summary)

        buttons = QHBoxLayout()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Guardar pedido nuevo")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        buttons.addWidget(cancel_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)

        self._render()

    # ------------------------------------------------------------------ #

    def _render(self) -> None:
        table = self.table
        table.setRowCount(0)
        for line in self._lines:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(line.code))
            table.setItem(row, 1, QTableWidgetItem(line.description))
            nuevo = "SÍ" if line.code.upper() not in self._existing_codes else ""
            table.setItem(row, 2, QTableWidgetItem(nuevo))
            qty = QSpinBox()
            qty.setRange(1, 99999)
            qty.setValue(max(1, line.quantity))
            table.setCellWidget(row, 3, qty)
            purchase = self._money_spin(line.unit_price)
            table.setCellWidget(row, 4, purchase)
            sale_default = line.sale_price if line.sale_price is not None else 0
            sale = self._money_spin(sale_default, minimum=0.0)
            table.setCellWidget(row, 5, sale)
        new_count = self._new_count()
        self.summary.setText(
            f"{len(self._lines)} renglones · {new_count} producto(s) que se crearán en el inventario"
        )
        table.setRowHeight(row, 36)
        table.resizeColumnToContents(0)

    @staticmethod
    def _money_spin(value: Decimal | int | float, minimum: float = 0.01) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(minimum, 9_999_999.99)
        spin.setValue(float(value))
        return spin

    def _new_count(self) -> int:
        return sum(1 for line in self._lines if line.code.upper() not in self._existing_codes)

    def _save(self) -> None:
        provider_name = self.provider_combo.currentText().strip()
        if not provider_name:
            QMessageBox.warning(self, "Proveedor", "Indique el proveedor del pedido.")
            return
        lines = tuple(
            ImportedOrderLineRequest(
                code=self.table.item(row, 0).text(),
                description=self.table.item(row, 1).text(),
                quantity=int(self.table.cellWidget(row, 3).value()),
                unit_price=str(self.table.cellWidget(row, 4).value()),
                sale_price=(
                    str(self.table.cellWidget(row, 5).value())
                    if self.table.cellWidget(row, 5).value() > 0
                    else None
                ),
            )
            for row in range(self.table.rowCount())
        )
        try:
            saved = self._commands.execute(
                LoadPurchaseOrderCommand(
                    provider_name=provider_name,
                    note=self.note.text().strip(),
                    lines=lines,
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        QMessageBox.information(
            self,
            "Pedido cargado",
            f"Pedido {saved.order_number} cargado como NUEVO pendiente "
            f"({len(lines)} renglones) para {provider_name}.\n"
            "El stock se suma al recibirlo desde esta misma ventana.",
        )
        self.accept()


class ProviderOrdersDialog(QDialog):
    """Pedidos de un único proveedor: cargar PDF/Excel, recibir, imprimir y etiquetas.

    A diferencia de “Pedidos guardados” (todos), aquí la lista y la carga de
    archivos quedan acotadas al proveedor elegido.
    """

    STATUSES = (
        ("TODOS", "Todos"),
        ("PENDIENTE", "Pendientes"),
        ("RECIBIDO", "Recibidos"),
        ("CANCELADO", "Cancelados"),
    )

    def __init__(
        self,
        commands: CommandBus,
        queries: QueryBus,
        provider: ProviderDTO,
        store: StoreInfo | None = None,
        parent=None,
        settings: Settings | None = None,
    ):
        super().__init__(parent)
        self._commands = commands
        self._queries = queries
        self._provider = provider
        self._store = store or StoreInfo()
        self._settings = settings or Settings()
        self._orders: list[PurchaseOrderDTO] = []
        self.setWindowTitle(f"Pedidos — {provider.name}")
        self.resize(1080, 540)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(make_label(f"Pedidos — {provider.name}", object_name="pageTitle"))
        layout.addWidget(
            make_label(
                "Cargue aquí su pedido (PDF o Excel) y quedará como NUEVO pedido pendiente. "
                "El stock solo se afecta al recibirlo.",
                object_name="muted",
            )
        )

        toolbar = QHBoxLayout()
        self.status_combo = QComboBox()
        for value, text in self.STATUSES:
            self.status_combo.addItem(text, value)
        self.status_combo.currentIndexChanged.connect(lambda _: self.refresh())
        self.count_label = make_label("", object_name="muted")
        toolbar.addWidget(make_label("Estado:", object_name="muted"))
        toolbar.addWidget(self.status_combo)
        toolbar.addStretch(1)
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)

        self.table = make_table(
            ["Nº", "Fecha", "Estado", "Artículos", "Total", "Nota"],
            stretch_column=5,
        )
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        close_btn = QPushButton("Cerrar")
        close_btn.setObjectName("ghost")
        close_btn.clicked.connect(self.accept)
        self.delete_btn = QPushButton("Eliminar")
        self.delete_btn.setObjectName("ghost")
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(self._delete_order)
        print_btn = QPushButton("Imprimir")
        print_btn.setObjectName("ghost")
        print_btn.clicked.connect(self._print_order)
        self.labels_btn = QPushButton("Etiquetas")
        self.labels_btn.setObjectName("ghost")
        self.labels_btn.clicked.connect(self._print_labels)
        receive_btn = QPushButton("Recibir a inventario")
        receive_btn.setObjectName("primary")
        receive_btn.clicked.connect(self._receive_order)
        load_btn = QPushButton("Cargar pedido (PDF/Excel)")
        load_btn.setObjectName("accent")
        load_btn.clicked.connect(self._load_order)
        buttons.addWidget(close_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)
        buttons.addWidget(print_btn)
        buttons.addWidget(self.labels_btn)
        buttons.addWidget(load_btn)
        buttons.addWidget(receive_btn)
        layout.addLayout(buttons)

        self._labels = LabelPrintingService(self._settings, self)
        self.refresh()

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        status = self.status_combo.currentData()
        self._orders = self._queries.ask(
            ListPurchaseOrdersQuery(status=status, provider=self._provider.name)
        )
        self._render()

    def _render(self) -> None:
        orders = self._orders
        self.table.setRowCount(0)
        for order in orders:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(order.order_number))
            self.table.setItem(row, 1, QTableWidgetItem(order.created_at.strftime("%d/%m/%Y %H:%M")))
            self.table.setItem(row, 2, QTableWidgetItem(order.status))
            self.table.setItem(row, 3, QTableWidgetItem(str(order.item_count)))
            self.table.setItem(row, 4, QTableWidgetItem(order.total.format()))
            self.table.setItem(row, 5, QTableWidgetItem(order.note or "—"))
            self.table.setRowHeight(row, 34)
        pending = sum(1 for o in orders if o.status == PurchaseOrder.STATUS_PENDING)
        self.count_label.setText(
            f"{len(orders)} pedido(s)" + (f" · {pending} pendiente(s)" if pending else "")
        )
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(5)

    def _selected(self) -> PurchaseOrderDTO | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._orders):
            return None
        return self._orders[row]

    # ------------------------------------------------------------------ #

    def _load_order(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Cargar pedido", "", "Pedido de proveedor (*.pdf *.xlsx);;Todo (*.*)"
        )
        if not path:
            return
        try:
            if path.lower().endswith(".pdf"):
                result = parse_order_pdf(path)
            else:
                result = parse_order_excel(path)
        except Exception:  # noqa: BLE001 - archivo ilegible
            log.exception("Carga de pedido: archivo ilegible.")
            QMessageBox.warning(self, "Archivo inválido", "No se pudo leer el archivo como pedido.")
            return
        if not result.lines:
            QMessageBox.warning(
                self,
                "Sin renglones",
                "No se detectaron renglones de pedido en el archivo.\n"
                "Verifique que sea un PDF/Excel de pedido de proveedor.",
            )
            return
        dialog = ImportedOrderPreviewDialog(
            self._commands,
            self._queries,
            result,
            self._provider.name,
            self,
        )
        if dialog.exec() != ImportedOrderPreviewDialog.Accepted:
            return
        self.refresh()

    def _receive_order(self) -> None:
        order = self._selected()
        if order is None:
            QMessageBox.information(self, "Selección", "Seleccione un pedido para recibir.")
            return
        if order.status == PurchaseOrder.STATUS_RECEIVED:
            QMessageBox.information(self, "Pedido recibido", f"El pedido {order.order_number} ya fue recibido.")
            return
        if order.status == PurchaseOrder.STATUS_CANCELLED:
            QMessageBox.information(self, "Pedido cancelado", f"El pedido {order.order_number} está cancelado.")
            return
        dialog = ReceiveOrderDialog(
            self._commands,
            self._queries,
            _cart_lines_from_order(order),
            self,
            order_id=order.id,
            settings=self._settings,
        )
        if dialog.exec() != ReceiveOrderDialog.Accepted:
            return
        received = dialog.received_count()
        self.refresh()
        QMessageBox.information(
            self,
            "Recepción registrada",
            f"Pedido {order.order_number}: se recibieron {received} unidades en inventario.\n"
            "Los artículos quedaron relacionados con sus productos.",
        )

    def _delete_order(self) -> None:
        order = self._selected()
        if order is None:
            QMessageBox.information(self, "Selección", "Seleccione un pedido para eliminar.")
            return
        if order.status != PurchaseOrder.STATUS_PENDING:
            QMessageBox.information(
                self, "No editable", f"El pedido {order.order_number} está {order.status}; no se puede eliminar."
            )
            return
        answer = QMessageBox.question(
            self,
            "Eliminar pedido",
            f"¿Eliminar el pedido {order.order_number} ({order.item_count} unidades)?\nEsta acción no se puede deshacer.",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self._commands.execute(DeletePurchaseOrderCommand(purchase_order_id=order.id))
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self.refresh()

    def _print_order(self) -> None:
        order = self._selected()
        if order is None:
            QMessageBox.information(self, "Selección", "Seleccione un pedido para imprimir.")
            return
        try:
            from app.infrastructure.printers.ticket_esc_pos import print_receipt_ticket

            html = render_purchase_list_html(_purchase_lines_from_order(order), self._store)
            if print_receipt_ticket(html):
                return
            ReceiptDialog(html, self).exec()
        except Exception:  # noqa: BLE001 - la impresión nunca debe romper el diálogo
            log.exception("Pedido por proveedor: no se pudo imprimir.")

    def _print_labels(self) -> None:
        order = self._selected()
        if order is None:
            QMessageBox.information(self, "Selección", "Seleccione un pedido para sus etiquetas.")
            return
        entries = [
            LabelBatchEntry(code=line.code, name=line.description, price=line.unit_price, copies=line.quantity)
            for line in order.lines
        ]
        if not entries:
            return
        LabelBatchDialog(self._labels, entries, self).exec()