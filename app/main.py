"""Punto de entrada de la aplicación de escritorio (Kiosco POS)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.application.queries import GetOpenCashDayQuery
from app.bootstrap import build_services
from app.interface.dialogs import OpenCashDayDialog
from app.interface.main_window import MainWindow
from app.interface.theme import apply_theme
from app.infrastructure.printing.label_printer import LabelPrintingService
from app.infrastructure.topology import Topology
from app.seed import seed_demo_data
from app.settings import Settings

log = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    settings.ensure_layout()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.FileHandler(settings.logs_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def build_settings(args: argparse.Namespace) -> Settings:
    kwargs = {
        "log_level": args.log_level,
        "seed_demo_data": args.demo,
        "label_printer_kind": args.printer,
    }
    if args.db:
        kwargs["database_path"] = Path(args.db)
    if args.brother_ip:
        kwargs["brother_printer_ip"] = args.brother_ip
    return Settings(**kwargs)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kiosco-pos", description="Punto de venta Kiosco POS")
    parser.add_argument("--db", help="Ruta del archivo SQLite (por defecto data/kiosco.db)")
    parser.add_argument("--demo", action="store_true", help="Cargar datos de demostración si la base está vacía")
    parser.add_argument("--log-level", default="INFO", help="Nivel de log: DEBUG, INFO, WARNING, ERROR")
    parser.add_argument(
        "--printer",
        default="windows",
        choices=["windows", "brother_ql", "null"],
        help="Transporte para imprimir etiquetas (windows = controlador QPrinter)",
    )
    parser.add_argument("--brother-ip", default="", help="IP de la Brother QL-810W (modo brother_ql)")
    parser.add_argument(
        "--id-sucursal", default="", help="ID de sucursal (topología offline-first). Se persiste en sys_config."
    )
    parser.add_argument(
        "--id-terminal", default="", help="ID de terminal de caja. Se persiste en sys_config."
    )
    parser.add_argument(
        "--terminal-num", default="", help="Número de caja (prefijo del recibo, p. ej. 1 o 2)."
    )
    parser.add_argument("--supabase-url", default="", help="URL del proyecto Supabase para sincronización.")
    parser.add_argument("--supabase-anon-key", default="", help="Anon key (publicable) del proyecto Supabase.")
    return parser


def build_topology(args: argparse.Namespace) -> Topology | None:
    """Devuelve la Topology sólo si el usuario configura algo de identidad."""
    values = {
        "id_sucursal": args.id_sucursal,
        "id_terminal": args.id_terminal,
        "terminal_num": args.terminal_num,
        "supabase_url": args.supabase_url,
        "supabase_anon_key": args.supabase_anon_key,
    }
    if not any(values.values()):
        return None
    return Topology(**values)


def prompt_open_cash_day(services) -> None:
    """Si no hay jornada abierta, ofrece iniciarla pidiendo el efectivo inicial."""
    try:
        open_day = services.queries.ask(GetOpenCashDayQuery())
    except Exception:  # noqa: BLE001 - la ventana no debe caerse por esto
        log.exception("No se pudo consultar la jornada de caja.")
        return
    if open_day is not None:
        return
    from PySide6.QtWidgets import QMessageBox

    question = QMessageBox.question(
        None,
        "Jornada de caja",
        "No hay una jornada de caja abierta.\n¿Desea iniciar la caja ahora?",
    )
    if question != QMessageBox.Yes:
        log.info("No se inició jornada de caja; podrá hacerlo desde la página Caja.")
        return
    dialog = OpenCashDayDialog(services.commands, opened_by=services.settings.login_username)
    dialog.exec()


def entrypoint(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    settings = build_settings(args)
    configure_logging(settings)

    services = build_services(settings=settings, seeds=args.demo, topology=build_topology(args))
    log.info("Base de datos lista en %s", settings.database_path)
    if services.topology is not None:
        topo = services.topology
        log.info(
            "Topología: sucursal=%s terminal=%s nube=%s",
            topo.id_sucursal or "(local)",
            topo.id_terminal,
            "configurada" if topo.is_cloud_configured else "offline",
        )

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    apply_theme(app)

    from app.interface.login_view import LoginDialog

    login = LoginDialog(settings)
    login.showFullScreen()
    if login.exec() != LoginDialog.Accepted:
        log.info("Acceso cancelado por el usuario.")
        return 0

    labels = LabelPrintingService(settings)
    prompt_open_cash_day(services)
    window = MainWindow(services, settings, labels)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(entrypoint())