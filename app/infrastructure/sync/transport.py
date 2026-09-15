"""Cliente REST (PostgREST) contra Supabase con urllib (sin dependencias extra).

Funciona con la ``anon key`` publicable del proyecto. Todas las tablas de
sincronización viven en el esquema ``public`` con prefijo ``pos_`` y exposan
claves naturales (``code``, ``(product_code, branch_id)``, ``receipt_number``)
para que el terminal local encuentre sus filas sin mapeo de ids.
"""

from __future__ import annotations

import json
from typing import Any
from urllib import request as urllib_request
from urllib.error import HTTPError
from urllib.parse import urlencode


class SyncTransportError(RuntimeError):
    """Fallo de red o HTTP al hablar con el hub de sincronización."""


class RestClient:
    """Envoltorio mínimo de la API REST de Supabase (PostgREST)."""

    def __init__(self, base_url: str, anon_key: str):
        self._base = base_url.rstrip("/")
        self._key = anon_key

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def fetch(self, table: str, filters: dict[str, str] | None = None, order: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        """Descarga todas las filas de una tabla (con paginación por offset)."""
        out: list[dict[str, Any]] = []
        offset = 0
        filters = filters or {}
        while True:
            params: dict[str, str] = {"select": "*", "limit": str(limit), "offset": str(offset)}
            params.update({name: f"eq.{value}" for name, value in filters.items()})
            if order:
                params["order"] = order
            query = urlencode(params)
            rows = self._request("GET", f"/rest/v1/{table}?{query}")
            out.extend(rows or [])
            if not rows or len(rows) < limit:
                break
            offset += limit
        return out

    def post(
        self,
        table: str,
        rows: list[dict[str, Any]],
        prefer: str = "resolution=merge-duplicates",
        on_conflict: str | None = None,
    ) -> list[dict[str, Any]]:
        """Inserta/actualiza una tanda de filas (upsert contra columnas únicas).

        ``on_conflict`` son las columnas únicas que determinan el upsert
        (PostgREST sólo resuelve conflictos contra el PK o estas columnas).
        """
        if not rows:
            return []
        rel = f"/rest/v1/{table}"
        if on_conflict:
            rel += f"?on_conflict={on_conflict}"
        return self._request("POST", rel, payload=rows, prefer=prefer) or []

    def _request(self, method: str, rel_url: str, payload: Any = None, prefer: str | None = None, timeout: int = 90) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib_request.Request(self._base + rel_url, data=body, method=method, headers=self._headers(prefer))
        try:
            with urllib_request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise SyncTransportError(f"HTTP {exc.code} en {method} {rel_url}: {detail}") from exc
        except OSError as exc:
            raise SyncTransportError(f"No se pudo contactar el hub ({method} {rel_url}): {exc}") from exc