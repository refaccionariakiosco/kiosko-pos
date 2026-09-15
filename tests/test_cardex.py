"""Pruebas del cardex: cálculo de saldo corrido, etiquetas de motivo y folios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.interface.cardex_core import (
    REASON_LABELS,
    compute_cardex_rows,
    concept_text,
    reason_label,
)


@dataclass
class FakeMovement:
    id: int
    delta: int
    reason: str
    note: str = ""
    document: str = ""
    created_at: datetime = datetime(2026, 9, 12, 10, 0)


def _newest_first(*movements) -> list:
    return list(reversed(movements))


def test_saldo_corrido_anclado_al_stock_actual():
    # Línea de tiempo: compra +10 (stock 10), venta -5 (stock 5), ajuste +2 (stock 7).
    compra = FakeMovement(1, +10, "COMPRA", document="P-0001")
    venta = FakeMovement(2, -5, "VENTA", note="Venta R-000123", document="R-000123")
    ajuste = FakeMovement(3, +2, "AJUSTE")

    rows = compute_cardex_rows(_newest_first(compra, venta, ajuste), current_stock=7)

    assert [r.saldo for r in rows] == [10, 5, 7]
    assert [r.entrada for r in rows] == [10, 0, 2]
    assert [r.salida for r in rows] == [0, 5, 0]
    assert [r.document for r in rows] == ["P-0001", "R-000123", ""]


def test_orden_cronologico_ascendente():
    compra = FakeMovement(1, +10, "COMPRA")
    venta = FakeMovement(2, -5, "VENTA")
    rows = compute_cardex_rows(_newest_first(compra, venta), current_stock=5)
    assert [r.document or r.concept for r in rows] == ["Compra", "Venta"]


def test_sin_movimientos():
    assert compute_cardex_rows([], current_stock=50) == []


def test_motivos_legibles():
    assert reason_label("VENTA") == "Venta"
    assert reason_label("ANULACION") == "Anulación de venta"
    assert reason_label("COMPRA") == "Compra"
    assert reason_label("CANCEL_APARTADO") == "Cancelación de apartado"
    assert reason_label("DESCONOCIDO") == "DESCONOCIDO"
    assert "VENTA" in REASON_LABELS


def test_concepto_no_duplica_el_folio_de_la_nota():
    assert concept_text("VENTA", "Venta R-000123", "R-000123") == "Venta"
    assert concept_text("APARTADO", "Apartado para Juan", "A-3") == "Apartado — Apartado para Juan"
    assert concept_text("COMPRA", "", "P-0001") == "Compra"