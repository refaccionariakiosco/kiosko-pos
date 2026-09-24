"""Diálogos de la interfaz (producto, cobro, ajuste de stock, detalle de venta)."""

from __future__ import annotations

import logging
from decimal import Decimal
from datetime import datetime, time

from PySide6.QtCore import QMarginsF, QSize, Qt
from PySide6.QtGui import QKeySequence, QPageLayout, QPageSize, QShortcut, QTextDocument

from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import CommandBus, QueryBus
from app.application.commands import (
    AddAbonoCommand,
    CloseCashDayCommand,
    CreateApartadoCommand,
    OpenCashDayCommand,
    RefundSaleItemCommand,
    RegisterCashMovementCommand,
    VoidSaleCommand,
)
from app.application.queries import (
    GetApartadoQuery,
    GetCashMovementsQuery,
    GetCatalogQuery,
    GetCorteQuery,
    GetOpenCashDayQuery,
    GetSaleQuery,
    GetSalesHistoryQuery,
)
from app.application.read_models import (
    ApartadoDTO,
    CashDayDTO,
    CorteDTO,
    ProductDTO,
    SaleDTO,
    StockMovementDTO,
)
from app.domain.entities import PaymentMethod
from app.domain.exceptions import DomainError
from app.domain.value_objects import Money
from app.infrastructure.printers.receipt import render_receipt_html
from app.interface.widgets import glyph_icon, make_label, make_table, search_matches, with_shortcut
from app.settings import StoreInfo


log = logging.getLogger(__name__)


def show_domain_error(parent: QWidget, error: DomainError) -> None:
    QMessageBox.warning(parent, "Acción rechazada", str(error))


# --------------------------------------------------------------------------- #
# Producto
# --------------------------------------------------------------------------- #


class ProductDialog(QDialog):
    """Crear o editar un producto."""

    def __init__(self, categories: list[tuple[int, str]], product: ProductDTO | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar producto" if product else "Nuevo producto")
        self.setMinimumWidth(420)
        self.product = product

        form = QFormLayout()
        self.code = QLineEdit()
        self.name = QLineEdit()
        self.category = QComboBox()
        self.category.addItem("Sin categoría", None)
        for cid, cname in categories:
            self.category.addItem(cname, cid)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 999999)
        self.price.setDecimals(2)
        self.price.setPrefix("$ ")
        self.price.setGroupSeparatorShown(True)
        self.cost = QDoubleSpinBox()
        self.cost.setRange(0, 999999)
        self.cost.setDecimals(2)
        self.cost.setPrefix("$ ")
        self.cost.setGroupSeparatorShown(True)
        self.stock = QSpinBox()
        self.stock.setRange(0, 999999)
        self.min_stock = QSpinBox()
        self.min_stock.setRange(0, 999999)
        self.description = QLineEdit()

        form.addRow("Código / barras:", self.code)
        form.addRow("Nombre:", self.name)
        form.addRow("Categoría:", self.category)
        form.addRow("Precio de venta:", self.price)
        form.addRow("Costo (opcional):", self.cost)
        form.addRow("Stock inicial:", self.stock)
        form.addRow("Stock mínimo:", self.min_stock)
        form.addRow("Descripción:", self.description)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Guardar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        if product:
            self.code.setText(product.code)
            self.name.setText(product.name)
            idx = self.category.findData(product.category_id or -1)
            if idx >= 0:
                self.category.setCurrentIndex(idx)
            else:
                self.category.setCurrentIndex(0)
            self.price.setValue(float(product.unit_price.as_decimal()))
            if product.cost is not None:
                self.cost.setValue(float(product.cost.as_decimal()))
            self.stock.setValue(product.stock)
            self.min_stock.setValue(product.min_stock)
            self.description.setText(product.description)
            self.stock.setEnabled(False)

    def _validate_and_accept(self) -> None:
        if not self.code.text().strip():
            QMessageBox.warning(self, "Datos incompletos", "El código es obligatorio.")
            return
        if not self.name.text().strip():
            QMessageBox.warning(self, "Datos incompletos", "El nombre es obligatorio.")
            return
        if self.price.value() <= 0:
            QMessageBox.warning(self, "Datos incompletos", "El precio debe ser mayor a cero.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "code": self.code.text().strip(),
            "name": self.name.text().strip(),
            "unit_price": Money.from_input(f"{self.price.value():.2f}"),
            "cost": Money.from_input(f"{self.cost.value():.2f}") if self.cost.value() > 0 else None,
            "category_id": self.category.currentData(),
            "stock": self.stock.value(),
            "min_stock": self.min_stock.value(),
            "description": self.description.text().strip(),
        }


# --------------------------------------------------------------------------- #
# Cobro
# --------------------------------------------------------------------------- #

PAYMENT_METHODS = (
    ("EFECTIVO", "Efectivo", "\U0001FA99"),
    ("CREDITO", "Crédito", "\U0001F4D2"),
    ("TRANSFERENCIA", "Transferencia", "\U0001F3E6"),
    ("TARJETA", "Tarjeta", "\U0001F4B3"),
    ("MIXTO", "Mixto", "\U0001F500"),
)


class PaymentSelection:
    """Resultado del diálogo de cobro."""

    def __init__(self, payments: list[tuple[str, Money]], tendered: Money | None, print_receipt: bool):
        self.payments = payments
        self.tendered = tendered
        self.print_receipt = print_receipt


def _money_from(spin: QDoubleSpinBox) -> Money:
    return Money.from_input(f"{spin.value():.2f}")


def _parse_money_input(text: str) -> Money | None:
    """Parsea texto manual de importe (``$ 1.234,56``, ``1,234.56``, ``1234``)."""
    raw = (text or "").strip().replace("$", "").replace(" ", "")
    if not raw:
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        return Money.from_input(raw)
    except Exception:
        return None


def _make_amount_spin(value: Money) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0, 99999999)
    spin.setDecimals(2)
    spin.setPrefix("$ ")
    spin.setGroupSeparatorShown(True)
    spin.setValue(float(value.as_decimal()))
    return spin


