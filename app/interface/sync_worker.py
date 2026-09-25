"""Sincronización en segundo plano con QThread (la UI no se congela)."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from app.bootstrap import AppServices


class SyncWorker(QThread):
    """Corre :meth:`AppServices.run_sync` fuera del hilo de la interfaz.

    ``direction`` elige el sentido de la corrida: ``"both"`` (subir y bajar),
    ``"push"`` (solo subir el estado local) o ``"pull"`` (solo bajar del servidor).
    """

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, services: AppServices, direction: str = "both", parent=None):
        super().__init__(parent)
        self._services = services
        self._direction = direction

    def run(self) -> None:
        try:
            report = self._services.run_sync(self._direction)
        except Exception as exc:  # noqa: BLE001 - nunca dejar caer el hilo
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        if report.ok and not report.error:
            self.finished_ok.emit(report)
        else:
            self.failed.emit(report.error or report.skipped_reason)