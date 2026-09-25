"""Tests del cliente PocketBase: normalización de URL y mensajes de error cortos."""

from __future__ import annotations

from pocketbase.errors import ClientResponseError

from app.infrastructure.sync.pocketbase_client import (
    DEFAULT_POCKETBASE_URL,
    PocketBaseClient,
    SyncTransportError,
)


def test_normaliza_url_sin_esquema():
    client = PocketBaseClient(base_url="192.168.100.6:8090")
    assert client.base_url == "http://192.168.100.6:8090"


def test_normaliza_url_con_http_se_mantiene():
    client = PocketBaseClient(base_url="http://192.168.100.6:8090/")
    assert client.base_url == "http://192.168.100.6:8090"


def test_mantiene_https():
    client = PocketBaseClient(base_url="https://hub.ejemplo.com:443/")
    assert client.base_url == "https://hub.ejemplo.com:443"


def test_default_cuando_no_hay_url():
    client = PocketBaseClient(base_url="")
    assert client.base_url == DEFAULT_POCKETBASE_URL


def test_error_transporte_corto():
    exc = ClientResponseError(status=404, data={"message": "admins: no record found."})
    err = PocketBaseClient._as_transport_error(exc, "GET", "pos_products")
    assert isinstance(err, SyncTransportError)
    text = str(err)
    assert "HTTP 404" in text
    assert len(text) <= 160


def test_error_transporte_trunca_detalle_largo():
    exc = ClientResponseError(status=500, data={"message": "x" * 500})
    err = PocketBaseClient._as_transport_error(exc, "POST", "pos_sale_payments")
    text = str(err)
    assert "HTTP 500" in text
    assert len(text) <= 160