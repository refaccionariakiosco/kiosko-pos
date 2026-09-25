"""Orquestador de sincronización en caliente con debounce (realtime ↔ local).

Convierte eventos disparados (realtime remoto de PocketBase o mutación local)
en corridas de sync con espera de "quiet period": si hay un estallido de
cambios, se agrupa en un solo push/pull. Además limita la frecuencia para no
abrumar al servidor ni entrar en bucles push -> realtime -> pull.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer

from app.bootstrap import AppServices
from app.interface.sync_worker import SyncWorker

log = logging.getLogger(__name__)

DEBOUNCE_MS = 4000
MIN_INTERVAL_S = 10.0


class SyncCoordinator(QObject):
    """Enlaza señales de RealtimeWorker y mutaciones locales con SyncWorker."""

    def __init__(self, services: AppServices, parent=None):
        super().__init__(parent)
        self._services = services
        self._worker: SyncWorker | None = None
        self._last_run_ts: dict[str, float] = {}  # direction -> monotonic
        self._pull_timer = QTimer(self)
        self._pull_timer.setSingleShot(True)
        self._pull_timer.timeout.connect(lambda: self._fire("pull"))
        self._push_timer = QTimer(self)
        self._push_timer.setSingleShot(True)
        self._push_timer.timeout.connect(lambda: self._fire("push"))

    def notify_remote_change(self, table: str | None = None, record: dict | None = None) -> None:
        """Un cambio llegó desde el servidor (realtime): agendamos un pull."""
        self._arm(self._pull_timer)

    def notify_local_change(self) -> None:
        """Una mutación local acaba de ocurrir: agendamos un push."""
        self._arm(self._push_timer)

    def shutdown(self) -> None:
        """Espera a que termine la corrida en curso (usar en el cierre de app)."""
        self._pull_timer.stop()
        self._push_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(30000)

    def _arm(self, timer: QTimer) -> None:
        timer.stop()
        timer.start(DEBOUNCE_MS)

    def _fire(self, direction: str) -> None:
        last = self._last_run_ts.get(direction, 0.0)
        if self._monotonic() - last < MIN_INTERVAL_S:
            log.debug("SyncCoordinator: pulso de %s demasiado pronto, omitido.", direction)
            return
        if self._worker is not None and self._worker.isRunning():
            log.debug("SyncCoordinator: ya hay un sync en curso, %s espera.", direction)
            return
        self._last_run_ts[direction] = self._monotonic()
        worker = SyncWorker(self._services, direction=direction, parent=self)
        worker.finished_ok.connect(lambda _report: log.info("SyncCoordinator: %s ok.", direction))
        worker.failed.connect(lambda err: log.warning("SyncCoordinator: %s falló: %s", direction, err))
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_finished(self) -> None:
        self._worker = None

    @staticmethod
    def _monotonic() -> float:
        import time

        return time.monotonic()