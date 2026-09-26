"""Escucha de cambios realtime (PocketBase SSE) en segundo plano.

Usa la suscripción nativa de la SDK de PocketBase
(``pb.collection("productos").subscribe(callback)``), que mantiene una conexión
SSE en un hilo daemon. Cada cambio de las colecciones ``pos_*`` se emite como
señal Qt; el coordinador de sync decide qué hacer (pull con debounce).

El filtrado CDC se conserva idéntico (ver ``should_ignore_change``): un
terminal descarta los eventos que él mismo generó y los de otras sucursales
para no entrar en un bucle push -> realtime -> pull.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

#: Colecciones replicadas que interesan a un terminal.
REALTIME_TABLES = (
    "pos_categories",
    "pos_products",
    "pos_inventory",
    "pos_stock_movements",
    "pos_sales",
    "pos_sale_items",
    "pos_sale_payments",
)

#: Colecciones de sucursal (el resto son catálogo global).
BRANCH_TABLES = ("pos_inventory", "pos_sales", "pos_stock_movements", "pos_sale_items", "pos_sale_payments")

#: Colecciones que además permiten descartar por terminal de origen.
TERMINAL_TABLES = ("pos_sales", "pos_stock_movements")


def should_ignore_change(table: str, record: dict, own_branch: str, own_terminal: str) -> bool:
    """Decide si un evento realtime debe descartarse.

    - Un terminal ignora los cambios que produjo él mismo (``origin_terminal`` o
      ``terminal_id`` propio) para no entrar en un bucle push -> realtime -> pull.
    - El inventario, las ventas, sus renglones/pagos y los movimientos de otras
      sucursales nunca deben tirar el pull de este terminal.
    - Los catálogos (productos/categorías) son globales: se aceptan siempre.
    """
    if record is None:
        return True
    if table in BRANCH_TABLES and str(record.get("branch_id") or "") != str(own_branch or ""):
        return True
    if table in TERMINAL_TABLES:
        origin = record.get("origin_terminal") or record.get("terminal_id") or ""
        if own_terminal and str(origin) == str(own_terminal):
            return True
    return False


class RealtimeWorker(QThread):
    """Hilo que escucha cambios realtime de PocketBase por SSE.

    Emite ``change_received(table, record)`` con cada fila cambiada y
    ``status_changed(text)`` para el estado de la conexión.
    """

    change_received = Signal(str, dict)
    status_changed = Signal(str)

    def __init__(self, realtime_url: str, token: str, branch_id: str, terminal_id: str, parent=None):
        super().__init__(parent)
        self._url = realtime_url
        self._token = token
        self._branch = branch_id
        self._terminal = terminal_id
        self._stop = False

    def stop(self, timeout_ms: int = 8000) -> None:
        self._stop = True
        self.requestInterruption()
        self.wait(timeout_ms)

    def run(self) -> None:
        try:
            self._main()
        except Exception as exc:  # noqa: BLE001 - el arranque no debe romper la app
            log.exception("Realtime: no se pudo iniciar la escucha.")
            self.status_changed.emit(f"error:{exc}")
        finally:
            self.status_changed.emit("detenido")

    def _main(self) -> None:
        from pocketbase import Client

        client: Client | None = None
        try:
            self.status_changed.emit("conectando")
            base = self._url.rstrip("/")
            client = Client(base)
            if self._token:
                client.auth_store.save(self._token, None)

            health = client.health.check()
            if health.code != 200:
                self.status_changed.emit("error:servidor no accesible")
                return

            def make_handler(table: str):
                def handle(message) -> None:  # noqa: ANN001 - tipos de la SDK
                    record = _record_to_dict(getattr(message, "record", None))
                    if should_ignore_change(table, record, self._branch, self._terminal):
                        return
                    log.info("Realtime: cambio en %s -> %s", table, record)
                    self.change_received.emit(table, record)

                return handle

            # Suscripción nativa de PocketBase (SSE). El inventario y las ventas
            # de la sucursal, más las colecciones de catálogo global.
            for table in ("pos_categories", "pos_products", "pos_inventory", "pos_sales"):
                client.collection(table).subscribe(make_handler(table))

            self.status_changed.emit("conectado")
            while not self._stop and not self.isInterruptionRequested():
                time.sleep(0.5)
        finally:
            if client is not None:
                try:
                    client.realtime.unsubscribe()
                except Exception:  # noqa: BLE001 - cierre best-effort
                    pass
                try:
                    client.auth_store.clear()
                except Exception:  # noqa: BLE001 - cierre best-effort
                    pass


def _record_to_dict(record) -> dict:  # noqa: ANN001 - tipos de la SDK
    """Extrae los campos del ``Record`` de la SDK como dict plano."""
    if record is None:
        return {}
    data: dict = {}
    for key, value in vars(record).items():
        if key in ("expand", "collection_id", "collection_name"):
            continue
        data[key] = value
    return data