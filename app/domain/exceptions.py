"""Errores de la capa de dominio."""


class DomainError(Exception):
    """Error base de la capa de dominio."""


class ValidationError(DomainError):
    """Un valor no cumple una invariante de negocio."""


class ProductNotFoundError(DomainError):
    """No existe un producto con los criterios indicados."""


class DuplicateProductCodeError(DomainError):
    """Ya existe un producto con el mismo código/barras."""


class CategoryNotFoundError(DomainError):
    """No existe la categoría indicada."""


class InsufficientStockError(DomainError):
    """El stock de un producto es insuficiente para la operación."""


class InvalidQuantityError(DomainError):
    """Una cantidad no es válida (menor o igual a cero)."""


class SaleNotFoundError(DomainError):
    """No existe una venta con el identificador indicado."""


class SaleAlreadyVoidedError(DomainError):
    """La venta ya fue anulada."""


class SaleItemRefundError(DomainError):
    """No se puede devolver el renglón solicitado de la venta."""


class NoOpenCashDayError(DomainError):
    """No hay una jornada de caja abierta para la operación."""


class CashDayAlreadyOpenError(DomainError):
    """Ya existe una jornada de caja abierta."""


class CashDayAlreadyClosedError(DomainError):
    """La jornada de caja ya fue cerrada."""


class ApartadoNotFoundError(DomainError):
    """No existe un apartado con el identificador indicado."""


class ProviderNotFoundError(DomainError):
    """No existe un proveedor con el identificador indicado."""


class ValeNotFoundError(DomainError):
    """No existe un vale con el código o identificador indicado."""


class ValeAlreadyVoidedError(DomainError):
    """El vale ya fue anulado y no admite más movimientos."""


class InsufficientValeBalanceError(DomainError):
    """El saldo del vale no alcanza para cubrir el importe solicitado."""