"""Diálogo de impresión de etiquetas de código de barras."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.application.read_models import ProductDTO
from app.infrastructure.printing.label_printer import LabelPrintingService
from app.interface.widgets import make_label


class LabelPrintDialog(QDialog):
    def __init__(self, product: ProductDTO, service: LabelPrintingService, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Imprimir etiqueta: {product.name}")
        self.setMinimumWidth(420)
        self._product = product
        self._service = service

        layout = QVBoxLayout(self)
        layout.addWidget(make_label(product.code, object_name="muted"))

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        pixmap_holder = QPixmap.fromImage(service.preview_image(product))
        scaled = pixmap_holder.scaledToWidth(360, Qt.SmoothTransformation)
        self.preview.setPixmap(scaled)
        layout.addWidget(self.preview)

        form = QFormLayout()
        self.copies = QSpinBox()
        self.copies.setRange(1, 99)
        self.copies.setValue(1)
        form.addRow("Cantidad de etiquetas:", self.copies)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.print_btn = QPushButton("Imprimir")
        self.print_btn.setObjectName("primary")
        close_btn = QPushButton("Cerrar")
        close_btn.setObjectName("ghost")
        self.print_btn.clicked.connect(self._print)
        close_btn.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        buttons.addWidget(self.print_btn)
        layout.addLayout(buttons)

    def _print(self) -> None:
        ok = self._service.print_product(self._product, self.copies.value())
        if ok:
            self.accept()
        else:
            QMessageBox.information(
                self,
                "Impresión",
                "La impresión fue cancelada o no se pudo completar.\n"
                "Verifique la impresora (Brother QL-810W) y el perfil de etiqueta.",
            )