class PaymentDialog(QDialog):
    """Cobro con selección de método por icono y cobro con/sin impresión."""

    def __init__(self, total: Money, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cobro")
        self.setMinimumWidth(480)
        self._total = total

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(make_label("TOTAL A COBRAR", object_name="kpiLabel", alignment=Qt.AlignCenter))
        layout.addWidget(make_label(total.format(), object_name="totalAmount", alignment=Qt.AlignCenter))

        self.method_buttons: list[QToolButton] = []
        self._method_group = QButtonGroup(self)
        self._method_group.setExclusive(True)
        grid = QGridLayout()
        grid.setSpacing(8)
        for col, (code, label, glyph) in enumerate(PAYMENT_METHODS):
            button = QToolButton()
            button.setObjectName("payMethod")
            button.setText(label)
            button.setIcon(glyph_icon(glyph))
            button.setIconSize(QSize(32, 32))
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setProperty("method", code)
            self._method_group.addButton(button)
            self.method_buttons.append(button)
            grid.addWidget(button, 0, col)
        layout.addLayout(grid)
        self._method_buttons_by_code = {b.property("method"): b for b in self.method_buttons}
        self._method_buttons_by_code["EFECTIVO"].setChecked(True)

        # Área contextual según el método
        self._mixto_label = make_label("Seleccioná los montos a cobrar", object_name="muted")
        layout.addWidget(self._mixto_label)
        self._mixto_label.setVisible(False)

        self.credit_info = make_label("Venta a cuenta corriente (fiado). Sin vuelto.", object_name="muted")
        layout.addWidget(self.credit_info)
        self.credit_info.setVisible(False)

        self.cash_row = QWidget()
        cash_layout = QHBoxLayout(self.cash_row)
        cash_layout.setContentsMargins(0, 0, 0, 0)
        cash_layout.addWidget(QLabel("Recibido (efectivo):"))
        self.tendered = _make_amount_spin(total)
        cash_layout.addWidget(self.tendered, 1)
        layout.addWidget(self.cash_row)

        self.mix_row: dict[str, tuple[QLabel, QDoubleSpinBox]] = {}
        for code, label in (("EFECTIVO", "Efectivo"), ("TARJETA", "Tarjeta"), ("TRANSFERENCIA", "Transferencia")):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(QLabel(f"${label}:"))
            spin = _make_amount_spin(Money.zero())
            row_layout.addWidget(spin, 1)
            self.mix_row[code] = (QLabel(), spin)
            self.mix_row[code][0].setObjectName("muted")
            row_layout.addWidget(self.mix_row[code][0])
            layout.addWidget(row)
            row.setVisible(False)
        self.mix_row["EFECTIVO"][1].setValue(float(total.as_decimal()))

        self.change_label = make_label("", object_name="muted", alignment=Qt.AlignRight)
        layout.addWidget(self.change_label)

        self._method_group.buttonToggled.connect(self._on_method_toggled)
        self.tendered.valueChanged.connect(lambda _: self._update_feedback())
        for _, spin in self.mix_row.values():
            spin.valueChanged.connect(lambda _: self._update_feedback())
        self._on_method_toggled()
        self._update_feedback()

        buttons = QHBoxLayout()
        self.print_btn = QPushButton(with_shortcut("Cobrar e imprimir", "F8"))
        self.print_btn.setObjectName("primary")
        self.quiet_btn = QPushButton(with_shortcut("Cobrar sin imprimir", "F9"))
        self.quiet_btn.setObjectName("ghost")
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setObjectName("ghost")
        self.print_btn.clicked.connect(lambda: self._finalize(print_receipt=True))
        self.quiet_btn.clicked.connect(lambda: self._finalize(print_receipt=False))
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.quiet_btn)
        buttons.addWidget(self.print_btn)
        layout.addLayout(buttons)

        QShortcut(QKeySequence("F8"), self, self.print_btn.click)
        QShortcut(QKeySequence("F9"), self, self.quiet_btn.click)

        self._selection: PaymentSelection | None = None
        self.print_btn.setToolTip("F8")
        self.quiet_btn.setToolTip("F9")

    # ------------------------------------------------------------------ #
    # Estado / validación
    # ------------------------------------------------------------------ #

    def _selected_method(self) -> str:
        for button in self.method_buttons:
            if button.isChecked():
                return str(button.property("method"))
        return "EFECTIVO"

    def _on_method_toggled(self) -> None:
        method = self._selected_method()
        is_cash = method == "EFECTIVO"
        is_mixed = method == "MIXTO"
        is_credit = method == "CREDITO"
        self.cash_row.setVisible(is_cash)
        self.credit_info.setVisible(is_credit)
        self._mixto_label.setVisible(is_mixed)
        for code in self.mix_row:
            self.mix_row[code][0].parentWidget().setVisible(is_mixed)
        self._update_feedback()

    def _mix_sum(self) -> Money:
        total = Money.zero()
        for _, spin in self.mix_row.values():
            total = total + _money_from(spin)
        return total

    def _update_feedback(self) -> None:
        method = self._selected_method()
        if method == "EFECTIVO":
            change = _money_from(self.tendered) - self._total
            change = max(change, Money.zero())
            self.change_label.setText(f"Vuelto: {change.format()}")
        elif method == "MIXTO":
            diff = self._mix_sum() - self._total
            if diff == Money.zero():
                self.change_label.setText("Medios completos.")
            elif diff > Money.zero():
                self.change_label.setText(f"Sobran: {diff.format()}")
            else:
                self.change_label.setText(f"Faltan: {abs(diff).format()}")
        else:
            self.change_label.setText("")

    def _validate(self) -> str | None:
        method = self._selected_method()
        if method == "EFECTIVO":
            if _money_from(self.tendered) < self._total:
                return "El efectivo recibido es menor al total."
        elif method == "MIXTO":
            if self._mix_sum() != self._total:
                return "Los medios de pago deben sumar exactamente el total."
        return None

    def _finalize(self, print_receipt: bool) -> None:
        error = self._validate()
        if error:
            QMessageBox.warning(self, "Importe insuficiente", error)
            return
        self._selection = self.build_selection(print_receipt)
        self.accept()

    def build_selection(self, print_receipt: bool) -> PaymentSelection:
        """Construye los pagos (1-3) según el método seleccionado."""
        method = self._selected_method()
        if method == "MIXTO":
            payments = []
            for code in ("EFECTIVO", "TARJETA", "TRANSFERENCIA"):
                amount = _money_from(self.mix_row[code][1])
                if amount > Money.zero():
                    payments.append((code, amount))
            return PaymentSelection(payments, _money_from(self.mix_row["EFECTIVO"][1]), print_receipt)
        payments = [(method, self._total)]
        tendered = _money_from(self.tendered) if method == "EFECTIVO" else None
        return PaymentSelection(payments, tendered, print_receipt)

    def selection(self) -> PaymentSelection | None:
        return self._selection


