"""Cliente PocketBase (REST + realtime) para las sucursales en LAN.

Reemplaza el antiguo cliente PostgREST/Supabase manteniendo la MISMA interfaz
que espera el motor de sincronización (``fetch`` / ``post`` con upsert por
claves naturales), de modo que el flujo Offline-First no cambia:

- ``fetch`` descarga registros de una colección con filtros y orden.
- ``post`` hace *upsert* idempotente: busca por las claves naturales indicadas
  en ``on_conflict`` y crea o actualiza con ``pb.collection(...).create()`` /
  ``.update(id, ...)`` (equivalente a ``pb.collection("nombre").upsert()``).
- ``check_connection`` valida que el servidor local esté accesible antes de
  intentar sincronizar (evita esperas largas e interrumpe menos la UI).

La URL base se lee de la configuración (parámetro ``base_url``) o de la
variable de entorno ``POCKETBASE_URL``, con ``http://192.168.100.6:8090`` como
valor por defecto para una instalación self-hosted de red local.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
from pocketbase import Client
from pocketbase.errors import ClientResponseError

#: URL por defecto del servidor PocketBase (self-hosted en la red local/LAN).
DEFAULT_POCKETBASE_URL = "http://192.168.100.6:8090"

#: Zoom de paginación por petición (límite seguro del API de PocketBase).
_PAGE_SIZE = 200


class _Unset:
    """Sentinel: "no enviar este campo" (a diferencia de ``None`` = limpiar)."""

    _instance: "_Unset | None" = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


#: Marca campos que deben omitirse del payload en ``post()``.
#:
#: Sin esto no se puede distinguir "dejá el valor como está" de "ponelo en
#: vacío": un ``None`` hoy se descarta del payload, y al actualizar una venta
#: remota anulada el ``voided_at``/``notes`` nunca se limpiaban, dejando el
#: registro local y remoto divergentes para siempre.
UNSET = _Unset()


class SyncTransportError(RuntimeError):
    """Fallo de red, HTTP o acceso al hablar con el hub PocketBase."""


class PocketBaseClient:
    """Envoltorio REST de PocketBase con la interfaz del motor de sync."""

    def __init__(self, base_url: str = "", token: str = "", timeout: int = 90):
        base_url = PocketBaseClient._normalize_url(base_url or os.environ.get("POCKETBASE_URL", ""))
        if not base_url:
            base_url = DEFAULT_POCKETBASE_URL
        token = token or os.environ.get("POCKETBASE_TOKEN", "").strip()
        self._base_url = base_url
        self._token = token
        self._pb = Client(base_url, timeout=timeout)
        if token:
            self._pb.auth_store.save(token, None)

    @property
    def base_url(self) -> str:
        return self._base_url

    # ------------------------------------------------------------------ #

    def check_connection(self) -> bool:
        """Valida que el servidor local de PocketBase esté accesible."""
        try:
            with httpx.Client(base_url=self._base_url, timeout=5.0) as http:
                response = http.get("/api/health")
                return response.status_code == 200
        except Exception:  # noqa: BLE001 - cualquier fallo de red => sin conexión
            return False

    def authenticate(self, email: str, password: str) -> bool:
        """Autentica contra la colección de usuarios de PocketBase.

        En caso de éxito la SDK guarda el token en el ``auth_store`` y las
        siguientes peticiones viajan autenticadas.
        """
        try:
            self._pb.collection("users").auth_with_password(email, password)
            return True
        except Exception:  # noqa: BLE001 - credenciales inválidas o sin servidor
            return False

    def clear_auth(self) -> None:
        """Cierra la sesión actual (limpia el ``auth_store``)."""
        try:
            self._pb.auth_store.clear()
        except Exception:  # noqa: BLE001 - cierre best-effort
            pass

    # ------------------------------------------------------------------ #
    # Interfaz usada por el motor de sincronización                       #
    # ------------------------------------------------------------------ #

    def fetch(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Descarga todas las filas de una colección (con paginación)."""
        query: dict[str, Any] = {}
        if filters:
            query["filter"] = self._build_filter(filters)
        if order:
            column, _, how = order.partition(".")
            query["sort"] = f"-{column}" if how == "desc" else column
        per_page = min(max(int(limit), 1), _PAGE_SIZE)
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            try:
                result = self._pb.collection(table).get_list(
                    page, per_page, dict(query)
                )
            except ClientResponseError as exc:
                raise self._as_transport_error(exc, "GET", table) from exc
            for record in result.items:
                items.append(self._record_to_dict(record))
            if page >= result.total_pages or not result.items:
                break
            page += 1
        return items

    def post(
        self,
        table: str,
        rows: list[dict[str, Any]],
        prefer: str = "resolution=merge-duplicates",
        on_conflict: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inserta o actualiza una tanda de filas (upsert por claves naturales).

        Emula el comportamiento de ``upsert()`` de PostgREST: por cada fila,
        si ya existe un registro que coincide con las columnas únicas de
        ``on_conflict`` se actualiza; si no, se crea.

        Los campos con valor ``None`` se envían tal cual (para vaciarlos en el
        hub); los marcados con :data:`UNSET` se omiten del payload.
        """
        if not rows:
            return []
        key_cols = [col.strip() for col in (on_conflict or "").split(",") if col.strip()]
        for row in rows:
            payload = {key: value for key, value in row.items() if not isinstance(value, _Unset)}
            existing = None
            if key_cols:
                filter_expr = self._build_filter({key: row.get(key) for key in key_cols})
                existing = self._find_match(table, filter_expr, key_cols, row)
            try:
                if existing is not None:
                    self._pb.collection(table).update(existing["id"], payload)
                else:
                    self._pb.collection(table).create(payload)
            except ClientResponseError as exc:
                raise self._as_transport_error(exc, "POST", table) from exc
        return []

    # ------------------------------------------------------------------ #

    def _find_match(
        self,
        table: str,
        filter_expr: str,
        key_cols: list[str],
        row: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Busca el registro existente que coincide EXACTO con todas las claves."""
        try:
            result = self._pb.collection(table).get_list(1, 5, {"filter": filter_expr})
        except ClientResponseError as exc:
            raise self._as_transport_error(exc, "READ", table) from exc
        for record in result.items:
            data = self._record_to_dict(record)
            if all(data.get(col) == row.get(col) for col in key_cols):
                return data
        return None

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Devuelve la URL entre `http://` y/o `https://` (antepone http:// si falta)."""
        url = (url or "").strip().rstrip("/")
        if url and not re.match(r"^https?://", url, re.IGNORECASE):
            url = "http://" + url
        return url

    @staticmethod
    def _build_filter(filters: dict[str, Any]) -> str:
        """Traduce ``{campo: valor}`` al lenguaje de filtros de PocketBase."""
        parts = []
        for key, value in filters.items():
            if value is None:
                continue
            parts.append(f"{key}={PocketBaseClient._pf_value(value)}")
        return " && ".join(parts)

    @staticmethod
    def _pf_value(value: Any) -> str:
        """Formatea un valor para un filtro PocketBase (strings entre comillas)."""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{text}"'

    @staticmethod
    def _record_to_dict(record: Any) -> dict[str, Any]:
        """Devuelve los campos del registro como dict plano (sin ``expand``)."""
        data: dict[str, Any] = {}
        for key, value in vars(record).items():
            if key in ("expand", "collection_id", "collection_name"):
                continue
            data[key] = value
        return data

    @staticmethod
    def _as_transport_error(exc: ClientResponseError, method: str, table: str) -> SyncTransportError:
        status = exc.status if getattr(exc, "status", None) else "?"
        detail = exc.__str__()[:100].strip()
        return SyncTransportError(f"PocketBase {method} /{table}: HTTP {status} — {detail}")