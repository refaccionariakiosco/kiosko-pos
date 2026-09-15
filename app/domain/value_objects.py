"""Value objects del dominio.

``Money`` es el tipo canónico para todo importe. Se basa en ``Decimal`` para
evitar artefactos de punto flotante y siempre se normaliza a 2 decimales.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Union

from app.domain.exceptions import ValidationError

DECIMAL_PLACES = 2
QUANT = Decimal("0.01")

NumberLike = Union["Money", Decimal, int, str, float]


def _to_amount(value: NumberLike) -> Decimal:
    if isinstance(value, Money):
        return value.amount
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValidationError(f"Importe no válido: {value!r}") from exc
    raise ValidationError(f"Tipo de importe no soportado: {type(value)!r}")


@dataclass(frozen=True, slots=True)
class Money:
    """Importe monetario inmutariable, normalizado a 2 decimales."""

    amount: Decimal
    currency: str = "$"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", _to_amount(self.amount))
        rounded = self.amount.quantize(QUANT, rounding=ROUND_HALF_UP)
        object.__setattr__(self, "amount", rounded)
        if rounded < 0:
            raise ValidationError("Un importe monetario no puede ser negativo.")

    @classmethod
    def zero(cls, currency: str = "$") -> "Money":
        return cls(Decimal("0.00"), currency)

    @classmethod
    def from_input(cls, value: NumberLike, currency: str = "$") -> "Money":
        return cls(_to_amount(value), currency)

    def __add__(self, other: NumberLike) -> "Money":
        return Money(self.amount + _to_amount(other), self.currency)

    def __sub__(self, other: NumberLike) -> "Money":
        return Money(self.amount - _to_amount(other), self.currency)

    def __mul__(self, factor: Union[int, Decimal]) -> "Money":
        return Money(self.amount * Decimal(factor), self.currency)

    def __lt__(self, other: NumberLike) -> bool:
        return self.amount < _to_amount(other)

    def __le__(self, other: NumberLike) -> bool:
        return self.amount <= _to_amount(other)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Money):
            return self.amount == other.amount and self.currency == other.currency
        return super().__eq__(other)

    def __gt__(self, other: NumberLike) -> bool:
        return self.amount > _to_amount(other)

    def __ge__(self, other: NumberLike) -> bool:
        return self.amount >= _to_amount(other)

    def as_decimal(self) -> Decimal:
        return self.amount

    def format(self, currency: bool = True) -> str:
        """Formatea como ``$1.234,56`` (es-AR / es-MX usan coma decimal)."""
        sign = "-" if self.amount < 0 else ""
        a = abs(self.amount)
        integer, _, frac = format(a, "f").partition(".")
        grouped = ""
        for i, ch in enumerate(reversed(integer)):
            if i and i % 3 == 0:
                grouped = "." + grouped
            grouped = ch + grouped
        text = f"{sign}{grouped},{frac or '00'}"
        return f"{self.currency} {text}" if currency else text