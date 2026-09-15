"""Paquete de impresión de recibos."""

from app.infrastructure.printers.receipt import render_receipt_html, render_sale_plain_text

__all__ = ["render_receipt_html", "render_sale_plain_text"]