class ChargeAmountDialog(QDialog):
    """Ajusta el monto a cobrar: entrada manual o descuento/incremento porcentual."""

    def __init__(self, subtotal: Money, current: Money, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ajustar monto a cobrar")
        self.setMinimumWidth(420)
        self._subtotal = subtotal

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(make_label("SUBTOTAL DE LOS ÍTEMS", object_name="kpiLabel", alignment=Qt.AlignCenter))
        layout.addWidget(make_label(subtotal.format(), object_name="totalAmount", alignment=Qt.AlignCenter))

        form = QFormLayout()

        self.manual_btn = QRadioButton("Monto manual")
        self.manual_btn.setChecked(True)
        self.amount_input = QLineEdit()
        self.amount_input.setText(current.format().replace(" ", ""))
        self.amount_input.setPlaceholderText("Ej.: 1.234,56")
        form.addRow(self.manual_btn, self.amount_input)

        self.pct_btn = QRadioButton("Porcentaje")
        self.pct_row = QWidget()
        pct_layout = QHBoxLayout(self.pct_row)
        pct_layout.setContentsMargins(0, 0, 0, 0)
        self.pct_type = QComboBox()
        self.pct_type.addItem("Descontar", -1)
        self.pct_type.addItem("Agregar", 1)
        self.pct_value = QDoubleSpinBox()
        self.pct_value.setRange(0.01, 999.00)
        self.pct_value.setDecimals(2)
        self.pct_value.setValue(10.00)
        self.pct_value.setSuffix(" %")
        pct_layout.addWidget(self.pct_type)
        pct_layout.addWidget(self.pct_value, 1)
        self.pct_row.setVisible(False)
        form.addRow(self.pct_btn, self.pct_row)
        layout.addLayout(form)

        self.preview = make_label("", object_name="muted", alignment=Qt.AlignCenter)
        layout.addWidget(self.preview)

        hint = make_label("El recibo y los pagos se calculan sobre el nuevo total.",
                          object_name="muted", alignment=Qt.AlignCenter)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Aplicar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._group = QButtonGroup(self)
        self._group.addButton(self.manual_btn)
        self._group.addButton(self.pct_btn)
        self.manual_btn.toggled.connect(self._on_mode)
        self.amount_input.textChanged.connect(lambda _: self._update_preview())
        self.pct_type.currentIndexChanged.connect(lambda _: self._update_preview())
        self.pct_value.valueChanged.connect(lambda _: self._update_preview())

        self._charge: Money | None = None
        self._update_preview()

    def _on_mode(self, _checked: bool) -> None:
        manual = self.manual_btn.isChecked()
        self.amount_input.setEnabled(manual)
        self.pct_row.setVisible(not manual)
        if manual:
            self.amount_input.setFocus()
        else:
            self.pct_value.setFocus()
        self._update_preview()

    def _manual_amount(self) -> Money | None:
        amount = _parse_money_input(self.amount_input.text())
        if amount is None or amount <= Money.zero() or amount > self._subtotal:
            return None
        return amount

    def _pct_amount(self) -> Money | None:
        pct = Decimal(f"{self.pct_value.value():.2f}")
        factor = Decimal("1") + (pct / Decimal("100")) * Decimal(self.pct_type.currentData())
        value = self._subtotal * factor
        if value <= Money.zero():
            return None
        return value

    def _computed(self) -> Money | None:
        return self._manual_amount() if self.manual_btn.isChecked() else self._pct_amount()

    def _update_preview(self) -> None:
        value = self._computed()
        if value is None:
            self.preview.setText("Importe inválido (debe ser mayor a cero y no superar el subtotal).")
        else:
            diff = self._subtotal - value
            if diff > Money.zero():
                text = f"Nuevo total: {value.format()}  ·  Descuento: {diff.format()}"
            elif diff < Money.zero():
                text = f"Nuevo total: {value.format()}  ·  Recargo: {abs(diff).format()}"
            else:
                text = f"Nuevo total: {value.format()}"
            self.preview.setText(text)

    def _accept(self) -> None:
        value = self._computed()
        if value is None:
            QMessageBox.warning(self, "Importe inválido", "Ingresá un monto mayor a cero y que no supere el subtotal.")
            return
        self._charge = value
        self.accept()

    def charge_amount(self) -> Money | None:
        return self._charge


# --------------------------------------------------------------------------- #
# Ajuste de stock
# --------------------------------------------------------------------------- #


class AdjustStockDialog(QDialog):
    def __init__(self, product: ProductDTO, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Ajustar stock: {product.name}")
        self.setMinimumWidth(380)

        form = QFormLayout()
        delta = QSpinBox()
        delta.setRange(-999999, 999999)
        delta.setValue(0)
        reason = QComboBox()
        for r in ("COMPRA", "AJUSTE", "DEVOLUCION", "PERDIDA", "OTRO"):
            reason.addItem(r.title(), r)
        note = QLineEdit()
        note.setPlaceholderText("Observación (opcional)")

        form.addRow("Cantidad (positiva/negativa):", delta)
        form.addRow("Motivo:", reason)
        form.addRow("Nota:", note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Aplicar")
        buttons.accepted.connect(self._click_validate(delta))
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(make_label(f"Stock actual: {product.stock}", object_name="muted"))
        layout.addLayout(form)

        self._delta = delta
        self._reason = reason
        self._note = note

    def _click_validate(self, delta: QSpinBox):
        def _go() -> None:
            if delta.value() == 0:
                QMessageBox.warning(self, "Ajuste inválido", "La cantidad debe ser distinta de cero.")
                return
            self.accept()

        return _go

    def values(self) -> tuple[int, str, str]:
        return self._delta.value(), self._reason.currentData(), self._note.text().strip()


# --------------------------------------------------------------------------- #
# Movimientos de stock
# --------------------------------------------------------------------------- #


class MovementsDialog(QDialog):
    def __init__(self, title: str, movements: list[StockMovementDTO], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        table = make_table(["Fecha", "Movimiento", "Motivo", "Nota"])
        table.horizontalHeader().setStretchLastSection(True)
        for m in movements:
            row = table.rowCount()
            table.insertRow(row)
            delta = f"{m.delta:+d}"
            table.setItem(row, 0, make_table_item(m.created_at.strftime("%d/%m/%Y %H:%M")))
            table.setItem(row, 1, make_table_item(delta))
            table.setItem(row, 2, make_table_item(m.reason))
            table.setItem(row, 3, make_table_item(m.note))
        layout.addWidget(table)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignRight | Qt.AlignBottom)


def make_table_item(text: str):
    from PySide6.QtWidgets import QTableWidgetItem

    item = QTableWidgetItem(str(text))
    return item


# --------------------------------------------------------------------------- #
# Recibo / detalle de venta
# --------------------------------------------------------------------------- #


class ReceiptDialog(QDialog):
    """Vista previa del recibo con impresión y guardado en PDF."""

    def __init__(self, html: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recibo")
        self.resize(360, 520)
        layout = QVBoxLayout(self)

        viewer = QTextBrowser()
        viewer.setHtml(html)
        layout.addWidget(viewer)

        buttons = QHBoxLayout()
        self.print_btn = QPushButton("Imprimir")
        self.pdf_btn = QPushButton("Guardar PDF")
        close_btn = QPushButton("Cerrar")
        self.print_btn.setObjectName("ghost")
        self.pdf_btn.setObjectName("ghost")
        close_btn.setObjectName("primary")
        self.print_btn.clicked.connect(lambda: self._print(html))
        self.pdf_btn.clicked.connect(lambda: self._save_pdf(html))
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.print_btn)
        buttons.addWidget(self.pdf_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    @staticmethod
    def build_document(html: str) -> QTextDocument:
        doc = QTextDocument()
        doc.setHtml(html)
        return doc

    def _make_printer(self, output_format: QPrinter.OutputFormat | None = None) -> QPrinter:
        printer = QPrinter()
        printer.setPageSize(QPageSize(QPageSize.A5))
        printer.setPageMargins(QMarginsF(8, 8, 8, 8), QPageLayout.Unit.Millimeter)
        if output_format is not None:
            printer.setOutputFormat(output_format)
        return printer

    def _print(self, html: str) -> None:
        from app.infrastructure.printers.ticket_esc_pos import print_receipt_ticket

        if print_receipt_ticket(html):
            return
        printer = self._make_printer()
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.build_document(html).print_(printer)

    def _save_pdf(self, html: str) -> None:
        from PySide6.QtWidgets import QFileDialog

        target, _ = QFileDialog.getSaveFileName(self, "Guardar recibo", "recibo.pdf", "PDF (*.pdf)")
        if not target:
            return
        printer = self._make_printer(QPrinter.PdfFormat)
        printer.setOutputFileName(target)
        self.build_document(html).print_(printer)


# --------------------------------------------------------------------------- #
# Cancelación de tickets del día
# --------------------------------------------------------------------------- #


class CancelTicketDialog(QDialog):
    """Ventana con el histórico de tickets del día para cancelar (anular) uno."""

    def __init__(self, commands: CommandBus, queries: QueryBus, settings, parent=None):
        super().__init__(parent)
        self._commands = commands
        self._queries = queries
        self._settings = settings
        self._sales: list[SaleDTO] = []
        self.setWindowTitle("Cancelar ticket del día")
        self.resize(760, 480)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(make_label("Tickets del día", object_name="pageTitle"))

        self.info = make_label("", object_name="muted")
        layout.addWidget(self.info)

        self.table = make_table(
            ["Recibo", "Caja", "Hora", "Ítems", "Total", "Método", "Estado"],
            stretch_column=0,
        )
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        refresh_btn = QPushButton("Actualizar")
        detail_btn = QPushButton("Ver detalle")
        self.cancel_btn = QPushButton("Cancelar ticket seleccionado")
        close_btn = QPushButton("Cerrar")
        for btn in (refresh_btn, detail_btn, self.cancel_btn):
            btn.setObjectName("ghost")
        close_btn.setObjectName("primary")
        refresh_btn.clicked.connect(self.refresh)
        detail_btn.clicked.connect(self._detail)
        self.cancel_btn.clicked.connect(self._void)
        close_btn.clicked.connect(self.accept)
        actions.addWidget(refresh_btn)
        actions.addWidget(detail_btn)
        actions.addWidget(self.cancel_btn)
        actions.addStretch(1)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        self.refresh()

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        today = datetime.now().date()
        self._sales = self._queries.ask(
            GetSalesHistoryQuery(
                start=datetime.combine(today, time.min),
                end=datetime.combine(today, time.max),
            )
        )
        self.table.setRowCount(0)
        for sale in self._sales:
            from app.interface.terminal_colors import terminal_label, terminal_row_color

            row = self.table.rowCount()
            self.table.insertRow(row)
            recibo_item = QTableWidgetItem(sale.receipt_number)
            caja_item = QTableWidgetItem(terminal_label(sale.receipt_number))
            background = terminal_row_color(sale.receipt_number)
            recibo_item.setBackground(background)
            caja_item.setBackground(background)
            self.table.setItem(row, 0, recibo_item)
            self.table.setItem(row, 1, caja_item)
            self.table.setItem(row, 2, QTableWidgetItem(sale.created_at.strftime("%H:%M")))
            self.table.setItem(row, 3, QTableWidgetItem(str(sale.item_count)))
            self.table.setItem(row, 4, QTableWidgetItem(sale.total.format()))
            self.table.setItem(row, 5, QTableWidgetItem(sale.methods_label))
            self.table.setItem(row, 6, QTableWidgetItem(sale.status))
        self.info.setText(f"{len(self._sales)} tickets emitidos hoy")

    def _selected(self) -> SaleDTO | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._sales):
            return self._sales[row]
        return None

    def _detail(self) -> None:
        sale = self._selected()
        if sale is None:
            QMessageBox.information(self, "Selección", "Seleccione un ticket.")
            return
        sale = self._queries.ask(GetSaleQuery(sale_id=sale.id))
        html = render_receipt_html(sale, self._settings.store)
        ReceiptDialog(html, self).exec()

    def _void(self) -> None:
        sale = self._selected()
        if sale is None:
            QMessageBox.information(self, "Selección", "Seleccione un ticket para cancelar.")
            return
        if sale.status == "ANULADA":
            QMessageBox.information(self, "Cancelación", "El ticket ya está anulado.")
            return
        question = QMessageBox.question(
            self,
            "Cancelar ticket",
            f"¿Cancelar el ticket {sale.receipt_number} por {sale.total.format()}?\n"
            "Se restituirá el stock. Esta acción no se puede deshacer.",
        )
        if question != QMessageBox.Yes:
            return
        try:
            self._commands.execute(VoidSaleCommand(sale_id=sale.id, reason="Cancelado por el cajero"))
        except DomainError as exc:
            show_domain_error(self, exc)
        self.refresh()


# --------------------------------------------------------------------------- #
# Apartados
# --------------------------------------------------------------------------- #

ABONO_METHODS = (
    ("EFECTIVO", "Efectivo"),
    ("TARJETA", "Tarjeta"),
    ("TRANSFERENCIA", "Transferencia"),
    ("CREDITO", "Crédito"),
)


def _method_combo() -> QComboBox:
    combo = QComboBox()
    for code, label in ABONO_METHODS:
        combo.addItem(label, code)
    return combo


def _apartado_total_label(apartado: ApartadoDTO) -> str:
    return (
        f"Total: {apartado.total.format()}  ·  Abonado: {apartado.amount_paid.format()}  ·  "
        f"Saldo: {apartado.balance.format()}"
    )


class ApartadoDialog(QDialog):
    """Crear un apartado: datos del cliente, productos y abono inicial opcional."""

    def __init__(self, queries: QueryBus, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nuevo apartado")
        self.setMinimumSize(640, 560)
        self._queries = queries
        self._catalog = [p for p in queries.ask(GetCatalogQuery()) if p.active]
        self._selected: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        self.client_name = QLineEdit()
        self.client_name.setPlaceholderText("Nombre del cliente")
        self.client_phone = QLineEdit()
        self.client_phone.setPlaceholderText("Teléfono (opcional)")
        self.note = QLineEdit()
        self.note.setPlaceholderText("Observación (opcional)")
        form.addRow("Cliente:", self.client_name)
        form.addRow("Teléfono:", self.client_phone)
        form.addRow("Observación:", self.note)
        layout.addLayout(form)

        search = QLineEdit()
        search.setPlaceholderText("Buscar producto por nombre o código…")
        search.returnPressed.connect(lambda: self._add_by_text(search.text()))
        add_btn = QPushButton("Agregar")
        add_btn.setObjectName("ghost")
        add_btn.clicked.connect(lambda: self._add_by_text(search.text()))
        search_row = QHBoxLayout()
        search_row.addWidget(search, 1)
        search_row.addWidget(add_btn)
        layout.addLayout(search_row)

        self.items_table = make_table(["Producto", "Precio", "Cant.", "Subtotal"], stretch_column=0)
        self.items_table.setMinimumHeight(180)
        layout.addWidget(self.items_table, 1)

        abono_row = QWidget()
        abono_layout = QHBoxLayout(abono_row)
        abono_layout.setContentsMargins(0, 0, 0, 0)
        abono_layout.addWidget(QLabel("Abono inicial:"))
        self.abono = QDoubleSpinBox()
        self.abono.setRange(0, 99999999)
        self.abono.setDecimals(2)
        self.abono.setPrefix("$ ")
        self.abono.setValue(0)
        self.method = _method_combo()
        abono_layout.addWidget(self.abono)
        abono_layout.addWidget(self.method)
        abono_layout.addWidget(make_label("(opcional)", object_name="muted"))
        abono_layout.addStretch(1)
        layout.addWidget(abono_row)

        self.total_label = make_label("", object_name="muted", alignment=Qt.AlignRight)
        layout.addWidget(self.total_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Guardar apartado")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.abono.valueChanged.connect(lambda _: self._update_total())
        self._update_total()

    # ------------------------------------------------------------------ #

    def _add_by_sku(self, product: ProductDTO) -> None:
        for entry in self._selected:
            if entry["product"].id == product.id:
                if entry["qty"] >= product.stock:
                    QMessageBox.warning(self, "Stock", f"No hay más stock de {product.name}.")
                    return
                entry["qty"] += 1
                self._render_items()
                return
        self._selected.append({"product": product, "qty": 1})
        self._render_items()

    def _add_by_text(self, text: str) -> None:
        term = (text or "").strip().lower()
        if not term:
            return
        product = next(
            (p for p in self._catalog if search_matches(term, p.name, p.code)),
            None,
        )
        if product is None:
            QMessageBox.information(self, "Producto desconocido", f"No se encontró '{text}'.")
            return
        self._add_by_sku(product)

    def _render_items(self) -> None:
        table = self.items_table
        table.setRowCount(0)
        for index, entry in enumerate(self._selected):
            product = entry["product"]
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(product.name))
            table.setItem(row, 1, QTableWidgetItem(product.unit_price.format()))
            spin = QSpinBox()
            spin.setMinimum(1)
            spin.setMaximum(max(1, product.stock))
            spin.setValue(entry["qty"])
            spin.valueChanged.connect(lambda value, r=row: self._on_qty(r, value))
            table.setCellWidget(row, 2, spin)
            table.setItem(row, 3, QTableWidgetItem((product.unit_price * entry["qty"]).format()))
        self._update_total()

    def _on_qty(self, row: int, value: int) -> None:
        entry = self._selected[row]
        entry["qty"] = max(1, value)
        product = entry["product"]
        self.items_table.setItem(row, 3, QTableWidgetItem((product.unit_price * entry["qty"]).format()))
        self._update_total()

    def _update_total(self) -> None:
        total = sum((e["product"].unit_price * e["qty"] for e in self._selected), Money.zero())
        if self.abono.value() > 0:
            text = f"Total: {total.format()}  ·  Abono inicial: {Money.from_input(f'{self.abono.value():.2f}').format()}"
        else:
            text = f"Total: {total.format()}  ·  Sin abono inicial"
        self.total_label.setText(text)

    def _validate_and_accept(self) -> None:
        if not self.client_name.text().strip():
            QMessageBox.warning(self, "Datos incompletos", "El nombre del cliente es obligatorio.")
            return
        if not self._selected:
            QMessageBox.warning(self, "Datos incompletos", "Agregue al menos un producto.")
            return
        if self.abono.value() > 0:
            total = sum((e["product"].unit_price * e["qty"] for e in self._selected), Money.zero())
            if Money.from_input(f"{self.abono.value():.2f}") > total:
                QMessageBox.warning(self, "Abono inválido", "El abono inicial no puede superar el total.")
                return
        self.accept()

    def values(self) -> dict:
        abono = Money.from_input(f"{self.abono.value():.2f}") if self.abono.value() > 0 else None
        return {
            "client_name": self.client_name.text().strip(),
            "client_phone": self.client_phone.text().strip(),
            "note": self.note.text().strip(),
            "items": [(e["product"].code, e["qty"]) for e in self._selected],
            "initial_abono": abono,
            "abono_method": self.method.currentData(),
        }


class AbonoDialog(QDialog):
    """Registrar un abono (pago parcial) sobre un apartado."""

    def __init__(self, apartado: ApartadoDTO, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Abono · {apartado.client_name}")
        self.setMinimumWidth(360)
        self._apartado = apartado

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(make_label(_apartado_total_label(apartado), object_name="muted", alignment=Qt.AlignCenter))
        layout.addWidget(make_label(apartado.balance.format(), object_name="totalAmount", alignment=Qt.AlignCenter))

        form = QFormLayout()
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.01, 99999999)
        self.amount.setDecimals(2)
        self.amount.setPrefix("$ ")
        self.amount.setValue(float(apartado.balance.as_decimal()))
        self.method = _method_combo()
        form.addRow("Monto:", self.amount)
        form.addRow("Método:", self.method)
        layout.addLayout(form)

        self.info = make_label("", object_name="muted", alignment=Qt.AlignCenter)
        layout.addWidget(self.info)
        self.amount.valueChanged.connect(self._update_info)
        self._update_info()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Registrar abono")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_info(self) -> None:
        amount = Money.from_input(f"{self.amount.value():.2f}")
        if amount > self._apartado.balance:
            self.info.setText(f"Supera el saldo ({self._apartado.balance.format()}).")
        else:
            self.info.setText("")

    def _accept(self) -> None:
        amount = Money.from_input(f"{self.amount.value():.2f}")
        if amount > self._apartado.balance:
            QMessageBox.warning(self, "Abono inválido", f"No puede superar el saldo de {self._apartado.balance.format()}.")
            return
        self.accept()

    def values(self) -> tuple[Money, str]:
        return Money.from_input(f"{self.amount.value():.2f}"), self.method.currentData()


class ApartadoDetailDialog(QDialog):
    """Detalle de un apartado: ítems y abonos registrados."""

    def __init__(self, apartado: ApartadoDTO, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Apartado · {apartado.client_name}")
        self.resize(640, 520)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        phone = f" · Tel: {apartado.client_phone}" if apartado.client_phone else ""
        header = make_label(
            f"{apartado.client_name}{phone}\n{_apartado_total_label(apartado)} · Estado: {apartado.status}",
            object_name="muted",
        )
        layout.addWidget(header)

        layout.addWidget(make_label("Productos", object_name="sectionTitle"))
        items = make_table(["Producto", "Cant.", "Precio", "Subtotal"], stretch_column=0)
        for item in apartado.items:
            row = items.rowCount()
            items.insertRow(row)
            items.setItem(row, 0, QTableWidgetItem(item.product_name))
            items.setItem(row, 1, QTableWidgetItem(str(item.quantity)))
            items.setItem(row, 2, QTableWidgetItem(item.unit_price.format()))
            items.setItem(row, 3, QTableWidgetItem(item.subtotal.format()))
        layout.addWidget(items, 1)

        layout.addWidget(make_label(f"Abonos ({apartado.abonos_count})", object_name="sectionTitle"))
        abonos = make_table(["Fecha", "Método", "Monto"], stretch_column=1)
        for abono in apartado.abonos:
            row = abonos.rowCount()
            abonos.insertRow(row)
            abonos.setItem(row, 0, QTableWidgetItem(abono.created_at.strftime("%d/%m/%Y %H:%M")))
            abonos.setItem(row, 1, QTableWidgetItem(abono.method.value))
            abonos.setItem(row, 2, QTableWidgetItem(abono.amount.format()))
        layout.addWidget(abonos, 1)

        close = QPushButton("Cerrar")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignRight | Qt.AlignBottom)


# --------------------------------------------------------------------------- #
# Caja: apertura, movimientos, devoluciones y corte
# --------------------------------------------------------------------------- #


def _money_spin(*, value: float = 0.0, decimals: int = 2) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0, 999999999)
    spin.setDecimals(decimals)
    spin.setPrefix("$ ")
    spin.setGroupSeparatorShown(True)
    spin.setValue(value)
    return spin


class OpenCashDayDialog(QDialog):
    """Iniciar una jornada de caja pidiendo el efectivo inicial (fondo)."""

    def __init__(self, commands: CommandBus, opened_by: str = "", parent=None):
        super().__init__(parent)
        self._commands = commands
        self._opened_by = opened_by
        self.setWindowTitle("Iniciar jornada de caja")
        self.setMinimumWidth(420)

        form = QFormLayout()
        form.addRow("Cajero/a:", make_label(opened_by or "—"))
        self.initial_cash = _money_spin()
        self.note = QLineEdit()
        self.note.setPlaceholderText("Nota (opcional)")
        form.addRow("¿Con cuánto efectivo inicia?", self.initial_cash)
        form.addRow("Nota:", self.note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Iniciar jornada")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._start)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(make_label("Abrir caja", object_name="pageTitle"))
        layout.addSpacing(4)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.initial_cash.setFocus()

    def _start(self) -> None:
        try:
            self._commands.execute(
                OpenCashDayCommand(
                    opening_cash=str(self.initial_cash.value()),
                    opened_by=self._opened_by,
                    note=self.note.text().strip(),
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        QMessageBox.information(
            self,
            "Jornada iniciada",
            f"Jornada iniciada con {Money(self.initial_cash.value()).format()} de efectivo.",
        )
        self.accept()


class CashMovementDialog(QDialog):
    """Registrar un ingreso o salida de efectivo de la caja."""

    IN_REASONS = (("INGRESO", "Ingreso de efectivo"),)
    OUT_REASONS = (("RETIRO", "Retiro de efectivo"), ("GASTO", "Gasto / pago"))

    def __init__(self, commands: CommandBus, movement_type: str, parent=None):
        super().__init__(parent)
        self._commands = commands
        self._movement_type = movement_type.upper()
        is_in = self._movement_type == "ENTRADA"
        self.setWindowTitle("Ingreso de efectivo" if is_in else "Salida de efectivo")
        self.setMinimumWidth(420)

        form = QFormLayout()
        self.reason = QComboBox()
        for code, label in (self.IN_REASONS if is_in else self.OUT_REASONS):
            self.reason.addItem(label, code)
        self.amount = _money_spin()
        self.note = QLineEdit()
        self.note.setPlaceholderText("Nota (opcional)")

        form.addRow("Concepto:", self.reason)
        form.addRow("Monto:", self.amount)
        form.addRow("Nota:", self.note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Registrar")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._register)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(make_label(self.windowTitle(), object_name="pageTitle"))
        layout.addSpacing(4)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.amount.setFocus()

    def _register(self) -> None:
        try:
            self._commands.execute(
                RegisterCashMovementCommand(
                    movement_type=self._movement_type,
                    amount=str(self.amount.value()),
                    reason=self.reason.currentData(),
                    note=self.note.text().strip(),
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        self.accept()


class RefundLineFormDialog(QDialog):
    """Formulario para devolver una partida de una venta completada."""

    def __init__(self, commands: CommandBus, sale: SaleDTO, item_index: int, parent=None):
        super().__init__(parent)
        self._commands = commands
        self._sale = sale
        self._item_index = item_index
        item = sale.items[item_index]
        self.setWindowTitle(f"Devolver partida — {item.product_name}")
        self.setMinimumWidth(440)

        form = QFormLayout()
        sold = make_label(f"{item.quantity} × {item.unit_price.format()}", object_name="muted")
        self.available = item.remaining_qty
        self.qty = QSpinBox()
        self.qty.setRange(1, max(1, self.available))
        self.qty.setValue(self.available)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Motivo (opcional)")
        self.cash_back = QCheckBox("Reintegrar el efectivo al cliente")
        paid_cash = any(p.method == PaymentMethod.CASH for p in sale.payments)
        self.cash_back.setChecked(paid_cash)
        self.amount_label = make_label("", object_name="totalAmount")

        form.addRow("Vendido:", sold)
        form.addRow("Cantidad a devolver:", self.qty)
        form.addRow("Motivo:", self.reason)
        form.addRow("", self.cash_back)
        form.addRow("Reembolso:", self.amount_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Devolver partida")
        buttons.button(QDialogButtonBox.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._refund)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.qty.valueChanged.connect(self._update_amount)
        self._update_amount()

    def _update_amount(self) -> None:
        item = self._sale.items[self._item_index]
        amount = item.unit_price * self.qty.value()
        self.amount_label.setText(amount.format())

    def _refund(self) -> None:
        item = self._sale.items[self._item_index]
        try:
            updated = self._commands.execute(
                RefundSaleItemCommand(
                    sale_id=self._sale.id,
                    item_index=self._item_index,
                    quantity=self.qty.value(),
                    reason=self.reason.text().strip(),
                    cash_payout=self.cash_back.isChecked(),
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        refunded = item.unit_price * self.qty.value()
        if self.cash_back.isChecked():
            self._open_drawer_for_cash()
        QMessageBox.information(
            self,
            "Partida devuelta",
            f"Se devolvieron {self.qty.value()} × {item.product_name} por {refunded.format()}.\n"
            f"{'Se reintegró el efectivo.' if self.cash_back.isChecked() else 'No se devolvió efectivo.'}",
        )
        self.accept()

    @staticmethod
    def _open_drawer_for_cash() -> None:
        try:
            from app.infrastructure.printers.ticket_esc_pos import kick_cash_drawer

            if not kick_cash_drawer():
                return
        except Exception:  # noqa: BLE001 - el pulso nunca debe romper la devolución
            pass


class RefundLineDialog(QDialog):
    """Lista las partidas de una venta para devolver alguna (cancelación parcial)."""

    def __init__(self, commands: CommandBus, queries: QueryBus, sale_id: int, parent=None):
        super().__init__(parent)
        self._commands = commands
        self._queries = queries
        self._sale_id = sale_id
        self.setWindowTitle("Devolver partida de ticket")
        self.resize(720, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.sale = self._queries.ask(GetSaleQuery(sale_id=sale_id))
        self.info = make_label("", object_name="muted")
        layout.addWidget(self.info)

        self.table = make_table(
            ["Producto", "Vendidos", "Devueltos", "Restantes", "Precio", ""],
            stretch_column=0,
        )
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        close_btn = QPushButton("Cerrar")
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.accept)
        actions.addStretch(1)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        self._render()

    def _render(self) -> None:
        self.table.setRowCount(0)
        if self.sale.status != "COMPLETADA":
            self.info.setText(f"Estado: {self.sale.status} · No se pueden devolver partidas.")
            return
        self.info.setText(f"{self.sale.receipt_number} · {self.sale.created_at.strftime('%d/%m/%Y %H:%M')}")
        for index, item in enumerate(self.sale.items):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(item.product_name))
            self.table.setItem(row, 1, QTableWidgetItem(str(item.quantity)))
            self.table.setItem(row, 2, QTableWidgetItem(str(item.refunded_qty)))
            self.table.setItem(row, 3, QTableWidgetItem(str(item.remaining_qty)))
            self.table.setItem(row, 4, QTableWidgetItem(item.subtotal.format()))
            if item.remaining_qty > 0:
                refund_btn = QPushButton("Devolver")
                refund_btn.setObjectName("ghost")
                refund_btn.clicked.connect(lambda _=False, i=index: self._refund(i))
                self.table.setCellWidget(row, 5, refund_btn)
            else:
                self.table.setItem(row, 5, QTableWidgetItem("—"))
            self.table.setRowHeight(row, 38)
        self.table.resizeColumnToContents(5)

    def _refund(self, index: int) -> None:
        dialog = RefundLineFormDialog(self._commands, self.sale, index, self)
        if dialog.exec() != RefundLineFormDialog.Accepted:
            return
        self.sale = self._queries.ask(GetSaleQuery(sale_id=self._sale_id))
        self._render()


MOVEMENT_LABELS = {
    "ENTRADA": "Ingreso",
    "SALIDA": "Salida",
    "INGRESO": "Ingreso",
    "DEVOLUCION": "Devolución",
    "RETIRO": "Retiro",
    "GASTO": "Gasto",
}


class CorteDialog(QDialog):
    """Corte de caja: resumen del día y cierre con arqueo.

    Al cerrar ofrece iniciar una nueva jornada (pide el efectivo inicial).
    """

    def __init__(self, commands: CommandBus, queries: QueryBus, opened_by: str = "", settings=None, parent=None):
        super().__init__(parent)
        self._commands = commands
        self._queries = queries
        self._opened_by = opened_by
        self._store: StoreInfo = getattr(settings, "store", None) or StoreInfo()
        self.setWindowTitle("Corte de caja")
        self.setMinimumSize(560, 620)

        self.corte = self._queries.ask(GetCorteQuery())
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        if self.corte is None:
            layout.addWidget(make_label("Corte de caja", object_name="pageTitle"))
            layout.addWidget(make_label("No hay una jornada abierta para cortar.", object_name="muted"))
            close = QPushButton("Cerrar")
            close.setObjectName("primary")
            close.clicked.connect(self.accept)
            layout.addWidget(close, alignment=Qt.AlignRight)
            return

        day: CashDayDTO = self.corte.day
        layout.addWidget(make_label("Corte de caja", object_name="pageTitle"))
        layout.addWidget(
            make_label(
                f"Jornada #{day.id} · abierta {day.opened_at.strftime('%d/%m/%Y %H:%M')} "
                f"· fondo inicial {day.opening_cash.format()}",
                object_name="muted",
            )
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.addWidget(make_label("Ventas del día", object_name="muted"), 0, 0)
        grid.addWidget(make_label(f"{self.corte.sales_count} · {self.corte.sales_total.format()}"), 0, 1)
        row = 1
        for method, amount in self.corte.by_method.items():
            grid.addWidget(make_label(method.title(), object_name="muted"), row, 0)
            grid.addWidget(make_label(amount.format()), row, 1)
            row += 1
        grid.addWidget(make_label("Ingresos de caja", object_name="muted"), row, 0)
        grid.addWidget(make_label(self.corte.cash_in_total.format()), row, 1)
        row += 1
        grid.addWidget(make_label("Egresos de caja", object_name="muted"), row, 0)
        grid.addWidget(make_label(self.corte.cash_out_total.format()), row, 1)
        row += 1
        if self.corte.refunds_total > Money.zero():
            grid.addWidget(make_label("Devoluciones", object_name="muted"), row, 0)
            grid.addWidget(make_label(self.corte.refunds_total.format()), row, 1)
            row += 1
        layout.addLayout(grid)

        expected = make_label("", object_name="totalAmount")
        expected.setText(self.corte.expected_cash.format())
        expected_row = QHBoxLayout()
        expected_row.addWidget(make_label("Efectivo esperado:"))
        expected_row.addWidget(expected)
        expected_row.addStretch(1)
        layout.addLayout(expected_row)

        form = QFormLayout()
        self.counted = _money_spin(value=float(self.corte.expected_cash.as_decimal()))
        self.arqueo_note = QLineEdit()
        self.arqueo_note.setPlaceholderText("Nota del cierre (opcional)")
        form.addRow("Efectivo contado:", self.counted)
        form.addRow("Nota:", self.arqueo_note)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        close_btn = QPushButton("Cerrar")
        close_btn.setObjectName("ghost")
        close_btn.clicked.connect(self.reject)
        self.close_day_btn = QPushButton("Cerrar caja (corte)")
        self.close_day_btn.setObjectName("primary")
        self.close_day_btn.clicked.connect(self._close_day)
        buttons.addWidget(close_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.close_day_btn)
        layout.addLayout(buttons)

    def _close_day(self) -> None:
        try:
            result = self._commands.execute(
                CloseCashDayCommand(
                    counted_cash=str(self.counted.value()),
                    note=self.arqueo_note.text().strip(),
                )
            )
        except DomainError as exc:
            show_domain_error(self, exc)
            return
        kind = result.difference_kind or "EXACTO"
        QMessageBox.information(
            self,
            "Corte realizado",
            f"Jornada cerrada.\n\n"
            f"Esperado: {result.expected_cash.format()}\n"
            f"Contado: {result.closing_cash.format()}\n"
            f"Diferencia: {result.difference.format()} ({kind})",
        )
        self._print_corte_ticket(result)
        question = QMessageBox.question(
            self,
            "Nueva jornada",
            "La caja quedó cerrada.\n¿Desea iniciar una nueva jornada ahora?",
        )
        if question == QMessageBox.Yes:
            dialog = OpenCashDayDialog(self._commands, opened_by=self._opened_by, parent=self)
            dialog.exec()
        self.accept()

    def _print_corte_ticket(self, result: CashDayDTO) -> None:
        """Imprime el ticket del cierre: tickets del día con su monto."""
        try:
            from app.infrastructure.printers.receipt import render_corte_html
            from app.infrastructure.printers.ticket_esc_pos import print_receipt_ticket

            closed = self._queries.ask(GetCorteQuery(day_id=result.id))
            if closed is None:
                return
            sales = self._queries.ask(
                GetSalesHistoryQuery(start=result.opened_at, status="COMPLETADA")
            )
            html = render_corte_html(closed, sales, self._store)
            if print_receipt_ticket(html):
                return
            ReceiptDialog(html, self).exec()
        except Exception:  # noqa: BLE001 - el ticket del corte nunca debe romper el cierre
            log.exception("Corte: no se pudo imprimir el ticket del cierre.")