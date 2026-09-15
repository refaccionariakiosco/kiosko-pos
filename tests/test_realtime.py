"""Tests de la capa Realtime (filtrado CDC) y del coordinador de sync."""

from __future__ import annotations

from app.interface.realtime_worker import should_ignore_change
from app.interface.sync_coordinator import DEBOUNCE_MS, MIN_INTERVAL_S


def test_ignora_venta_propia():
    record = {"branch_id": "SUC-1", "terminal_id": "T-AA", "receipt_number": "1"}
    assert should_ignore_change("pos_sales", record, "SUC-1", "T-AA") is True


def test_acepta_venta_de_otro_terminal_misma_sucursal():
    record = {"branch_id": "SUC-1", "terminal_id": "T-BB", "receipt_number": "2"}
    assert should_ignore_change("pos_sales", record, "SUC-1", "T-AA") is False


def test_ignora_venta_de_otra_sucursal():
    record = {"branch_id": "SUC-2", "terminal_id": "T-BB", "receipt_number": "3"}
    assert should_ignore_change("pos_sales", record, "SUC-1", "T-AA") is True


def test_ignora_inventario_de_otra_sucursal():
    record = {"branch_id": "SUC-2", "product_code": "A", "stock": 5}
    assert should_ignore_change("pos_inventory", record, "SUC-1", "T-AA") is True


def test_acepta_inventario_de_la_misma_sucursal():
    record = {"branch_id": "SUC-1", "product_code": "A", "stock": 5}
    assert should_ignore_change("pos_inventory", record, "SUC-1", "T-AA") is False


def test_ignora_registro_vacio():
    assert should_ignore_change("pos_sales", {}, "SUC-1", "T-AA") is True
    assert should_ignore_change("pos_sales", None, "SUC-1", "T-AA") is True


def test_catalogo_global_siempre_aceptado():
    record = {"code": "PROD", "name": "X"}
    assert should_ignore_change("pos_products", record, "SUC-1", "T-AA") is False
    assert should_ignore_change("pos_categories", record, "SUC-1", "T-AA") is False


def test_constantes_de_debounce_sensatas():
    assert 0 < DEBOUNCE_MS <= 15_000
    assert 0 < MIN_INTERVAL_S <= 120