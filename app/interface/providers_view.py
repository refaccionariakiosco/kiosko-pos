"""Pantalla de proveedores: alta, listas de precios importadas y lista de compra.

Es un área independiente del inventario: aquí solo se cargan proveedores, se
importan sus listas para cotizar y se arma un pedido para imprimir.
"""

from __future__ import annotations

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
from app.application.commands import DeleteProviderCommand
from app.application.queries import ListProvidersQuery
from app.application.read_models import ProviderDTO
from app.domain.exceptions import DomainError
from app.interface.provider_dialogs import (
    ProviderDialog,
    ProviderImportDialog,
    ProviderItemsDialog,
    PurchaseListDialog,
    SavedOrdersDialog,
)
from app.interface.widgets import make_label, make_table, search_matches
from app.settings import Settings


class ProvidersView(QWidget):
    def __init__(self, commands: CommandBus, queries: QueryBus, settings: Settings):
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._settings = settings
        self._providers: list[ProviderDTO] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        layout.addWidget(make_label("Proveedores", object_name="pageTitle"))
        layout.addWidget(
            make_label(
                "Área independiente del inventario: listas de precios para cotizar pedidos.",
                object_name="muted",
            )
        )

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar proveedor…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._render())
        purchase_btn = QPushButton("Lista de compra")
        purchase_btn.setObjectName("primary")
        purchase_btn.clicked.connect(self._purchase_list)
        saved_orders_btn = QPushButton("Pedidos guardados")
        saved_orders_btn.setObjectName("primary")
        saved_orders_btn.clicked.connect(self._saved_orders)
        new_btn = QPushButton("Nuevo proveedor")
        edit_btn = QPushButton("Editar")
        delete_btn = QPushButton("Eliminar")
        import_btn = QPushButton("Importar artículos")
        items_btn = QPushButton("Ver artículos")
        for btn in (new_btn, edit_btn, delete_btn, import_btn, items_btn):
            btn.setObjectName("ghost")
        new_btn.clicked.connect(self._new_provider)
        edit_btn.clicked.connect(self._edit_provider)
        delete_btn.clicked.connect(self._delete_provider)
        import_btn.clicked.connect(self._import_items)
        items_btn.clicked.connect(self._view_items)
        toolbar.addWidget(self.search, 1)
        toolbar.addStretch(1)
        toolbar.addWidget(items_btn)
        toolbar.addWidget(import_btn)
        toolbar.addWidget(delete_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(new_btn)
        toolbar.addWidget(saved_orders_btn)
        toolbar.addWidget(purchase_btn)
        layout.addLayout(toolbar)

        self.table = make_table(
            ["Proveedor", "Teléfono", "Artículos", "Registro"],
            stretch_column=0,
        )
        layout.addWidget(self.table, 1)
        self.count_label = make_label("", object_name="muted")
        layout.addWidget(self.count_label)

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._providers = self._queries.ask(ListProvidersQuery())
        self._render()

    def _visible(self) -> list[ProviderDTO]:
        term = self.search.text().strip()
        if not term:
            return self._providers
        return [p for p in self._providers if search_matches(term, p.name, p.phone)]

    def _render(self) -> None:
        providers = self._visible()
        self.table.setRowCount(0)
        for provider in providers:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(provider.name))
            self.table.setItem(row, 1, QTableWidgetItem(provider.phone or "—"))
            self.table.setItem(row, 2, QTableWidgetItem(str(provider.item_count)))
            created = provider.created_at.strftime("%d/%m/%Y") if provider.created_at else "—"
            self.table.setItem(row, 3, QTableWidgetItem(created))
        self.count_label.setText(f"{len(providers)} proveedores")

    def _selected(self) -> ProviderDTO | None:
        row = self.table.currentRow()
        visible = self._visible()
        if row < 0 or row >= len(visible):
            return None
        return visible[row]

    # ------------------------------------------------------------------ #

    def _new_provider(self) -> None:
        dialog = ProviderDialog(self._commands, self)
        if dialog.exec() != ProviderDialog.Accepted:
            return
        self.refresh()

    def _edit_provider(self) -> None:
        provider = self._selected()
        if provider is None:
            QMessageBox.information(self, "Selección", "Seleccione un proveedor para editar.")
            return
        dialog = ProviderDialog(self._commands, self, provider=provider)
        if dialog.exec() != ProviderDialog.Accepted:
            return
        self.refresh()

    def _delete_provider(self) -> None:
        provider = self._selected()
        if provider is None:
            QMessageBox.information(self, "Selección", "Seleccione un proveedor para eliminar.")
            return
        items = provider.item_count
        question = QMessageBox.question(
            self,
            "Eliminar proveedor",
            f"¿Eliminar el proveedor “{provider.name}”?\n"
            f"Se borrarán también sus {items} artículo(s) de la lista de precios."
            if items
            else f"¿Eliminar el proveedor “{provider.name}”?\nEsta acción no se puede deshacer.",
        )
        if question != QMessageBox.Yes:
            return
        try:
            self._commands.execute(DeleteProviderCommand(provider_id=provider.id))
        except DomainError as exc:
            QMessageBox.warning(self, "Acción rechazada", str(exc))
            return
        self.refresh()

    def _import_items(self) -> None:
        provider = self._selected()
        if provider is None:
            QMessageBox.information(self, "Selección", "Seleccione un proveedor para importar su lista.")
            return
        dialog = ProviderImportDialog(self._commands, self._queries, provider, self)
        if dialog.exec() != ProviderImportDialog.Accepted:
            return
        self.refresh()

    def _view_items(self) -> None:
        provider = self._selected()
        if provider is None:
            QMessageBox.information(self, "Selección", "Seleccione un proveedor para ver sus artículos.")
            return
        dialog = ProviderItemsDialog(self._queries, provider, self)
        dialog.exec()

    def _purchase_list(self) -> None:
        store = getattr(self._settings, "store", None)
        dialog = PurchaseListDialog(
            self._commands,
            self._queries,
            store,
            self,
            provider=self._selected(),
            settings=self._settings,
        )
        dialog.exec()

    def _saved_orders(self) -> None:
        store = getattr(self._settings, "store", None)
        dialog = SavedOrdersDialog(
            self._commands, self._queries, store, self, settings=self._settings
        )
        dialog.exec()