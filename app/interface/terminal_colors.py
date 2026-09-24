"""Identidad de terminal/caja y colores estables para diferenciar tickets y ventas.

Los folios tienen la forma ``R-<caja>-<secuencia>`` (p. ej. ``R-1-000123``),
de modo que el número de caja (terminal) puede deducirse del propio recibo incluso
cuando el terminal baja ventas de sus cajas hermanas. Cada caja recibe un color
estable de la paleta para pintar sus tickets en historial, reportes y cancelación.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QColor

# https://doc.qt.io/qtforpython-6/            (paleta de alto contraste, distinta de la de reportes)
TERMINAL_PALETTE = (
    "#0e7490",
    "#b91c1c",
    "#15803d",
    "#b45309",
    "#6d28d9",
    "#be185d",
    "#1d4ed8",
    "#65a30d",
    "#c2410c",
    "#0f766e",
    "#a21caf",
    "#475569",
    "#e11d48",
    "#0891b2",
    "#ca8a04",
)

_RECEIPT_CAJA_RE = re.compile(r"^R-(\d+)-")


def terminal_key(receipt_number: str) -> str:
    """Número de caja (terminal) deducido del folio ``R-<caja>-<secuencia>``."""
    match = _RECEIPT_CAJA_RE.match(receipt_number or "")
    return match.group(1) if match else "0"


def terminal_label(receipt_number: str) -> str:
    """Etiqueta legible de la caja, p. ej. ``Caja 2``."""
    return f"Caja {terminal_key(receipt_number)}"


def _palette_index(key: str) -> int:
    try:
        return int(key or "0") - 1
    except ValueError:
        return 0


def terminal_color(key: str) -> QColor:
    """Color sólido asignado a una caja (estable entre llamadas)."""
    return QColor(TERMINAL_PALETTE[_palette_index(key) % len(TERMINAL_PALETTE)])


def terminal_row_color(receipt_number: str) -> QColor:
    """Color de fondo suave para pintar la fila del ticket de una caja."""
    color = terminal_color(terminal_key(receipt_number))
    color.setAlpha(38)
    return color