"""Punto de entrada para empaquetado con PyInstaller (``KioscoPOS.exe``)."""

from __future__ import annotations

import sys

from app.main import entrypoint

if __name__ == "__main__":
    sys.exit(entrypoint())