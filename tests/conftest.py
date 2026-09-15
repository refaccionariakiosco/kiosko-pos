"""Fixtures compartidos de tests (base SQLite en memoria, sin Qt)."""

from __future__ import annotations

import pytest


@pytest.fixture
def services():
    """Servicios completos de la aplicación sobre una base en memoria."""
    from app.bootstrap import build_services
    from app.settings import Settings

    services = build_services(settings=Settings(database_path=":memory:"))
    return services