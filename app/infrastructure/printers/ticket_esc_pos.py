"""Impresión de tickets en la impresora térmica (p. ej. 'ZKteco ticket').

La ZKTeco suele estar instalada como driver 'Generic / Text Only', que no
acepta gráficos (GDI) enviados por QTextDocument/QPrinter. Por eso el ticket
se manda como ESC/POS crudo (RAW) directo al spooler de Windows, sin pasar por
el driver. Solo depende de la API winspool (ctypes, sin dependencias extra).

El código de barras se emite con el comando ESC/POS estándar GS k (CODE128),
ampliamente soportado por las térmicas ZKTeco.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import html as html_mod
import logging
import re

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Comandos ESC/POS
# --------------------------------------------------------------------------- #

ESC = b"\x1b"
GS = b"\x1d"
INIT = ESC + b"@"
ALIGN_LEFT = ESC + b"a" + bytes([0])
ALIGN_CENTER = ESC + b"a" + bytes([1])
ALIGN_RIGHT = ESC + b"a" + bytes([2])
BOLD_ON = ESC + b"E" + bytes([1])
BOLD_OFF = ESC + b"E" + bytes([0])
BARCODE_HEIGHT = GS + b"h" + bytes([64])   # ~8 mm
BARCODE_WIDTH = GS + b"w" + bytes([2])
CODE128_TERMINATED = GS + b"k" + bytes([73])
FEED_LINES = ESC + b"d"
CUT = GS + b"V" + bytes([0])
DRAWER_KICK = ESC + b"p" + bytes([0, 25, 25])  # abrir cajón de dinero por el puerto RJ11 de la térmica

_CHARSET = "cp437"

_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_ALIGN_RE = re.compile(r"text-align\s*:\s*(center|right|left)")
_BARCODE_BLOCK_RE = re.compile(r"background-color:#111111")
_RECEIPT_NUMBER_RE = re.compile(r"N\.\s*([A-Za-z0-9\-]+)")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _encode(text: str) -> bytes:
    return text.encode(_CHARSET, errors="replace")


def build_esc_pos_from_html(html: str) -> bytes:
    """Convierte el HTML propio de `render_receipt_html` a bytes ESC/POS."""
    out = bytearray(INIT)
    out += ALIGN_LEFT + BOLD_OFF

    barcode_data = None
    match = _RECEIPT_NUMBER_RE.search(html)
    if match:
        barcode_data = match.group(1)

    parts = re.split(r"</div>", html)
    for part in parts:
        part_html = _BR_RE.sub("\n", part)
        if _BARCODE_BLOCK_RE.search(part_html):
            continue  # el bloque de spans del código se imprime con GS k
        visible = _TAG_RE.sub("", part_html)
        visible = html_mod.unescape(visible)
        visible = re.sub(r"[ \t]+", " ", visible).strip()
        if not visible:
            out += b"\n"
            continue

        align = _STYLE_ALIGN_RE.search(part_html)
        if align and align.group(1) == "center":
            out += ALIGN_CENTER
        elif align and align.group(1) == "right":
            out += ALIGN_RIGHT

        bold = bool(re.search(r"<b>|<strong>", part_html, re.IGNORECASE))
        out += BOLD_ON if bold else BOLD_OFF
        for line in visible.splitlines():
            out += _encode(line) + b"\n"
        if align:
            out += ALIGN_LEFT

    if barcode_data:
        out += ALIGN_CENTER + BARCODE_HEIGHT + BARCODE_WIDTH
        out += CODE128_TERMINATED + _encode(barcode_data) + b"\x00"
        out += ALIGN_LEFT

    out += FEED_LINES + bytes([4])
    out += CUT
    return bytes(out)


# --------------------------------------------------------------------------- #
# winspool (RAW) vía ctypes
# --------------------------------------------------------------------------- #

class PRINTER_INFO_1(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD),
        ("pDescription", ctypes.c_wchar_p),
        ("pName", ctypes.c_wchar_p),
        ("pComment", ctypes.c_wchar_p),
    ]


class DOC_INFO_1(ctypes.Structure):
    _fields_ = [
        ("pDocName", ctypes.c_wchar_p),
        ("pOutputFile", ctypes.c_wchar_p),
        ("pDatatype", ctypes.c_wchar_p),
    ]


_winspool = ctypes.WinDLL("winspool.drv")


def list_printers() -> list[str]:
    """Lista los nombres de las impresoras instaladas (PRINTER_INFO_1)."""
    _winspool.EnumPrintersW.argtypes = [wt.DWORD, ctypes.c_wchar_p, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD)]
    flags = 2  # PRINTER_ENUM_LOCAL
    needed = wt.DWORD(0)
    returned = wt.DWORD(0)
    _winspool.EnumPrintersW(flags, None, 1, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    if needed.value == 0:
        return []
    buf = ctypes.create_string_buffer(needed.value)
    if not _winspool.EnumPrintersW(flags, None, 1, buf, needed.value, ctypes.byref(needed), ctypes.byref(returned)):
        return []
    infos = (PRINTER_INFO_1 * returned.value).from_buffer(buf)
    return [info.pName for info in infos if info.pName]


def find_ticket_printer() -> str | None:
    """Busca una impresora que parezca térmica de tickets."""
    for name in list_printers():
        lowered = name.lower()
        if any(needle in lowered for needle in ("zkt", "tick", "thermal", "escpos", "esc/pos", "termica", "térmica")):
            return name
    return None


def raw_send(printer_name: str, data: bytes) -> bool:
    """Envía bytes RAW a la impresora por el spooler de Windows."""
    _winspool.OpenPrinterW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(wt.HANDLE), ctypes.c_void_p]
    _winspool.OpenPrinterW.restype = wt.BOOL
    _winspool.StartDocPrinterW.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p]
    _winspool.StartDocPrinterW.restype = wt.BOOL
    _winspool.StartPagePrinter.argtypes = [wt.HANDLE]
    _winspool.StartPagePrinter.restype = wt.BOOL
    _winspool.WritePrinter.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    _winspool.WritePrinter.restype = wt.BOOL
    _winspool.EndPagePrinter.argtypes = [wt.HANDLE]
    _winspool.EndPagePrinter.restype = wt.BOOL
    _winspool.EndDocPrinter.argtypes = [wt.HANDLE]
    _winspool.EndDocPrinter.restype = wt.BOOL
    _winspool.ClosePrinter.argtypes = [wt.HANDLE]
    _winspool.ClosePrinter.restype = wt.BOOL

    handle = wt.HANDLE()
    if not _winspool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        log.error("Ticket: no se pudo abrir la impresora %r", printer_name)
        return False
    try:
        doc = DOC_INFO_1("Kiosco POS - Recibo", None, "RAW")
        if not _winspool.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            log.error("Ticket: StartDocPrinter falló en %r", printer_name)
            return False
        try:
            if not _winspool.StartPagePrinter(handle):
                log.error("Ticket: StartPagePrinter falló en %r", printer_name)
                return False
            buffer = ctypes.create_string_buffer(data, len(data))
            written = wt.DWORD(0)
            ok = _winspool.WritePrinter(handle, buffer, len(data), ctypes.byref(written))
            if not ok or written.value != len(data):
                log.error("Ticket: WritePrinter incompleto (%d/%d) en %r", written.value, len(data), printer_name)
                return False
            _winspool.EndPagePrinter(handle)
        finally:
            _winspool.EndDocPrinter(handle)
        return True
    finally:
        _winspool.ClosePrinter(handle)


def print_receipt_ticket(html: str) -> bool:
    """Imprime un recibo (HTML del proyecto) en la térmica detectada.

    Devuelve False si no hay impresora de tickets o falló el envío.
    """
    printer_name = find_ticket_printer()
    if printer_name is None:
        log.warning("Ticket: no se encontró una impresora térmica instalada.")
        return False
    try:
        data = build_esc_pos_from_html(html)
    except Exception:  # noqa: BLE001 - el parseo nunca debe romper la venta
        log.exception("Ticket: no se pudo convertir el recibo a ESC/POS.")
        return False
    ok = raw_send(printer_name, data)
    if ok:
        log.info("Ticket: enviado a %r (ESC/POS, %d bytes).", printer_name, len(data))
    return ok


def kick_cash_drawer() -> bool:
    """Abre el cajón de dinero conectado a la impresora térmica.

    Se envía ``ESC p 0 t1 t2`` por RAW; la mayoría de las térmicas (ZKteco
    incluida) dispara el pulso por el puerto RJ11 al recibirlo, haciendo saltar
    el cajón de efectivo. Nunca debe romper el flujo de la venta.
    """
    printer_name = find_ticket_printer()
    if printer_name is None:
        log.warning("Cajón: no se encontró una impresora térmica para el pulso.")
        return False
    try:
        ok = raw_send(printer_name, INIT + DRAWER_KICK)
    except Exception:  # noqa: BLE001 - el pulso no debe romper la venta
        log.exception("Cajón: no se pudo abrir el cajón de dinero.")
        return False
    if ok:
        log.info("Cajón: apertura enviada a %r.", printer_name)
    return ok