"""Cliente de sincronización contra el hub PocketBase (LAN).

Este módulo re-expone :class:`PocketBaseClient` (antes ``RestClient`` de
Supabase/PostgREST) para no romper importaciones existentes. El cliente real
vive en ``app.infrastructure.sync.pocketbase_client`` y mantiene la misma
interfaz que espera el motor de sincronización (``fetch`` / ``post``).
"""

from __future__ import annotations

from app.infrastructure.sync.pocketbase_client import (
    DEFAULT_POCKETBASE_URL,
    PocketBaseClient,
    SyncTransportError,
)

__all__ = ["PocketBaseClient", "SyncTransportError", "DEFAULT_POCKETBASE_URL"]

#: Alias de compatibilidad con el nombre histórico del transporte.
RestClient = PocketBaseClient