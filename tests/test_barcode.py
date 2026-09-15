"""Tests del motor de códigos de barras (puro, sin Qt)."""

from __future__ import annotations

import pytest

from app.domain.exceptions import ValidationError
from app.infrastructure.labels.barcode import (
    code128b_modules,
    ean13_check_digit,
    ean13_modules,
    encode_barcode,
)


def test_ean13_check_digit() -> None:
    assert ean13_check_digit("750105530208") == 6
    assert ean13_check_digit("400638133393") == 1


def test_ean13_modules_longitud_95() -> None:
    bits = ean13_modules("7501055302082")
    assert len(bits) == 95
    assert bits.startswith("101")
    assert bits.endswith("101")
    assert "01010" in bits


def test_ean13_formato_invalido() -> None:
    with pytest.raises(ValidationError):
        ean13_modules("abcdefghijklm")
    with pytest.raises(ValidationError):
        ean13_modules("123")


def test_ean13_autocompleta_12_digitos() -> None:
    bits = ean13_modules("400638133393")
    assert len(bits) == 95


def test_code128b_estructura() -> None:
    bits = code128b_modules("KIOSCO")
    # 1 start + 6 datos + 1 checksum (11 módulos c/u) + stop (13 módulos)
    assert len(bits) == (1 + 6 + 1) * 11 + 13
    assert bits.startswith(_widths_to_modules("211214"))  # Start-B


def _widths_to_modules(pattern: str) -> str:
    modules: list[str] = []
    bar = True
    for width in pattern:
        modules.append("1" if bar else "0")
        modules.extend(["1" if bar else "0"] * (int(width) - 1))
        bar = not bar
    return "".join(modules)


def test_code128b_rechaza_no_imprimibles() -> None:
    with pytest.raises(ValidationError):
        code128b_modules("caf\x01")
    with pytest.raises(ValidationError):
        code128b_modules("ñandú")


def test_encode_elige_motores() -> None:
    assert len(encode_barcode("7501055302082")) == 95  # EAN-13
    assert encode_barcode("PAPAS-01").startswith("11010010000")  # Start-B
    with pytest.raises(ValidationError):
        encode_barcode("")