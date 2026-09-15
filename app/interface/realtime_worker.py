"""Escucha de cambios Postgres CDC (Supabase Realtime) en segundo plano.

Usa la librería oficial ``realtime`` de PyPI (WebSocket + Phoenix channels con
heartbeat y reconexión). Cada cambio de las tablas ``pos_*`` se emite como señal
Qt; el coordinador de sync decide qué hacer (pull con debounce).
"""

from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)

#: Tablas replicadas que interesan a un terminal.
REALTIME_TABLES = (
    "pos_categories",
    "pos_products",
    "pos_inventory",
    "pos_sales",
    "pos_sale_items",
    "pos_sale_payments",
)

#: Tablas de sucursal (el resto son catálogo global).
BRANCH_TABLES = ("pos_inventory", "pos_sales")


def should_ignore_change(table: str, record: dict, own_branch: str, own_terminal: str) -> bool:
    """Decide si un evento CDC debe descartarse.

    - Un terminal ignora los cambios que produjo él mismo (propio ``terminal_id``
      en ventas) para no entrar en un bucle push -> realtime -> pull.
    - El inventario y las ventas de otras sucursales nunca deben tirar el pull
      de este terminal.
    - Los catálogos (productos/categorías) son globales: se aceptan siempre.
    """
    if record is None:
        return True
    if table == "pos_sales":
        if str(record.get("terminal_id") or "") == str(own_terminal or ""):
            return True
        if str(record.get("branch_id") or "") != str(own_branch or ""):
            return True
        return False
    if table == "pos_inventory":
        if str(record.get("branch_id") or "") != str(own_branch or ""):
            return True
        return False
    return False


class RealtimeWorker(QThread):
    """Hilo con un event-loop asyncio que escucha cambios CDC de Supabase.

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

    def run(self) -> None:  # noqa: C901 - flujo asyncio del loop de escucha
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main(loop))
        except Exception as exc:  # noqa: BLE001 - el arranque no debe romper la app
            log.exception("Realtime: no se pudo iniciar la escucha.")
            self.status_changed.emit(f"error:{exc}")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()
            self.status_changed.emit("detenido")

    async def _main(self, loop: object) -> None:  # noqa: ANN201
        from realtime import AsyncRealtimeClient, RealtimeSubscribeStates

        client: AsyncRealtimeClient | None = None
        try:
            self.status_changed.emit("conectando")
            base = self._url.rstrip("/")
            if not base.endswith("/realtime/v1"):
                base = f"{base}/realtime/v1"
            client = AsyncRealtimeClient(base, self._token, auto_reconnect=True)
            channel = client.channel("pos-realtime")

            def make_handler(table: str):
                def handle(payload) -> None:  # noqa: ANN001 - tipos de la librería
                    data = getattr(payload, "data", None)
                    record = dict(getattr(data, "record", None) or {})
                    if should_ignore_change(table, record, self._branch, self._terminal):
                        return
                    log.info("Realtime: cambio en %s -> %s", table, record)
                    self.change_received.emit(table, record)

                return handle

            for table, extra in (
                ("pos_sales", {"filter": f"branch_id=eq.{self._branch}"}),
                ("pos_inventory", {"filter": f"branch_id=eq.{self._branch}"}),
                ("pos_products", {}),
                ("pos_categories", {}),
            ):
                channel.on_postgres_changes(
                    "*", schema="public", table=table, callback=make_handler(table), **extra
                )

            def on_status(status, err) -> None:  # noqa: ANN001
                if status == RealtimeSubscribeStates.SUBSCRIBED:
                    self.status_changed.emit("conectado")
                elif status == RealtimeSubscribeStates.CHANNEL_ERROR:
                    self.status_changed.emit(f"error:{err}")

            await client.connect()
            await channel.subscribe(on_status)
            while not self._stop and not self.isInterruptionRequested():
                await asyncio.sleep(0.5)
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:  # noqa: BLE001 - cierre best-effort
                    pass