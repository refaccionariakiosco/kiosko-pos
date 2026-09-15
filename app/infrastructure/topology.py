"""Identidad de la instalación (topología sucursal/terminal) y configuración de nube.

Fase 1 del plan upgrade: cada instalación es un terminal de caja que pertenece a
una sucursal. La identidad vive en la tabla local ``sys_config`` (clave/valor)
para poder configurarse al momento de la instalación sin tocar código.

Mecanismo de replicación selectiva (cómo el worker de sincronización usará estas
variables):

- ``id_sucursal``: grupo lógico del inventario y de las ventas. Al sincronizar,
  el worker SOLO descarga el catálogo global (products) y el inventario de su
  sucursal (inventory WHERE branch_id = id_sucursal). Las existencias de otras
  sucursales nunca llegan a este terminal.
- ``id_terminal``: identifica la caja registradora dentro de la sucursal. Permite
  que varias cajas trabajen offline contra la misma sucursal; al volver online,
  cada una sube sus ventas y el worker descarga las ventas de sus terminales
  hermanas (misma sucursal) para mantener el historial y las existencias
  consistentes (última escritura gana).
- ``supabase_url`` / ``supabase_anon_key``: endpoint de la nube. Si están vacías,
  el terminal opera 100% offline con su catálogo e inventario local.

Un terminal sin ``id_sucursal`` asignado usa la sucursal sintética
``DEFAULT_BRANCH_ID`` (modo single-site heredado): el stock sigue funcionando
igual que antes de la migración, sin mezclarse con la nube.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import select, text
from sqlalchemy.orm import Session

KEY_BRANCH = "id_sucursal"
KEY_TERMINAL = "id_terminal"
KEY_TERMINAL_NUM = "terminal_num"
KEY_SUPABASE_URL = "supabase_url"
KEY_SUPABASE_ANON_KEY = "supabase_anon_key"

#: Sucursal sintética para instalaciones todavía no asociadas a la nube.
DEFAULT_BRANCH_ID = "BRANCH-LOCAL"

_TOPOLOGY_KEYS = (KEY_BRANCH, KEY_TERMINAL, KEY_TERMINAL_NUM, KEY_SUPABASE_URL, KEY_SUPABASE_ANON_KEY)


@dataclass(frozen=True, slots=True)
class Topology:
    """Identidad de la instalación leída desde ``sys_config``."""

    id_sucursal: str = ""
    id_terminal: str = ""
    terminal_num: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""

    @property
    def effective_branch_id(self) -> str:
        """Sucursal usada para leer/escribir el inventario local."""
        return self.id_sucursal or DEFAULT_BRANCH_ID

    @property
    def is_cloud_configured(self) -> bool:
        return bool(self.id_sucursal and self.supabase_url)

    @property
    def receipt_prefix(self) -> str:
        """Número de caja saneado (1-9999) usado como prefijo del recibo.

        Con varias cajas en la misma sucursal cada una genera tickets
        ``R-<caja>-<secuencia>`` para que nunca se dupliquen los folios.
        """
        digits = re.sub(r"\D", "", self.terminal_num)
        return digits[-4:] or "1"

    def as_dict(self) -> dict[str, str]:
        return {
            KEY_BRANCH: self.id_sucursal,
            KEY_TERMINAL: self.id_terminal,
            KEY_TERMINAL_NUM: self.terminal_num,
            KEY_SUPABASE_URL: self.supabase_url,
            KEY_SUPABASE_ANON_KEY: self.supabase_anon_key,
        }

    def sync_filters(self) -> dict[str, str]:
        """Filtros que el worker de sincronización debe aplicar al descargar."""
        return {
            "id_sucursal": self.id_sucursal,
            "id_terminal": self.id_terminal,
            "inventory": "solo sucursal propia",
            "catalog": "global (todos los productos)",
            "sales": "todas las ventas de la sucursal (terminales hermanos)",
        }


def _new_terminal_id() -> str:
    return f"T-{uuid.uuid4().hex[:10].upper()}"


def get_terminal_num(session: Session) -> str:
    """Número de caja saneado (1-9999) persistido en ``sys_config``."""
    from app.infrastructure.orm import SysConfigRow

    row = session.execute(
        select(SysConfigRow).where(SysConfigRow.key == KEY_TERMINAL_NUM)
    ).scalar_one_or_none()
    digits = re.sub(r"\D", "", (row.value if row is not None else "") or "")
    return digits[-4:] or "1"


def load_topology(session: Session) -> Topology:
    """Lee la topología actual sin crear nada."""
    from app.infrastructure.orm import SysConfigRow

    rows = session.execute(
        select(SysConfigRow).where(SysConfigRow.key.in_(_TOPOLOGY_KEYS))
    ).scalars().all()
    values = {r.key: r.value for r in rows}
    return Topology(
        id_sucursal=values.get(KEY_BRANCH, ""),
        id_terminal=values.get(KEY_TERMINAL, ""),
        terminal_num=values.get(KEY_TERMINAL_NUM, ""),
        supabase_url=values.get(KEY_SUPABASE_URL, ""),
        supabase_anon_key=values.get(KEY_SUPABASE_ANON_KEY, ""),
    )


def ensure_topology(
    session: Session,
    overrides: Mapping[str, str] | None = None,
) -> Topology:
    """Garantiza la tabla ``sys_config`` poblada y aplica sobrescrituras.

    Se invoca una vez al arrancar la aplicación. Un terminal sin identidad
    recibe un ``id_terminal`` nuevo pero SIN sucursal (modo local heredado).
    """
    from app.infrastructure.orm import SysConfigRow

    overrides = dict(overrides or {})
    session.execute(
        text("CREATE TABLE IF NOT EXISTS sys_config (key TEXT PRIMARY KEY, value TEXT, updated_at DATETIME NOT NULL)")
    )
    # seeds implícitos (solo si la clave aún no existe)
    seed_defaults = {KEY_TERMINAL: _new_terminal_id(), KEY_TERMINAL_NUM: "1"}
    existing = {
        r.key
        for r in session.execute(
            text("SELECT key FROM sys_config")
        ).all()
    }
    for key, default in seed_defaults.items():
        if key not in existing and key not in overrides:
            session.execute(
                text("INSERT INTO sys_config (key, value, updated_at) VALUES (:k, :v, datetime('now'))"),
                {"k": key, "v": default},
            )
    for key, value in overrides.items():
        if not value:
            continue
        session.execute(
            text(
                "INSERT INTO sys_config (key, value, updated_at) VALUES (:k, :v, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
            ),
            {"k": key, "v": str(value)},
        )
    session.commit()
    return load_topology(session)


def save_topology(session: Session, topology: Topology) -> Topology:
    """Persiste una topología completa (usada por la configuración de instalación)."""
    return ensure_topology(session, overrides=topology.as_dict())