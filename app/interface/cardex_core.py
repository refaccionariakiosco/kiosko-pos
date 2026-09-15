"""Lógica pura del cardex (sin dependencias de Qt) para cálculo y pruebas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

REASON_LABELS = {
    "VENTA": "Venta",
    "COMPRA": "Compra",
    "AJUSTE": "Ajuste",
    "ANULACION": "Anulación de venta",
    "APARTADO": "Apartado",
    "CANCEL_APARTADO": "Cancelación de apartado",
    "DEVOLUCION": "Devolución",
}

_STATE_COLORS = {
    "INACTIVO": "#9ca3af",
    "SIN STOCK": "#9ca3af",
    "STOCK BAJO": "#d97706",
    "ACTIVO": "#16a34a",
}

_CAPTIONS = ("Código", "Nombre", "Categoría", "Precio", "Costo", "Stock", "Mín.", "Estado")


@dataclass(slots=True, frozen=True)
class CardexRow:
    created_at: datetime
    concept: str
    entrada: int
    salida: int
    saldo: int
    document: str
    note: str = ""


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason or "", reason or "")


def concept_text(reason: str, note: str, document: str) -> str:
    note = (note or "").strip()
    if note and document and document in note:
        return reason_label(reason)
    if note:
        return f"{reason_label(reason)} — {note}"
    return reason_label(reason)


def compute_cardex_rows(movements: list, current_stock: int) -> list[CardexRow]:
    """Convierte movimientos (descendentes) en filas cronológicas con saldo corrido.

    El saldo se ancla en el stock actual y se reconstruye hacia atrás recorriendo
    los movimientos de más nuevo a más viejo.
    """
    saldo = current_stock
    saldos: dict[int, int] = {}
    for movement in movements:
        saldos[movement.id] = saldo
        saldo -= movement.delta
    rows: list[CardexRow] = []
    for movement in reversed(movements):
        rows.append(
            CardexRow(
                created_at=movement.created_at,
                concept=concept_text(movement.reason, movement.note, movement.document),
                entrada=movement.delta if movement.delta > 0 else 0,
                salida=-movement.delta if movement.delta < 0 else 0,
                saldo=saldos[movement.id],
                document=movement.document,
                note=movement.note,
            )
        )
    return rows