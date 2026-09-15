"""Impresión de etiquetas en la Brother QL-810W (90x29 mm @ 300 dpi).

Dos transportes:
- ``WindowsDriverLabelPrinter``: envía la imagen al controlador instalado de
  Windows (QPrinter). Pide confirmar impresora/perfiles en cada impresión.
- ``BrotherQLNetworkLabelPrinter``: imprime por red (puerto 9100) usando el
  paquete opcional ``brother_ql`` (modelo QL-810W, etiqueta 29x90), sin pasar
  por el controlador.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence

from PySide6.QtCore import QRectF, QSizeF, Qt
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter, QTransform
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from app.application.read_models import ProductDTO
from app.domain.value_objects import Money
from app.infrastructure.labels.label_renderer import LabelSpec, render_barcode_label
from app.settings import Settings

log = logging.getLogger(__name__)


def _rotate_for_ql(image: QImage) -> QImage:
    """Rota la etiqueta 90x29 mm (horizontal) a la orientación física de la
    Brother QL (rollo de 29 mm de ancho x 90 mm de largo, vertical)."""
    return image.transformed(QTransform().rotate(90))


class LabelPrinter(ABC):
    @abstractmethod
    def print_labels(self, images: Sequence[QImage], copies: int) -> bool: ...


class NullLabelPrinter(LabelPrinter):
    def print_labels(self, images: Sequence[QImage], copies: int) -> bool:
        log.info("Impresión de etiquetas deshabilitada (NullLabelPrinter).")
        return False


class WindowsDriverLabelPrinter(LabelPrinter):
    """Imprime usando el controlador instalado de la Brother (QPrinter)."""

    def __init__(self, spec: LabelSpec, parent: QWidget | None = None):
        self._spec = spec
        self._parent = parent

    def print_labels(self, images: Sequence[QImage], copies: int) -> bool:
        if not images:
            return False
        portrait = [_rotate_for_ql(image) for image in images]

        printer = self._printer_for_dialog()
        log.info("Etiqueta: diálogo de impresión (impresora preseleccionada: %s).", printer.printerName() or "(ninguna)")
        dialog = QPrintDialog(printer, self._parent)
        if dialog.exec() != QDialog.Accepted:
            log.info("Etiqueta: el usuario canceló el diálogo de impresión.")
            return False

        # El diálogo/controlador puede reescribir página, resolución u
        # orientación con los valores por defecto -> re-aplicar los nuestros.
        self._apply_settings(printer)

        info = QPrinterInfo.printerInfo(printer.printerName())
        if not info.isNull():
            page_mm = printer.pageLayout().pageSize().rect(QPageSize.Millimeter)
        else:
            page_mm = QRectF(0, 0, self._spec.height_mm, self._spec.width_mm)
        log.info(
            "Etiqueta: imprimiendo en '%s' | papel %.1fx%.1f mm | resolución %s ppp | tamaño página %dx%d px.",
            printer.printerName(),
            page_mm.width(),
            page_mm.height(),
            printer.resolution(),
            printer.width(),
            printer.height(),
        )

        painter = QPainter()
        if not painter.begin(printer):
            log.error("Etiqueta: QPainter no pudo iniciar sobre la impresora '%s'.", printer.printerName())
            QMessageBox.warning(self._parent, "Etiqueta", "No se pudo iniciar la impresión sobre la impresora.")
            return False
        try:
            total = max(copies, 1) * len(portrait)
            for index in range(total):
                image = portrait[index % len(portrait)]
                rect = QRectF(0, 0, printer.width(), printer.height())
                painter.setClipRect(rect)
                painter.drawImage(rect, image)
                if index != total - 1:
                    printer.newPage()
        except Exception as exc:  # noqa: BLE001 - el controlador puede fallar
            log.exception("Etiqueta: error enviando al controlador de Windows.")
            QMessageBox.warning(
                self._parent,
                "Etiqueta",
                f"No se pudo imprimir:\n{exc}",
            )
            return False
        finally:
            painter.end()
        log.info(
            "Etiqueta: %d hoja/s enviada(s) al controlador de Windows (impresora: %s).",
            total,
            printer.printerName(),
        )
        QMessageBox.information(
            self._parent,
            "Etiqueta",
            f"Etiqueta enviada a: {printer.printerName()}\nPáginas: {total}\n\n"
            "Si no sale físicamente, revisá que la impresora tenga papel y esté enchufada.",
        )
        return True

    def _apply_settings(self, printer: QPrinter, info: QPrinterInfo | None = None) -> None:
        printer.setResolution(max(self._spec.dpi, 300))
        printer.setFullPage(True)
        printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        if info is None:
            info = QPrinterInfo.printerInfo(printer.printerName())
        page_size = self._match_page_size(info, self._spec.height_mm, self._spec.width_mm)
        if page_size is not None:
            printer.setPageSize(page_size)
        else:
            printer.setPageSize(QPageSize(QSizeF(self._spec.height_mm, self._spec.width_mm), QPageSize.Unit.Millimeter))

    @staticmethod
    def _match_page_size(info: QPrinterInfo, width_mm: float, height_mm: float) -> QPageSize | None:
        """Devuelve un tamaño de página que el driver de la Brother expone como
        soportado y que coincide con la etiqueta en vertical (alto x ancho).

        La QL-810W a través de su driver devuelve sus medidas (''29mm x 90mm'',
        ''29mm'', etc.) marcadas como `QPageSize.Custom` pero igual son soportadas;
        el problema es inventar un `QPageSize(QSizeF(...))` nuevo, que Qt no mapea
        al preset del driver y el trabajo se rechaza. Por eso se usa directamente
        el objeto que reporta `QPrinterInfo.supportedPageSizes()`.
        """
        if info is None or info.isNull():
            return None
        sizes = info.supportedPageSizes()
        for name in ("29mm x 90mm", "29mm"):
            for page_size in sizes:
                if page_size.name().strip().lower() == name:
                    return page_size
        best: tuple[float, QPageSize] | None = None
        for page_size in sizes:
            rect = page_size.rect(QPageSize.Millimeter)
            diff = abs(rect.width() - width_mm) + abs(rect.height() - height_mm)
            if best is None or diff < best[0]:
                best = (diff, page_size)
        if best is not None and best[0] <= 3.0:
            return best[1]
        return None

    def _printer_for_dialog(self) -> QPrinter:
        """Crea el QPrinter sobre la Brother QL-810W si está instalada."""
        brother = next(
            (info for info in QPrinterInfo.availablePrinters() if "ql-810w" in info.printerName().lower()),
            None,
        )
        if brother is not None:
            log.info("Etiqueta: usando impresora '%s'.", brother.printerName())
            printer = QPrinter(brother)
            self._apply_settings(printer, brother)
            return printer
        log.warning("Etiqueta: no se encontró la Brother QL-810W instalada.")
        printer = QPrinter(QPrinter.HighResolution)
        self._apply_settings(printer)
        return printer


def _qimage_to_pil(image: QImage):
    from PIL import Image

    gray = image.convertToFormat(QImage.Format_Grayscale8)
    width, height = gray.width(), gray.height()
    ptr = gray.constBits()
    ptr.setsize(gray.sizeInBytes())
    return Image.frombuffer("L", (width, height), ptr, "raw", "L", 0, 1)


class BrotherQLNetworkLabelPrinter(LabelPrinter):
    """Imprime por red con ``brother_ql`` (sin el controlador de Windows)."""

    MODEL_QL810W = "QL_810W"
    LABEL_29x90 = "29x90"

    def __init__(self, host: str, port: int = 9100, parent: QWidget | None = None):
        self._host = host
        self._port = port
        self._parent = parent

    def print_labels(self, images: Sequence[QImage], copies: int) -> bool:
        try:
            from brother_ql.backends.helpers import send
            from brother_ql.conversion import convert
        except ImportError as exc:  # pragma: no cover - depende del entorno
            log.error("Falta 'brother_ql'. Instale con: pip install brother-ql pillow", exc_info=exc)
            return False

        instructions = []
        for _ in range(max(copies, 1)):
            for image in images:
                pil = _qimage_to_pil(_rotate_for_ql(image))
                instructions.extend(
                    convert(pil, self.MODEL_QL810W, self.LABEL_29x90, cut=True)
                )
        send(instructions, "network", host=self._host, port=self._port)
        log.info("Etiquetas enviadas por red a %s:%s (QL-810W).", self._host, self._port)
        return True


def make_label_printer(settings: Settings, parent: QWidget | None = None) -> LabelPrinter:
    spec = LabelSpec(width_mm=settings.label_width_mm, height_mm=settings.label_height_mm, dpi=settings.label_dpi)
    kind = (settings.label_printer_kind or "windows").lower()
    if kind == "brother_ql" and settings.brother_printer_ip:
        return BrotherQLNetworkLabelPrinter(host=settings.brother_printer_ip, port=9100, parent=parent)
    if kind == "null":
        return NullLabelPrinter()
    return WindowsDriverLabelPrinter(spec, parent=parent)


class LabelPrintingService:
    """Facade: recibe producto y cantidad y decide etiqueta + transporte."""

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        self._settings = settings
        self._spec = LabelSpec(
            width_mm=settings.label_width_mm, height_mm=settings.label_height_mm, dpi=settings.label_dpi
        )
        self._printer = make_label_printer(settings, parent)

    def preview_image(self, product: ProductDTO) -> QImage:
        return render_barcode_label(
            code=product.code,
            name=product.name,
            price=product.unit_price,
            spec=self._spec,
        )

    def print_product(self, product: ProductDTO, copies: int) -> bool:
        image = self.preview_image(product)
        return self._printer.print_labels([image], copies)

    def print_price_only(self, code: str, name: str, price: Money, copies: int) -> bool:
        image = render_barcode_label(code=code, name=name, price=price, spec=self._spec)
        return self._printer.print_labels([image], copies)

    def print_batch(self, labels: Sequence[tuple[str, str, Money, int]]) -> int:
        """Imprime un lote de etiquetas (código, nombre, precio, copias) en un solo envío.

        Devuelve la cantidad de etiquetas renderizadas (>= 1 cuando hay datos).
        """
        images: list[QImage] = []
        for code, name, price, copies in labels or []:
            copies = max(1, int(copies or 1))
            image = render_barcode_label(code=code, name=name, price=price, spec=self._spec)
            images.extend([image] * copies)
        if not images:
            return 0
        try:
            self._printer.print_labels(images, 1)
        except Exception:  # noqa: BLE001 - no romper el flujo si el transporte falla
            log.exception("Etiquetas en lote: falló el envío a la impresora.")
        return len(images)