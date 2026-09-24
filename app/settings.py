"""Configuración de la aplicación (fuente única de verdad para rutas y opciones)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    """Al estar empaquetado (PyInstaller) la base y los logs viven junto al .exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data"
    return Path(os.environ.get("KIOSCO_DATA_DIR", APP_DIR / "data"))


DEFAULT_DATA_DIR = _default_data_dir()
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "kiosco.db"


@dataclass(frozen=True, slots=True)
class StoreInfo:
    name: str = "Kiosco POS"
    address: str = ""
    phone: str = ""
    footer: str = "¡Gracias por su compra!"
    branch_label: str = ""
    terminal_label: str = ""


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Kiosco POS"
    database_path: Path | str = DEFAULT_DB_PATH
    log_level: str = "INFO"
    currency: str = "$"
    store: StoreInfo = field(default_factory=StoreInfo)
    seed_demo_data: bool = False
    label_width_mm: float = 90.0
    label_height_mm: float = 30.0
    label_dpi: int = 300
    label_printer_kind: str = "windows"  # "windows" | "brother_ql" | "null"
    brother_printer_ip: str = ""
    ticket_printer: str = ""  # nombre de la impresora térmica de tickets ("" = automática)
    sync_interval_seconds: int = 300  # sincronización automática (sin cambios desactiva: 0)
    login_username: str = "angel"
    login_password: str = "12345"

    def __post_init__(self) -> None:
        if isinstance(self.database_path, str) and self.database_path != ":memory:":
            object.__setattr__(self, "database_path", Path(self.database_path))

    @property
    def database_dir(self) -> Path:
        path = Path(self.database_path)
        return path.parent if path.suffix else path

    @property
    def logs_path(self) -> Path:
        path = Path(self.database_path)
        return path.parent / "app.log"

    def ensure_layout(self) -> None:
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)