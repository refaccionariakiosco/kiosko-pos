"""Sincronización offline-first con el hub Supabase (v1: catálogo + inventario + ventas)."""

from app.infrastructure.sync.engine import SyncEngine, SyncReport, run_sync

__all__ = ["SyncEngine", "SyncReport", "run_sync"]