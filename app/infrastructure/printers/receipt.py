"""Renderizado de recibos (HTML compacto, 80 mm). La impresión la resuelve Qt."""

from __future__ import annotations

import html
import textwrap
from datetime import datetime

from app.application.read_models import CorteDTO, PurchaseLineDTO, SaleDTO
from app.domain.value_objects import Money
from app.infrastructure.labels.barcode import encode_barcode
from app.settings import StoreInfo


def render_legend_html(store: StoreInfo) -> str:
    """Línea de política de devolución que se imprime en cada ticket de venta."""
    legend = (store.sale_legend or "").strip()
    if not legend:
        return ""
    return f"<div style='text-align:center;white-space:pre-wrap'>{html.escape(legend)}</div>"


def render_receipt_html(sale: SaleDTO, store: StoreInfo, *, title: str = "RECIBO") -> str:
    lines: list[str] = []
    lines.append(
        f"""<div style="font-family:'Courier New',monospace;font-size:11px;color:#111;line-height:1.35;width:100%">"""
    )
    lines.append(f"<div style='text-align:center;font-size:14px;font-weight:bold'>{html.escape(store.name)}</div>")
    if store.address:
        lines.append(f"<div style='text-align:center'>{html.escape(store.address)}</div>")
    if store.phone:
        lines.append(f"<div style='text-align:center'>Tel: {html.escape(store.phone)}</div>")
    lines.append(_render_identity_line(store))
    lines.append("<div style='text-align:center'>- - - - - - - - - - - - - - - -</div>")
    lines.append(f"<div style='text-align:center;font-weight:bold;margin-top:2px'>{title}</div>")
    lines.append(
        f"<div style='text-align:center'>N. {html.escape(sale.receipt_number)}</div>"
    )
    lines.append(f"<div style='text-align:center'>{sale.created_at.strftime('%d/%m/%Y %H:%M')}</div>")
    lines.append(render_separator())

    for item in sale.items:
        qty = f"{item.quantity:>3}u"
        unit = item.unit_price.format()
        sub = item.subtotal.format()
        name = html.escape(item.product_name)
        # Marca de precio alterado inline por el cajero.
        name += "<span style='opacity:.7'> *</span>" if item.price_overridden else ""
        lines.append(f"<div>{name}</div>")
        lines.append(
            f"<div>&nbsp;&nbsp;{qty}&nbsp;x&nbsp;{html.escape(unit)}"
            f"<span style='float:right'>{html.escape(sub)}</span></div>"
        )
    if any(item.price_overridden for item in sale.items):
        lines.append("<div style='opacity:.7'>* precio ajustado en la venta</div>")
    lines.append(render_separator())
    if sale.discount > Money.zero():
        lines.append(
            f"<div><b>SUBTOTAL: <span style='float:right'>{html.escape(sale.subtotal.format())}</span></b></div>"
        )
        lines.append(
            f"<div><b>DESCUENTO: <span style='float:right'>-{html.escape(sale.discount.format())}</span></b></div>"
        )
    lines.append(f"<div><b>TOTAL: <span style='float:right'>{html.escape(sale.total.format())}</span></b></div>")
    for payment in sale.payments:
        lines.append(f"<div style='text-align:right'>{html.escape(payment.method.value)}: {html.escape(payment.amount.format())}</div>")
    if sale.tendered is not None:
        lines.append(f"<div style='text-align:right'>Efectivo: {html.escape(sale.tendered.format())}</div>")
        lines.append(f"<div style='text-align:right'>Vuelto: {html.escape(sale.change_amount.format())}</div>")
    try:
        lines.append(render_barcode_html(sale.receipt_number))
    except Exception:
        pass
    if sale.status == "ANULADA":
        lines.append("<div style='text-align:center;color:#b91c1c;font-weight:bold'>ANULADA</div>")
        if sale.void_reason:
            lines.append(f"<div style='text-align:center'>{html.escape(sale.void_reason)}</div>")
    lines.append(render_separator())
    lines.append(render_legend_html(store))
    if (store.sale_legend or "").strip():
        lines.append(render_separator())
    lines.append(f"<div style='text-align:center'>{html.escape(store.footer)}</div>")
    lines.append("</div>")
    return "".join(lines)


def render_separator() -> str:
    return "<div>- - - - - - - - - - - - - - - -</div>"


def _render_identity_line(store: StoreInfo) -> str:
    """Línea con la caja/sucursal donde se aplicó la venta (si están configuradas)."""
    parts = []
    if store.terminal_label:
        parts.append(f"Caja: {store.terminal_label}")
    if store.branch_label:
        parts.append(f"Sucursal: {store.branch_label}")
    if not parts:
        return ""
    return f"<div style='text-align:center'>{html.escape(' · '.join(parts))}</div>"


