"""Widgets y helpers reutilizables de la interfaz."""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QTableWidget,
    QVBoxLayout,
)


def search_matches(term: str, *values: str) -> bool:
    """Empareja un término contra valores usando comodines ``%`` y ``_``.

    - ``%`` equivale a cualquier secuencia de caracteres (incluso vacía).
    - ``_`` equivale a un solo carácter.
    - Sin comodines se comporta como una subcadena (ignora mayúsculas).
    - El patrón NO está anclado: ``camara%110`` empareja "CAMARA MOTO PREMIUM
      110/90-17 …" porque ``%`` cubre todo lo que hay entre "camara" y "110".
    """
    term = (term or "").strip().lower()
    if not term:
        return True
    candidates = (values or (None,))
    if "%" not in term and "_" not in term:
        return any(term in (v or "").lower() for v in candidates)
    pattern = re.escape(term).replace("%", ".*").replace("_", ".")
    regex = re.compile(pattern, re.IGNORECASE)
    return any(regex.search(v or "") is not None for v in candidates)


class ClickableLabel(QLabel):
    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


def glyph_icon(symbol: str, *, size: int = 44, point_size: float = 19.0) -> QIcon:
    """Icono a partir de un glifo (emoji) renderizado sobre un pixmap transparente."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing | QPainter.TextAntialiasing)
    font = QFont()
    font.setPointSizeF(point_size)
    painter.setFont(font)
    painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, symbol)
    painter.end()
    return QIcon(pixmap)


def make_label(text: str = "", *, object_name: str = "", alignment=Qt.AlignLeft) -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    label.setAlignment(alignment)
    return label


def with_shortcut(label: str, shortcut: str) -> str:
    """Muestra el atajo en el texto del botón: ``[F12] - Cobrar``."""
    return f"[{shortcut}] - {label}"


def make_table(headers: list[str], *, stretch_column: int | None = None, height: int | None = None) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.horizontalHeader().highlightSections = False
    if stretch_column is not None:
        table.horizontalHeader().setSectionResizeMode(stretch_column, QHeaderView.Stretch)
    if height:
        table.setMinimumHeight(height)
    return table


class Card(QFrame):
    """Panel blanco redondeado con título opcional."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 14, 14, 14)
        self._layout.setSpacing(8)
        if title:
            label = QLabel(title)
            label.setObjectName("sectionTitle")
            self._layout.addWidget(label)

    def add(self, widget) -> None:
        self._layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._layout.addLayout(layout)