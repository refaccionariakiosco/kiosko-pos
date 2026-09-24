"""Tests de impresión de tickets/cajón y diferenciación por terminal en reportes."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session", autouse=True)
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def ui_services():
    from app.bootstrap import build_services
    from app.settings import Settings

    return build_services(settings=Settings(database_path=":memory:"))


@pytest.fixture
def store_settings():
    from app.settings import Settings

    return Settings(database_path=":memory:")


# --------------------------------------------------------------------------- #
# Impresora de tickets configurada
# --------------------------------------------------------------------------- #


def test_ticket_printer_configurada_se_usa_en_lugar_de_la_heuristica(monkeypatch):
    import app.infrastructure.printers.ticket_esc_pos as ticket_mod

    installed = ["Generic / Text Only", "HP LaserJet", "ZKteco ticket"]
    monkeypatch.setattr(ticket_mod, "list_printers", lambda: installed)

    ticket_mod.set_configured_printer("HP LaserJet")
    assert ticket_mod.find_ticket_printer() == "HP LaserJet"

    ticket_mod.set_configured_printer("Generic / Text Only")
    assert ticket_mod.find_ticket_printer() == "Generic / Text Only"

    # Sin configuración explícita cae en la heurística por nombre.
    ticket_mod.set_configured_printer("")
    assert ticket_mod.find_ticket_printer() == "ZKteco ticket"


def test_kick_cash_drawer_envia_pulso_a_la_impresora_configurada(monkeypatch):
    import app.infrastructure.printers.ticket_esc_pos as ticket_mod

    sent: list[tuple[str, bytes]] = []
    monkeypatch.setattr(ticket_mod, "list_printers", lambda: ["Generic / Text Only", "ZKteco ticket"])
    monkeypatch.setattr(ticket_mod, "raw_send", lambda name, data: sent.append((name, data)) or True)

    ticket_mod.set_configured_printer("Generic / Text Only")
    assert ticket_mod.kick_cash_drawer() is True
    assert sent and sent[0][0] == "Generic / Text Only"
    assert sent[0][1] == ticket_mod.INIT + ticket_mod.DRAWER_KICK
    ticket_mod.set_configured_printer("")


def test_print_receipt_ticket_usa_printer_name_si_se_pasa(monkeypatch):
    import app.infrastructure.printers.ticket_esc_pos as ticket_mod

    sent: list[str] = []
    monkeypatch.setattr(ticket_mod, "list_printers", lambda: ["Generic / Text Only", "ZKteco ticket"])
    monkeypatch.setattr(ticket_mod, "raw_send", lambda name, data: sent.append(name) or True)

    ticket_mod.set_configured_printer("")
    html = "<div style='text-align:center'>N. R-1-000001</div>"
    assert ticket_mod.print_receipt_ticket(html, printer_name="Generic / Text Only") is True
    assert sent == ["Generic / Text Only"]


def test_sin_impresora_termica_no_rompe(monkeypatch):
    import app.infrastructure.printers.ticket_esc_pos as ticket_mod

    monkeypatch.setattr(ticket_mod, "list_printers", lambda: ["Solo oficina"])
    ticket_mod.set_configured_printer("")
    assert ticket_mod.find_ticket_printer() is None
    assert ticket_mod.kick_cash_drawer() is False
    assert ticket_mod.print_receipt_ticket("<div>x</div>") is False


# --------------------------------------------------------------------------- #
# Identidad sucursal/terminal en Settings y tickets
# --------------------------------------------------------------------------- #


def test_build_services_puebla_identidad_en_store():
    from app.bootstrap import build_services
    from app.settings import Settings

    services = build_services(settings=Settings(database_path=":memory:"))
    store = services.settings.store
    # terminal_num tiene semilla "1" aunque no se configure topología.
    assert store.terminal_label == "1"


def test_build_services_persiste_impresora_de_tickets_desde_sys_config(tmp_path):
    from pathlib import Path

    from sqlalchemy import text

    from app.bootstrap import build_services
    from app.infrastructure.printers.ticket_esc_pos import configured_printer
    from app.settings import Settings

    db = str(tmp_path / "kiosco.db")
    settings = Settings(database_path=db)
    services = build_services(settings=settings)
    with services.session_factory() as session:
        session.execute(
            text("INSERT OR REPLACE INTO sys_config (key, value, updated_at) VALUES (:k, :v, datetime('now'))"),
            {"k": "ticket_printer", "v": "ZKteco ticket"},
        )
        session.commit()

    # Al reconstruir (mismo archivo) el ajuste se recarga y se propaga a la térmica.
    services2 = build_services(settings=settings)
    assert services2.settings.ticket_printer == "ZKteco ticket"
    assert settings.ticket_printer == "ZKteco ticket"
    assert configured_printer() == "ZKteco ticket"


def test_recibo_lleva_identidad_de_caja_y_sucursal():
    from datetime import datetime
    from decimal import Decimal

    from app.application.read_models import SaleDTO
    from app.infrastructure.printers.receipt import render_receipt_html
    from app.settings import StoreInfo

    store = StoreInfo(name="Tienda", footer="ok", branch_label="SUC-1", terminal_label="2")
    sale = SaleDTO(id=1, receipt_number="R-2-000001", created_at=datetime.now(), status="COMPLETADA")
    html = render_receipt_html(sale, store)
    assert "Caja: 2" in html
    assert "Sucursal: SUC-1" in html


# --------------------------------------------------------------------------- #
# Colores por terminal
# --------------------------------------------------------------------------- #


def test_terminal_key_desde_folio():
    from app.interface.terminal_colors import terminal_key

    assert terminal_key("R-1-000123") == "1"
    assert terminal_key("R-7-000004") == "7"
    assert terminal_key("R-000123") == "0"
    assert terminal_key("") == "0"


def test_terminal_color_estable_y_distinto_por_caja():
    from app.interface.terminal_colors import terminal_color, terminal_row_color

    color1 = terminal_color("1")
    color2 = terminal_color("2")
    assert color1.name() != color2.name()
    assert terminal_color("1").name() == color1.name()
    row = terminal_row_color("R-3-000001")
    assert row.alpha() < 255  # fondo suave


# --------------------------------------------------------------------------- #
# Reportes: sumatoria y datos de exportación
# --------------------------------------------------------------------------- #


def test_reports_muestra_fila_de_totales_y_guarda_datos_de_exportacion(
    qapp, ui_services, store_settings
):
    from datetime import datetime, time
    from decimal import Decimal

    from app.application.commands import (
        CompleteSaleCommand,
        CreateCategoryCommand,
        CreateProductCommand,
        SaleItemRequest,
        SalePaymentRequest,
    )
    from app.interface.reports_dialog import ReportsDialog

    c = ui_services.commands
    cat = c.execute(CreateCategoryCommand(name="BEBIDAS"))
    p = c.execute(
        CreateProductCommand(code="7501055302082", name="Agua 500ml", unit_price="20", stock=10, category_id=cat.id)
    )
    c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=p.code, quantity=2),),
            payments=(SalePaymentRequest(method="EFECTIVO", amount="40"),),
        )
    )
    c.execute(
        CompleteSaleCommand(
            items=(SaleItemRequest(code=p.code, quantity=1),),
            payments=(SalePaymentRequest(method="TARJETA", amount="20"),),
        )
    )

    dialog = ReportsDialog(ui_services.queries, parent=None)
    dialog._generate()

    # La última fila debe ser la de totales.
    last_row = dialog.table.rowCount() - 1
    assert last_row >= 0
    totales = dialog.table.item(last_row, 0)
    assert totales is not None
    assert "TOTALES" in totales.text()
    assert totales.font().bold()

    # La sumatoria debe corresponder a las ventas completadas.
    assert len(dialog._export_rows) >= 1
    assert dialog._export_totals[0] == "TOTALES"
    total_cell = next(c for c in dialog._export_totals if str(c).startswith("$"))
    assert total_cell == "$ 60,00"

    # Columna de caja presente en el detalle de ventas.
    assert "Caja" in dialog._export_headers

    # Exportación a Excel genera un archivo.
    from pathlib import Path

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = str(Path(tmp) / "reporte.xlsx")
        dialog._write_excel(target)
        assert Path(target).exists()

    # El HTML de exportación incluye la sumatoria.
    html = dialog._export_html()
    assert "TOTALES" in html


def test_reports_excel_y_pdf_exportables_sin_errores(qapp, ui_services, monkeypatch, tmp_path):
    from app.interface.reports_dialog import ReportsDialog

    dialog = ReportsDialog(ui_services.queries, parent=None)
    dialog._generate()

    from pathlib import Path

    target = str(tmp_path / "out.xlsx")
    dialog._write_excel(target)
    assert Path(target).exists()

    html = dialog._export_html()
    assert "<table" in html
    assert "</table>" in html