def render_purchase_list_html(
    items: list[PurchaseLineDTO],
    store: StoreInfo,
    *,
    title: str = "LISTA DE COMPRA",
    date: datetime | None = None,
) -> str:
    """Ticket de la lista de compra (pedido) armada manualmente para un proveedor."""
    issue_date = date or datetime.now()
    lines: list[str] = []
    lines.append(
        f"""<div style="font-family:'Courier New',monospace;font-size:11px;color:#111;line-height:1.35;width:100%">"""
    )
    lines.append(f"<div style='text-align:center;font-size:14px;font-weight:bold'>{html.escape(store.name)}</div>")
    if store.address:
        lines.append(f"<div style='text-align:center'>{html.escape(store.address)}</div>")
    if store.phone:
        lines.append(f"<div style='text-align:center'>Tel: {html.escape(store.phone)}</div>")
    lines.append(render_separator())
    lines.append(f"<div style='text-align:center;font-weight:bold'>{html.escape(title)}</div>")
    lines.append(f"<div style='text-align:center'>{issue_date.strftime('%d/%m/%Y %H:%M')}</div>")
    lines.append(render_separator())

    for item in items:
        quantity = f"{item.quantity:>3}u"
        unit = item.unit_price.format()
        subtotal = item.subtotal.format()
        description = html.escape(item.description)
        provider = html.escape(item.provider_name)
        lines.append(f"<div>{description}</div>")
        lines.append(
            f"<div>&nbsp;&nbsp;{quantity}&nbsp;x&nbsp;{html.escape(unit)}"
            f"<span style='float:right'>{html.escape(subtotal)}</span></div>"
        )
        lines.append(f"<div style='text-align:right;font-size:10px'>{provider} · {html.escape(item.code)}</div>")

    lines.append(render_separator())
    total = sum((item.subtotal for item in items), Money.zero())
    lines.append(f"<div><b>TOTAL: <span style='float:right'>{html.escape(total.format())}</span></b></div>")
    lines.append("<div style='text-align:center'>(copia del pedido, sin imprimir inventario)</div>")
    lines.append(render_separator())
    lines.append(f"<div style='text-align:center'>{html.escape(store.footer)}</div>")
    lines.append("</div>")
    return "".join(lines)


def render_barcode_html(code: str, *, module_px: int = 2, height_px: int = 36, with_code: bool = True) -> str:
    """Código de barras escaneable en HTML puro (spans con fondo negro).

    Fuente: secuencia de módulos (0/1) de EAN-13/CODE128-B. Qt imprime los
    fondos de los spans, de modo que el código sale legible en el ticker.
    """
    modules = encode_barcode(code)
    pieces: list[str] = []
    current = modules[0]
    count = 1

    def push(bit: str, n: int) -> None:
        color = "#111111" if bit == "1" else "#ffffff"
        pieces.append(
            f"<span style='background-color:{color};display:inline-block;"
            f"height:{height_px}px;width:{n * module_px}px'></span>"
        )

    for bit in modules[1:]:
        if bit == current:
            count += 1
            continue
        push(current, count)
        current = bit
        count = 1
    push(current, count)

    out = f"<div style='text-align:center;white-space:nowrap'>{''.join(pieces)}</div>"
    if with_code:
        out += f"<div style='text-align:center;font-size:9px'>{html.escape(code)}</div>"
    return out


def render_sale_plain_text(sale: SaleDTO, store: StoreInfo) -> str:
    """Variante en texto plano (útil para testear o consola)."""
    width = 32
    out: list[str] = []
    out.append(store.name.center(width))
    if store.address:
        out.append(store.address.center(width))
    out.append("-" * width)
    out.append(f"N. {sale.receipt_number}  {sale.created_at.strftime('%d/%m/%Y %H:%M')}")
    out.append("-" * width)
    for item in sale.items:
        suffix = " *" if item.price_overridden else ""
        out.append(item.product_name[: width - 1 - len(suffix)] + suffix)
        out.append(
            f"  {item.quantity:>3}u x {item.unit_price.format():<12} {item.subtotal.format()}".ljust(width)
        )
    if any(item.price_overridden for item in sale.items):
        out.append("* precio ajustado en la venta")
    out.append("-" * width)
    if sale.discount > Money.zero():
        out.append(f"{'SUBTOTAL:':<16}{sale.subtotal.format().rjust(width - 16)}")
        out.append(f"{'DESCUENTO:':<16}{('- ' + sale.discount.format()).rjust(width - 16)}")
    out.append(f"{'TOTAL:':<16}{sale.total.format().rjust(width - 16)}")
    for payment in sale.payments:
        out.append(f"{payment.method.value:.<14}{payment.amount.format().rjust(width - 14)}")
    if sale.tendered is not None:
        out.append(f"{'Efectivo:':<14}{sale.tendered.format().rjust(width - 14)}")
        out.append(f"{'Vuelto:':<14}{sale.change_amount.format().rjust(width - 14)}")
    out.append("-" * width)
    legend = (store.sale_legend or "").strip()
    if legend:
        for chunk in textwrap.wrap(legend, width=width) or [""]:
            out.append(chunk.center(width))
    out.append(store.footer.center(width))
    return "\n".join(out)


