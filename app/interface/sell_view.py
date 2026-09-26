"""Vista de venta (POS): grilla de productos, carrito multiticket, cobro y recibo."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QDoubleSpinBox,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import CommandBus, QueryBus
from app.application.commands import CompleteSaleCommand, SaleItemRequest, SalePaymentRequest
from app.application.queries import GetCatalogQuery, GetCategoriesQuery
from app.application.read_models import ProductDTO
from app.domain.exceptions import DomainError
from app.domain.value_objects import Money
from app.infrastructure.printers.receipt import render_receipt_html
from app.settings import Settings
from app.interface.dialogs import (
    CancelTicketDialog,
    ChargeAmountDialog,
    PaymentDialog,
    ReceiptDialog,
    show_domain_error,
)
from app.interface.widgets import ClickableLabel, make_label, make_table, search_matches, with_shortcut


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


MAX_GRID_BUTTONS = 150
CART_COLUMNS = ["Código", "Producto", "Precio", "Cant.", "Subtotal"]

log = logging.getLogger(__name__)


class ProductButton(QPushButton):
    def __init__(self, text: str, product: ProductDTO):
        super().__init__(text)
        self.product = product
        self.setObjectName("productCard")
        self.setMinimumSize(150, 74)


def cart_price(entry: dict) -> Money:
    """Precio efectivo de la línea: el ajustado inline, o el de catálogo."""
    override = entry.get("unit_price")
    return override if override is not None else entry["product"].unit_price


def cart_total(cart: list[dict]) -> Money:
    return sum((cart_price(e) * e["qty"] for e in cart), Money.zero())


class SellView(QWidget):
    def __init__(self, commands: CommandBus, queries: QueryBus, settings: Settings):
        super().__init__()
        self._commands = commands
        self._queries = queries
        self._settings = settings
        self._products: list[ProductDTO] = []
        self._categories: list = []
        self._ticket_carts: list[list[dict]] = []
        self._ticket_tables: list[QTableWidget] = []
        self._ticket_charges: list[Money | None] = []
        self._ticket_seq = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        layout.addWidget(make_label("Vender", object_name="pageTitle"))

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar producto por nombre o código…")
        self.barcode = QLineEdit()
        self.barcode.setPlaceholderText("Código de barras / EAN (Enter)…")
        self.search.textChanged.connect(self._apply_search)
        self.barcode.returnPressed.connect(self._add_by_barcode)
        top.addWidget(self.search, 2)
        top.addWidget(self.barcode, 1)
        layout.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(12)

        # Columna izquierda: categorías + grilla
        left = QVBoxLayout()
        self.categories = QListWidget()
        self.categories.setFixedWidth(170)
        self.categories.currentRowChanged.connect(lambda _: self._rebuild_grid())
        left.addWidget(self.categories, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(8)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.scroll.setWidget(self.grid_host)
        left.addWidget(self.scroll, 1)

        left_wrap = QWidget()
        left_wrap.setLayout(left)
        body.addWidget(left_wrap, 3)

        # Columna derecha: tickets + carrito
        cart_widget = QWidget()
        cart_layout = QVBoxLayout(cart_widget)
        cart_layout.setContentsMargins(0, 0, 0, 0)
        cart_layout.addWidget(make_label("Carrito", object_name="sectionTitle"))
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(lambda _: self._refresh_totals())
        self.new_ticket_btn = QPushButton("＋ Nuevo")
        self.new_ticket_btn.setObjectName("ghost")
        self.new_ticket_btn.setToolTip("Abrir un ticket nuevo")
        self.new_ticket_btn.clicked.connect(self._new_ticket)
        self.tabs.setCornerWidget(self.new_ticket_btn, Qt.TopRightCorner)
        cart_layout.addWidget(self.tabs, 1)

        totals = QHBoxLayout()
        left_total = QVBoxLayout()
        self.count_label = make_label("0 ítems", object_name="muted")
        self.discount_label = make_label("", object_name="muted")
        left_total.addWidget(self.count_label)
        left_total.addWidget(self.discount_label)
        left_total.addStretch(1)
        totals.addLayout(left_total)
        self.total_label = ClickableLabel("$ 0,00")
        self.total_label.setObjectName("totalAmount")
        self.total_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.total_label.setCursor(Qt.PointingHandCursor)
        self.total_label.setToolTip(
            "Doble clic para ajustar el monto a cobrar.\n"
            "El ajuste es un monto absoluto sobre el total del ticket:\n"
            "la diferencia queda registrada como descuento del ticket."
        )

        self.total_label.doubleClicked.connect(self._edit_charge_amount)
        totals.addWidget(self.total_label, 1)
        cart_layout.addLayout(totals)

        actions = QHBoxLayout()
        self.cancel_ticket_btn = QPushButton(with_shortcut("Cancelar ticket", "F11"))
        self.remove_btn = QPushButton("Quitar selección")
        self.clear_btn = QPushButton("Vaciar")
        self.adjust_btn = QPushButton("Ajustar monto")
        self.pending_btn = QPushButton(with_shortcut("Pendiente", "F6"))
        self.pay_btn = QPushButton(with_shortcut("Cobrar", "F12"))
        self.cancel_ticket_btn.setObjectName("ghost")
        self.remove_btn.setObjectName("ghost")
        self.clear_btn.setObjectName("ghost")
        self.adjust_btn.setObjectName("ghost")
        self.adjust_btn.setToolTip(
            "Fija el monto a cobrar del ticket (monto absoluto).\n"
            "La diferencia contra el total se guarda como descuento del ticket,\n"
            "por encima de cualquier precio ajustado partida por partida."
        )

        self.pending_btn.setObjectName("ghost")
        self.pay_btn.setObjectName("primary")
        self.cancel_ticket_btn.clicked.connect(self._cancel_ticket)
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn.clicked.connect(self._clear_cart)
        self.adjust_btn.clicked.connect(self._edit_charge_amount)
        self.pending_btn.clicked.connect(self._pending_ticket)
        self.pay_btn.clicked.connect(self._collect)
        self.cancel_ticket_btn.setToolTip("Anular un ticket del día (F11)")
        self.adjust_btn.setToolTip("Modificar el monto a cobrar (también con doble clic en el total)")
        self.pending_btn.setToolTip("Dejar el ticket pendiente (F6)")
        self.pay_btn.setToolTip("Cobrar (F12)")
        actions.addWidget(self.cancel_ticket_btn)
        actions.addWidget(self.remove_btn)
        actions.addWidget(self.clear_btn)
        actions.addWidget(self.adjust_btn)
        actions.addWidget(self.pending_btn)
        actions.addStretch(1)
        actions.addWidget(self.pay_btn)
        cart_layout.addLayout(actions)
        body.addWidget(cart_widget, 2)

        layout.addLayout(body, 1)

        QShortcut(QKeySequence(Qt.Key_F12), self, self.pay_btn.click)
        QShortcut(QKeySequence(Qt.Key_F11), self, self.cancel_ticket_btn.click)
        QShortcut(QKeySequence(Qt.Key_F6), self, self.pending_btn.click)
        self._new_ticket()

    # ------------------------------------------------------------------ #
    # Tickets
    # ------------------------------------------------------------------ #

    def _current_cart(self) -> list[dict]:
        return self._ticket_carts[self.tabs.currentIndex()]

    def _current_table(self) -> QTableWidget:
        return self._ticket_tables[self.tabs.currentIndex()]

    def _new_ticket(self) -> None:
        self._ticket_seq += 1
        table = make_table(CART_COLUMNS, stretch_column=1, height=360)
        table.horizontalHeader().setStretchLastSection(False)
        self._ticket_carts.append([])
        self._ticket_tables.append(table)
        self._ticket_charges.append(None)
        self.tabs.addTab(table, f"Ticket {self._ticket_seq}")
        self.tabs.setCurrentIndex(len(self._ticket_carts) - 1)
        self._refresh_totals()
        table.setFocus()

    def _close_tab(self, index: int) -> None:
        if len(self._ticket_carts) <= 1:
            return
        del self._ticket_carts[index]
        self.tabs.removeTab(index)
        del self._ticket_tables[index]
        del self._ticket_charges[index]
        self._refresh_totals()

    def _pending_ticket(self) -> None:
        if not self._current_cart():
            QMessageBox.information(self, "Ticket vacío", "El ticket actual está vacío.")
            return
        self._new_ticket()

    # ------------------------------------------------------------------ #
    # Datos
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._categories = self._queries.ask(GetCategoriesQuery())
        self.categories.blockSignals(True)
        self.categories.clear()
        self.categories.addItem("Todas")
        for category in self._categories:
            self.categories.addItem(category.name)
        self.categories.blockSignals(False)
        self.categories.setCurrentRow(0)
        self._products = self._queries.ask(GetCatalogQuery())
        self._rebuild_grid()

    def _current_category_id(self) -> int | None:
        row = self.categories.currentRow()
        if row <= 0:
            return None
        if 0 < row <= len(self._categories):
            return self._categories[row - 1].id
        return None

    def _visible_products(self) -> list[ProductDTO]:
        category_id = self._current_category_id()
        term = self.search.text().strip().lower()
        products = [p for p in self._products if p.active]
        if category_id is not None:
            products = [p for p in products if p.category_id == category_id]
        if term:
            products = [p for p in products if search_matches(term, p.name, p.code)]
        return products

    def _apply_search(self, _text: str | None = None) -> None:
        self._rebuild_grid()

    # ------------------------------------------------------------------ #
    # Grilla de productos
    # ------------------------------------------------------------------ #

    def _rebuild_grid(self) -> None:
        clear_layout(self.grid)
        visible = self._visible_products()
        limited = len(visible) > MAX_GRID_BUTTONS
        buttons = visible[:MAX_GRID_BUTTONS] if limited else visible
        cols = 4
        for index, button in enumerate(self._make_product_button(p) for p in buttons):
            self.grid.addWidget(button, index // cols, index % cols)
        row = (len(buttons) + cols - 1) // cols
        if not buttons:
            self.grid.addWidget(
                make_label("Sin productos para mostrar.", object_name="muted", alignment=Qt.AlignCenter),
                0,
                0,
                1,
                cols,
            )
        elif limited:
            self.grid.addWidget(
                make_label(
                    f"Se muestran {len(buttons)} de {len(visible)} productos. Buscá por nombre o código…",
                    object_name="muted",
                    alignment=Qt.AlignCenter,
                ),
                row,
                0,
                1,
                cols,
            )
        self.grid_host.updateGeometry()

    def _make_product_button(self, product: ProductDTO) -> ProductButton:
        price = product.unit_price.format()
        stock = "agotado" if product.out_of_stock else f"stk {product.stock}"
        tooltip = (
            f"{product.name}\n{price}  ·  {stock}"
            + (f"\nCódigo: {product.code}" if product.code else "")
        )
        display = product.name if len(product.name) <= 34 else product.name[:33].rstrip() + "…"
        text = f"{display}\n{price}  ·  {stock}"
        button = ProductButton(text, product)
        button.setToolTip(tooltip)
        if len(product.name) > 34:
            font = button.font()
            font.setPointSizeF(max(9.5, font.pointSizeF() - 2.0))
            button.setFont(font)
        if product.out_of_stock or product.low_stock:
            if product.out_of_stock:
                button.setStyleSheet(button.styleSheet() + "color:#9ca3af;")
            button.setToolTip(
                f"Stock: {product.stock} (mínimo {product.min_stock})\n{product.name}\n{price}  ·  {stock}"
            )
        button.clicked.connect(lambda _=False, b=button: self._add_product(b.product))
        return button

    # ------------------------------------------------------------------ #
    # Carrito (ticket activo)
    # ------------------------------------------------------------------ #

    def _add_by_barcode(self) -> None:
        code = self.barcode.text().strip()
        if not code:
            return
        found = next((p for p in self._products if p.code.upper() == code.upper()), None)
        if found is None:
            matches = self._queries.ask(GetCatalogQuery(search=code))
            found = next((p for p in matches if p.code.upper() == code.upper()), matches[0] if matches else None)
        self.barcode.clear()
        if found is None:
            QMessageBox.information(self, "Producto desconocido", f"No se encontró el código {code}.")
            return
        self._add_product(found)

    def _add_product(self, product: ProductDTO) -> None:
        cart = self._current_cart()
        if product.out_of_stock:
            QMessageBox.warning(self, "Sin stock", f"{product.name} está agotado.")
            return
        for entry in cart:
            if entry["product"].id == product.id:
                if entry["qty"] >= product.stock:
                    QMessageBox.warning(self, "Stock máximo", f"Ya agregó todo el stock de {product.name}.")
                    return
                entry["qty"] += 1
                self._rebuild_cart()
                return
        cart.append({"product": product, "qty": 1, "unit_price": None})
        self._rebuild_cart()

    def _rebuild_cart(self) -> None:
        cart = self._current_cart()
        table = self._current_table()
        table.setRowCount(0)
        table.clearSelection()
        for entry in cart:
            product = entry["product"]
            qty = entry["qty"]
            price = cart_price(entry)
            subtotal = price * qty

            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(product.code))
            table.setItem(row, 1, QTableWidgetItem(product.name))

            # Precio editable: parte de catálogo y el cajero puede ajustarlo.
            # Solo permite bajar el precio; el descuento se aplica aparte.
            price_spin = QDoubleSpinBox()
            price_spin.setDecimals(2)
            price_spin.setRange(0.01, 9_999_999.99)
            price_spin.setSingleStep(10.0)
            price_spin.setGroupSeparatorShown(True)
            price_spin.setKeyboardTracking(False)
            price_spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            price_spin.setValue(float(price.as_decimal()))
            price_spin.setToolTip(
                f"Precio de catálogo: {product.unit_price.format()}\n"
                "Ajustá el precio de esta partida. El descuento del ticket se aplica aparte."
            )
            price_spin.editingFinished.connect(lambda s=price_spin, pid=product.id: self._on_price(pid, s))
            table.setCellWidget(row, 2, price_spin)

            spin = QSpinBox()
            spin.setMinimum(1)
            spin.setMaximum(max(1, product.stock))
            spin.setValue(qty)
            spin.valueChanged.connect(lambda value, r=row, p=product: self._on_qty(r, p.id, value))
            table.setCellWidget(row, 3, spin)
            table.setItem(row, 4, QTableWidgetItem(subtotal.format()))
            table.setRowHeight(row, 34)
        table.setCurrentCell(-1, -1)
        self._refresh_totals()

    def _on_price(self, product_id: int, spin: QDoubleSpinBox) -> None:
        """Fija el precio inline de la línea y recalcula su subtotal."""
        cart = self._current_cart()
        index = next((i for i, e in enumerate(cart) if e["product"].id == product_id), -1)
        if index < 0:
            return
        entry = cart[index]
        new_price = Money.from_input(f"{spin.value():.2f}")
        if new_price <= Money.zero():
            spin.setValue(float(entry["product"].unit_price.as_decimal()))
            return
        entry["unit_price"] = new_price
        table = self._current_table()
        if index < table.rowCount():
            table.setItem(index, 4, QTableWidgetItem((new_price * entry["qty"]).format()))
        self._refresh_totals()

    def _refresh_totals(self) -> None:
        cart = self._current_cart()
        subtotal = cart_total(cart)
        charge = self._charge_for(subtotal)
        count = sum(e["qty"] for e in cart)
        self.count_label.setText(f"{count} ítems")
        self.total_label.setText(charge.format())
        if charge < subtotal:
            discount = subtotal - charge
            self.discount_label.setText(f"Descuento: -{discount.format()}")
            self.discount_label.setVisible(True)
        else:
            self.discount_label.setVisible(False)
        self.pay_btn.setEnabled(bool(cart))
        self.adjust_btn.setEnabled(bool(cart))

    def _charge_for(self, subtotal: Money) -> Money:
        override = self._ticket_charges[self.tabs.currentIndex()]
        if override is None:
            return subtotal
        return override if override < subtotal else subtotal

    def _edit_charge_amount(self) -> None:
        cart = self._current_cart()
        if not cart:
            QMessageBox.information(self, "Ticket vacío", "No hay productos en el ticket actual.")
            return
        subtotal = cart_total(cart)
        current = self._charge_for(subtotal)
        dialog = ChargeAmountDialog(subtotal, current, self)
        if dialog.exec() != ChargeAmountDialog.Accepted:
            return
        charge = dialog.charge_amount()
        if charge is None:
            return
        self._ticket_charges[self.tabs.currentIndex()] = charge
        self._refresh_totals()

    def _on_qty(self, row: int, product_id: int, value: int) -> None:
        cart = self._current_cart()
        entry = next((e for e in cart if e["product"].id == product_id), None)
        if entry is None:
            return
        entry["qty"] = max(1, value)
        subtotal = cart_price(entry) * entry["qty"]
        self._current_table().setItem(row, 4, QTableWidgetItem(subtotal.format()))
        self._refresh_totals()

    def _remove_selected(self) -> None:
        row = self._current_table().currentRow()
        cart = self._current_cart()
        if row < 0 or row >= len(cart):
            return
        del cart[row]
        self._rebuild_cart()

    def _clear_cart(self) -> None:
        self._current_cart().clear()
        self._rebuild_cart()

    # ------------------------------------------------------------------ #
    # Cobro
    # ------------------------------------------------------------------ #

    def _collect(self) -> None:
        cart = self._current_cart()
        if not cart:
            return
        subtotal = cart_total(cart)
        charge = self._charge_for(subtotal)
        discount = subtotal - charge if charge < subtotal else Money.zero()
        dialog = PaymentDialog(charge, self)
        if dialog.exec() != PaymentDialog.Accepted:
            return
        selection = dialog.selection()
        if selection is None:
            return

        command = CompleteSaleCommand(
            items=tuple(
                SaleItemRequest(code=e["product"].code, quantity=e["qty"], unit_price=cart_price(e))
                for e in cart
            ),
            payments=tuple(SalePaymentRequest(method=code, amount=amount) for code, amount in selection.payments),
            tendered=selection.tendered,
            discount=discount if discount > Money.zero() else None,
        )
        try:
            result = self._commands.execute(command)
        except DomainError as exc:
            show_domain_error(self, exc)
            self.refresh()
            return

        self._mark_charged()
        self.refresh()
        self._kick_drawer_if_cash(selection.payments)
        if selection.print_receipt:
            receipt_html = render_receipt_html(result.sale, self._settings.store)
            ReceiptDialog(receipt_html, self).exec()

    def _kick_drawer_if_cash(self, payments) -> None:
        """Abre el cajón de dinero al cobrar en efectivo (puede fallar sin detener la venta)."""
        if not any(code == "EFECTIVO" for code, _amount in payments):
            return
        try:
            from app.infrastructure.printers.ticket_esc_pos import kick_cash_drawer

            if not kick_cash_drawer():
                log.warning("Cajón: no se abrió, la venta continúa.")
        except Exception:  # noqa: BLE001 - el pulso nunca debe romper la venta
            log.exception("Cajón: falló el pulso tras la venta.")

    def _mark_charged(self) -> None:
        if len(self._ticket_carts) > 1:
            self._close_tab(self.tabs.currentIndex())
        else:
            self._ticket_charges[0] = None
            self._current_cart().clear()
            self._rebuild_cart()

    # ------------------------------------------------------------------ #
    # Cancelación de tickets del día
    # ------------------------------------------------------------------ #

    def _cancel_ticket(self) -> None:
        dialog = CancelTicketDialog(self._commands, self._queries, self._settings, self)
        dialog.exec()
        self.refresh()