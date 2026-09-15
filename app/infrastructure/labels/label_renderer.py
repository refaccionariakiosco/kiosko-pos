"""Renderizado de etiquetas de código de barras a imagen (QImage @ dpi nativo).

El lienzo se trabaja en milímetros y se escala al DPI de impresión para que
las barras queden 1:1 (la Brother QL-810W imprime a 300 dpi).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen

from app.domain.value_objects import Money
from app.infrastructure.labels.barcode import encode_barcode

MM_TO_IN = 25.4
BLACK = QColor("#111111")
GRAY = QColor("#444444")


@dataclass(frozen=True, slots=True)
class LabelSpec:
    width_mm: float = 90.0
    height_mm: float = 30.0
    dpi: int = 300


def mm_to_px(value_mm: float, dpi: int) -> int:
    return max(1, round(value_mm / MM_TO_IN * dpi))


def px_to_mm(value_px: float, dpi: int) -> float:
    return value_px / dpi * MM_TO_IN


def _font(pixel_height_mm: float, dpi: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    font = QFont("Arial")
    font.setPixelSize(mm_to_px(pixel_height_mm, dpi))
    font.setWeight(weight)
    return font


def _wrap(font: QFont, metrics: QFontMetricsF, text: str, max_px: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if metrics.horizontalAdvance(candidate) <= max_px:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:2]


def _draw_module_bars(
    painter: QPainter,
    *,
    modules: str,
    x_mm: float,
    y_mm: float,
    width_mm: float,
    height_mm: float,
    dpi: int,
) -> None:
    if not modules:
        return
    n = len(modules)
    module = (width_mm / MM_TO_IN * dpi) / n
    x_px = mm_to_px(x_mm, dpi)
    y_px = mm_to_px(y_mm, dpi)
    h_px = mm_to_px(height_mm, dpi)
    run_start = -1
    for i, bit in enumerate(modules):
        if bit == "1":
            if run_start == -1:
                run_start = i
            if i != n - 1:
                continue
        if run_start != -1:
            close = i if bit == "0" else i + 1
            start_x = x_px + round(run_start * module)
            end_x = x_px + round(close * module)
            painter.fillRect(start_x, y_px, max(1, end_x - start_x), h_px, BLACK)
            run_start = -1


def render_barcode_label(
    *,
    code: str,
    name: str,
    price: Money | None,
    spec: LabelSpec = LabelSpec(),
) -> QImage:
    """Etiqueta 90x29 mm: nombre y precio arriba, código de barras abajo."""
    dpi = spec.dpi
    image = QImage(mm_to_px(spec.width_mm, dpi), mm_to_px(spec.height_mm, dpi), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("#ffffff"))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    text_zone = spec.width_mm - 6
    name_font = _font(4.6, dpi, QFont.DemiBold)
    name_metrics = QFontMetricsF(name_font)

    # Nombre (máx. 2 líneas)
    lines = _wrap(name_font, name_metrics, name.upper(), mm_to_px(text_zone, dpi))
    y = 2.8
    for line in lines:
        painter.setFont(name_font)
        painter.setPen(QPen(BLACK))
        painter.drawText(
            QRectF(mm_to_px(3, dpi), mm_to_px(y, dpi), mm_to_px(text_zone, dpi), mm_to_px(5, dpi)),
            line,
        )
        y += 5.0

    # Precio: no se imprime, puede variar según el punto de venta.

    # Código de barras + dígitos legibles
    try:
        modules = encode_barcode(code)
    except Exception:
        modules = ""
    if modules:
        quiet = 3
        bar_y = 12.0
        bar_h = 6.6
        _draw_module_bars(
            painter,
            modules=modules,
            x_mm=quiet,
            y_mm=bar_y,
            width_mm=spec.width_mm - quiet * 2,
            height_mm=bar_h,
            dpi=dpi,
        )

        code_font = _font(3.2, dpi)
        code_metrics = QFontMetricsF(code_font)
        painter.setFont(code_font)
        painter.setPen(QPen(GRAY))
        code_w = code_metrics.horizontalAdvance(code)
        code_x = (spec.width_mm - px_to_mm(code_w, dpi)) / 2
        painter.drawText(
            QRectF(mm_to_px(code_x, dpi), mm_to_px(bar_y + bar_h + 0.5, dpi), mm_to_px(code_w, dpi), mm_to_px(4, dpi)),
            code,
        )

    painter.end()
    dots_per_meter = round(dpi / MM_TO_IN * 1000)
    image.setDotsPerMeterX(dots_per_meter)
    image.setDotsPerMeterY(dots_per_meter)
    return image


def image_to_png_bytes(image: QImage) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    ok = image.save(buffer, "PNG")
    if not ok:
        raise RuntimeError("No se pudo serializar la etiqueta a PNG.")
    return buffer.getvalue()