def render_corte_html(corte: CorteDTO, sales: list[SaleDTO], store: StoreInfo) -> str:
    """Ticket de cierre de caja: lista los tickets del día con su monto.

    Mantiene el mismo formato HTML por bloques ``<div>`` que los recibos para que
    ``build_esc_pos_from_html`` pueda convertirlo a ESC/POS.
    """
    lines: list[str] = []
    lines.append(
        f"""<div style="font-family:'Courier New',monospace;font-size:11px;color:#111;line-height:1.35;width:100%">"""
    )
    lines.append(f"<div style='text-align:center;font-size:14px;font-weight:bold'>{html.escape(store.name)}</div>")
    if store.address:
        lines.append(f"<div style='text-align:center'>{html.escape(store.address)}</div>")
    if store.phone:
        lines.append(f"<div style='text-align:center'>Tel: {html.escape(store.phone)}</div>")
    lines.append(_render_identity_line(store))
    lines.append(render_separator())
    lines.append("<div style='text-align:center;font-weight:bold'>CIERRE DE CAJA</div>")

    day = corte.day
    lines.append(f"<div style='text-align:center'>Jornada #{day.id}</div>")
    lines.append(f"<div style='text-align:center'>{day.opened_at.strftime('%d/%m/%Y %H:%M')}</div>")
    lines.append(render_separator())
    lines.append("<div style='text-align:center;font-weight:bold'>TICKETS DEL DÍA</div>")

    for sale in sales:
        lines.append(
            f"<div>{html.escape(sale.receipt_number)} "
            f"<span style='float:right'>{html.escape(sale.total.format())}</span></div>"
        )
        lines.append(
            f"<div style='text-align:right;font-size:10px'>{sale.created_at.strftime('%d/%m %H:%M')} "
            f"· {html.escape(sale.methods_label)}</div>"
        )
    lines.append(render_separator())
    lines.append(
        f"<div><b>VENTAS ({corte.sales_count}): "
        f"<span style='float:right'>{html.escape(corte.sales_total.format())}</span></b></div>"
    )
    for method, amount in corte.by_method.items():
        lines.append(
            f"<div>{html.escape(method.title())}: "
            f"<span style='float:right'>{html.escape(amount.format())}</span></div>"
        )
    if corte.cash_in_total > Money.zero():
        lines.append(
            f"<div>Ingresos de caja: <span style='float:right'>{html.escape(corte.cash_in_total.format())}</span></div>"
        )
    if corte.cash_out_total > Money.zero():
        lines.append(
            f"<div>Egresos de caja: <span style='float:right'>{html.escape(corte.cash_out_total.format())}</span></div>"
        )
    if corte.refunds_total > Money.zero():
        lines.append(
            f"<div>Devoluciones: <span style='float:right'>{html.escape(corte.refunds_total.format())}</span></div>"
        )
    if day.opening_cash > Money.zero():
        lines.append(
            f"<div>Fondo inicial: <span style='float:right'>{html.escape(day.opening_cash.format())}</span></div>"
        )
    lines.append(
        f"<div><b>EFECTIVO ESPERADO: "
        f"<span style='float:right'>{html.escape(corte.expected_cash.format())}</span></b></div>"
    )
    if day.closing_cash is not None:
        lines.append(
            f"<div>Efectivo contado: <span style='float:right'>{html.escape(day.closing_cash.format())}</span></div>"
        )
    if day.difference is not None:
        kind = day.difference_kind or "EXACTO"
        lines.append(
            f"<div>Diferencia ({html.escape(kind)}): "
            f"<span style='float:right'>{html.escape(day.difference.format())}</span></div>"
        )
    lines.append(render_separator())
    lines.append(f"<div style='text-align:center'>{html.escape(store.footer)}</div>")
    lines.append("</div>")
    return "".join(lines)