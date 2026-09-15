"""Codificación de códigos de barras (EAN-13 y CODE128), sin dependencias.

Determinístico: las secuencias de módulos se generan como listas de 0/1 y el
renderizado a imagen lo hace Qt (QPainter) con ancho de módulo exacto.
"""

from __future__ import annotations

from app.domain.exceptions import ValidationError


def ean13_check_digit(digits: str) -> int:
    """Dígito verificador EAN-13 para los primeros 12 dígitos."""
    if len(digits) != 12 or not digits.isdigit():
        raise ValidationError("EAN-13 requiere exactamente 12 dígitos.")
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
    return (10 - (total % 10)) % 10


# Estructuras EAN-13 (7 módulos por dígito)
_L = {
    "0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101", "4": "0100011",
    "5": "0110001", "6": "0101111", "7": "0111011", "8": "0110111", "9": "0001011",
}
_G = {
    "0": "0100111", "1": "0110011", "2": "0011011", "3": "0100001", "4": "0011101",
    "5": "0111001", "6": "0000101", "7": "0010001", "8": "0001001", "9": "0010111",
}
_R = {
    "0": "1110010", "1": "1100110", "2": "1101100", "3": "1000010", "4": "1011100",
    "5": "1001110", "6": "1010000", "7": "1000100", "8": "1001000", "9": "1110100",
}
_PATTERNS = {
    "0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL", "4": "LGLLGG",
    "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG", "8": "LGLGGL", "9": "LGGLGL",
}
_START_GUARD = "101"
_CENTER_GUARD = "01010"
_END_GUARD = "101"


def ean13_modules(code: str) -> str:
    """Convierte un código EAN-13 (13 dígitos) en tira de módulos (95 bits)."""
    code = code.strip()
    if len(code) == 12 and code.isdigit():
        code = code + str(ean13_check_digit(code))
    if len(code) != 13 or not code.isdigit():
        raise ValidationError(f"El código {code!r} no es un EAN-13 válido.")
    first = code[0]
    pattern = _PATTERNS[first]
    bits = [_START_GUARD]
    for i, digit in enumerate(code[1:7]):
        table = _L if pattern[i] == "L" else _G
        bits.append(table[digit])
    bits.append(_CENTER_GUARD)
    for digit in code[7:13]:
        bits.append(_R[digit])
    bits.append(_END_GUARD)
    return "".join(bits)


# --------------------------------------------------------------------------- #
# CODE 128: tabla canónica de patrones (elementos por símbolo, empieza con barra)
# --------------------------------------------------------------------------- #

_CODE128_PATTERNS: tuple[str, ...] = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232", "2331112",
)
_START_B = 104
_STOP = "2331112"


def _widths_to_modules(pattern: str) -> str:
    """Convierte un patrón de anchos (barra/espacio alternados) en 0/1 por módulo."""
    modules: list[str] = []
    bar = True
    for width in pattern:
        modules.append("1" if bar else "0")
        # expandir el módulo repetido `width` veces
        modules.extend(["1" if bar else "0"] * (int(width) - 1))
        bar = not bar
    return "".join(modules)


def code128b_modules(data: str) -> str:
    """Codifica texto en CODE128 - juego B (ASCII imprimible 32..126).

    Devuelve una tira de 0/1 por módulo (igual que EAN-13) lista para dibujar.
    """
    values: list[int] = []
    for ch in data:
        if not 32 <= ord(ch) <= 126:
            raise ValidationError(f"CODE128-B no soporta el carácter {ch!r}.")
        values.append(ord(ch) - 32)
    checksum = _START_B + sum(v * (i + 1) for i, v in enumerate(values))
    checksum %= 103
    symbols = [_CODE128_PATTERNS[_START_B]]
    symbols.extend(_CODE128_PATTERNS[v] for v in values)
    symbols.append(_CODE128_PATTERNS[checksum])
    symbols.append(_STOP)
    return "".join(_widths_to_modules(pattern) for pattern in symbols)


def encode_barcode(value: str) -> str:
    """Elige EAN-13 para códigos de 13 dígitos; CODE128-B para el resto."""
    value = (value or "").strip()
    if not value:
        raise ValidationError("No hay código para imprimir.")
    if len(value) == 13 and value.isdigit():
        return ean13_modules(value)
    return code128b_modules(value) if value else ""


def module_width_mm(modules: int, available_mm: float, quiet_mm: float = 0.0) -> float:
    return (available_mm - 2 * quiet_mm) / max(modules, 1)