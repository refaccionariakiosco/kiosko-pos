"""Entidades (agregados) del dominio Kiosco POS."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable

from app.domain.events import (
    AggregateEvents,
    ApartadoAbonado,
    ApartadoCancelled,
    ApartadoSettled,
    CashDayClosed,
    CashDayOpened,
    CashMovementRegistered,
    LowStockAlert,
    ProductCreated,
    ProductDeactivated,
    ProductUpdated,
    SaleCompleted,
    SaleItemRefunded,
    SaleVoided,
    StockAdjusted,
    ValeIssued,
    ValeRedeemed,
    ValeVoided,
)
from app.domain.exceptions import (
    CashDayAlreadyClosedError,
    DuplicateProductCodeError,
    InsufficientStockError,
    InsufficientValeBalanceError,
    InvalidQuantityError,
    SaleAlreadyVoidedError,
    SaleItemRefundError,
    ValeAlreadyVoidedError,
    ValidationError,
)
from app.domain.value_objects import Money


class PaymentMethod(str, Enum):
    CASH = "EFECTIVO"
    CARD = "TARJETA"
    TRANSFER = "TRANSFERENCIA"
    CREDIT = "CREDITO"
    VALE = "VALE"

    @classmethod
    def from_value(cls, value: str) -> "PaymentMethod":
        try:
            return cls(value.upper())
        except ValueError:
            raise ValidationError(f"Método de pago no soportado: {value!r}") from None


def _require_positive_int(value: int, label: str) -> int:
    if value is None:
        raise ValidationError(f"{label} debe ser un entero >= 0.")
    if isinstance(value, float):
        value = int(value)
    if not isinstance(value, int) or value < 0:
        raise ValidationError(f"{label} debe ser un entero >= 0.")
    return value


def _require_positive_quantity(value: int) -> int:
    if not isinstance(value, int) or value <= 0:
        raise InvalidQuantityError("La cantidad debe ser un entero mayor a cero.")
    return value


def _resolve_line_price(catalog_price: Money, unit_price: Money | None) -> Money:
    """Precio efectivo de un renglón: el override inline si viene, el de catálogo si no.

    El precio de catálogo es el valor por defecto. Un override explícito debe ser
    estrictamente mayor a cero (un renglón gratuito o de importe negativo no es
    una venta válida). Los negativos ya los rechaza el constructor de ``Money``.
    """
    if unit_price is None:
        return catalog_price
    if unit_price <= Money.zero():
        raise ValidationError("El precio del renglón debe ser mayor a cero.")
    return unit_price


@dataclass(slots=True)
class Category:
    id: int | None
    name: str
    description: str = ""

    def __post_init__(self) -> None:
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError("El nombre de la categoría es obligatorio.")

    def rename(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise ValidationError("El nombre de la categoría es obligatorio.")
        self.name = name


@dataclass(slots=True)
class Product(AggregateEvents):
    """Agregado Producto: código, precios y existencias.

    Las invariantes de negocio (stock >= 0, precios no negativos, código único
    verificado por el repositorio) se aplican desde aquí.
    """

    code: str
    name: str
    unit_price: Money
    stock: int
    min_stock: int = 0
    category_id: int | None = None
    description: str = ""
    cost: Money | None = None
    wholesale_price: Money | None = None
    active: bool = True
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        super().__init__()
        self.code = (self.code or "").strip().upper()
        self.name = (self.name or "").strip()
        self.stock = _require_positive_int(self.stock, "Stock")
        self.min_stock = _require_positive_int(self.min_stock, "Stock mínimo")
        if not self.code:
            raise ValidationError("El código del producto es obligatorio.")
        if not self.name:
            raise ValidationError("El nombre del producto es obligatorio.")
        self.record_event(ProductCreated(product_id=self.id, code=self.code, name=self.name))

    @property
    def low_stock(self) -> bool:
        return self.active and self.stock > 0 and self.stock <= self.min_stock

    @property
    def out_of_stock(self) -> bool:
        return self.active and self.stock == 0

    def update(
        self,
        *,
        code: str | None = None,
        name: str | None = None,
        unit_price: Money | None = None,
        cost: Money | None = None,
        wholesale_price: Money | None = None,
        category_id: int | None = None,
        min_stock: int | None = None,
        description: str | None = None,
    ) -> None:
        if code is not None:
            self.code = (code or "").strip().upper()
        if name is not None:
            self.name = (name or "").strip()
        if unit_price is not None:
            self.unit_price = unit_price
        if cost is not None:
            self.cost = cost
        if wholesale_price is not None:
            self.wholesale_price = wholesale_price
        if category_id is not None:
            self.category_id = category_id
        if min_stock is not None:
            self.min_stock = _require_positive_int(min_stock, "Stock mínimo")
        if description is not None:
            self.description = description.strip()
        if not self.code or not self.name:
            raise ValidationError("Código y nombre son obligatorios.")
        self.record_event(ProductUpdated(self.id or 0, self.code, self.name))

    def deactivate(self) -> None:
        if not self.active:
            return
        self.active = False
        self.record_event(ProductDeactivated(self.id or 0, self.code))

    def activate(self) -> None:
        self.active = True

    def adjust_stock(self, delta: int, reason: str) -> None:
        """Ajusta existencias validando que nunca queden negativas."""
        if delta == 0:
            return
        new_stock = self.stock + delta
        if new_stock < 0:
            raise InsufficientStockError(
                f"No hay stock suficiente para {self.code} ({self.name}): disponible {self.stock}."
            )
        self.stock = new_stock
        self.record_event(
            StockAdjusted(
                product_id=self.id or 0,
                code=self.code,
                delta=delta,
                reason=reason,
                running_stock=self.stock,
            )
        )
        if 0 < self.stock <= self.min_stock:
            self.record_event(
                LowStockAlert(
                    product_id=self.id or 0,
                    code=self.code,
                    name=self.name,
                    stock=self.stock,
                    min_stock=self.min_stock,
                )
            )

    def assert_code_unique(self, other_codes: Iterable[str]) -> None:
        normalized = (c or "").strip().upper()
        if normalized and normalized in {c.strip().upper() for c in other_codes if c}:
            raise DuplicateProductCodeError(f"Ya existe un producto con el código {normalized}.")


@dataclass(slots=True)
class SaleItem:
    product_id: int
    product_name: str
    quantity: int
    unit_price: Money
    refunded_qty: int = 0
    price_overridden: bool = False

    @property
    def subtotal(self) -> Money:
        return self.unit_price * self.quantity

    @property
    def remaining_qty(self) -> int:
        return self.quantity - self.refunded_qty


@dataclass(slots=True)
class Payment:
    method: PaymentMethod
    amount: Money


@dataclass(slots=True)
class Sale(AggregateEvents):
    """Agregado Venta: encabezado, ítems y pagos. Cálculo del total aquí adentro."""

    receipt_number: str
    tendered: Money | None = None
    id: int | None = None
    status: str = "COMPLETADA"
    created_at: datetime = field(default_factory=datetime.now)
    items: list[SaleItem] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    subtotal: Money = field(default_factory=Money.zero)
    discount: Money = field(default_factory=Money.zero)
    voided_at: datetime | None = None
    void_reason: str = ""

    STATUS_COMPLETED = "COMPLETADA"
    STATUS_VOID = "ANULADA"
    STATUS_RETURNED = "DEVUELTA"

    def __post_init__(self) -> None:
        AggregateEvents.__init__(self)

    def add_item(self, product: Product, quantity: int, *, unit_price: Money | None = None) -> None:
        """Agrega un ítem, valida stock y descuenta existencias del producto.

        ``unit_price`` permite cobrar la partida a un precio distinto al de
        catálogo (ajuste inline del cajero). Si se omite, se usa el precio del
        producto. El precio resuelto queda congelado en el renglón.
        """
        quantity = _require_positive_quantity(quantity)
        if not product.active:
            raise ValidationError(f"El producto {product.code} ({product.name}) está desactivado.")
        product_id = product.id or 0
        price = _resolve_line_price(product.unit_price, unit_price)
        existing = next((i for i in self.items if i.product_id == product_id), None)
        if existing:
            if unit_price is not None and existing.unit_price != price:
                raise ValidationError(
                    f"{product.name} ya está en la venta a {existing.unit_price.format()}. "
                    "Quita la línea antes de cobrarla a otro precio."
                )
            _require_positive_quantity(existing.quantity + quantity)
        product.adjust_stock(-quantity, reason="VENTA")
        if existing:
            existing.quantity += quantity
        else:
            self.items.append(
                SaleItem(
                    product_id=product_id,
                    product_name=product.name,
                    quantity=quantity,
                    unit_price=price,
                    price_overridden=unit_price is not None and price != product.unit_price,
                )
            )
        self.subtotal = sum((i.subtotal for i in self.items), Money.zero())

    def add_payment(self, method: PaymentMethod, amount: Money) -> None:
        if amount.amount <= 0:
            raise ValidationError("El monto del pago debe ser mayor a cero.")
        self.payments.append(Payment(method=method, amount=amount))

    def apply_discount(self, discount: Money) -> None:
        """Ajusta el monto a cobrar: reduce el total sin bajar de cero."""
        if discount < Money.zero():
            raise ValidationError("El descuento no puede ser negativo.")
        if discount > self.subtotal:
            raise ValidationError("El descuento no puede superar el subtotal.")
        self.discount = discount

    @property
    def total(self) -> Money:
        """Total a cobrar: subtotal de los ítems menos el descuento/ajuste."""
        total = self.subtotal - self.discount
        return max(total, Money.zero())

    def paid_amount(self) -> Money:
        return sum((p.amount for p in self.payments), Money.zero())

    @property
    def is_fully_paid(self) -> bool:
        return self.paid_amount() >= self.total

    @property
    def payment_methods(self) -> list[PaymentMethod]:
        return [p.method for p in self.payments]

    def change(self) -> Money:
        if self.tendered is None or self.tendered <= self.total:
            return Money.zero()
        return self.tendered - self.total

    def finalize(self) -> None:
        if not self.items:
            raise ValidationError("No se puede completar una venta sin ítems.")
        if not self.payments:
            raise ValidationError("La venta debe registrar al menos un pago.")
        if self.status != self.STATUS_COMPLETED:
            raise ValidationError(f"La venta ya no está en estado completada ({self.status}).")

    def emit_completed(self) -> None:
        self.record_event(
            SaleCompleted(
                sale_id=self.id or 0,
                receipt_number=self.receipt_number,
                total=self.total,
                method=" / ".join(m.value for m in self.payment_methods) or "-",
            )
        )

    def void(self, reason: str = "") -> None:
        if self.status == self.STATUS_VOID:
            raise SaleAlreadyVoidedError(f"La venta {self.receipt_number} ya está anulada.")
        if self.status == self.STATUS_RETURNED:
            raise ValidationError(f"La venta {self.receipt_number} ya fue devuelta por completo.")
        self.status = self.STATUS_VOID
        self.voided_at = datetime.now()
        self.void_reason = (reason or "").strip()
        self.record_event(SaleVoided(self.id or 0, self.receipt_number, reason))

    def refund_item(self, index: int, quantity: int, reason: str = "") -> SaleItem:
        """Devuelve parcialmente un renglón: marca cantidad a devolver sin tocar pagos.

        La restitución de stock y el egreso de caja los orquesta el handler. Si todos
        los renglones quedan devueltos, la venta pasa al estado ``DEVUELTA``.
        """
        if self.status != self.STATUS_COMPLETED:
            raise SaleItemRefundError(f"La venta {self.receipt_number} no está en estado devolvible ({self.status}).")
        if index < 0 or index >= len(self.items):
            raise SaleItemRefundError("El renglón indicado no existe en la venta.")
        quantity = _require_positive_quantity(quantity)
        line = self.items[index]
        if quantity > line.remaining_qty:
            raise SaleItemRefundError(
                f"Solo quedan {line.remaining_qty} unidad(es) para devolver de {line.product_name}."
            )
        line.refunded_qty += quantity
        self.record_event(
            SaleItemRefunded(
                sale_id=self.id or 0,
                receipt_number=self.receipt_number,
                item_index=index,
                product_id=line.product_id,
                product_name=line.product_name,
                quantity=quantity,
                refund_amount=line.unit_price * quantity,
                reason=(reason or "").strip(),
            )
        )
        if sum(i.remaining_qty for i in self.items) == 0:
            self.status = self.STATUS_RETURNED
        return line


@dataclass(slots=True)
class StockMovement:
    """Movimiento de stock con motivos para auditoría."""

    product_id: int
    delta: int
    reason: str
    note: str = ""
    document: str = ""
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)

    REASON_SALE = "VENTA"
    REASON_PURCHASE = "COMPRA"
    REASON_ADJUSTMENT = "AJUSTE"
    REASON_VOID = "ANULACION"
    REASON_APARTADO = "APARTADO"
    REASON_APARTADO_CANCEL = "CANCEL_APARTADO"
    REASON_REFUND = "DEVOLUCION"

    def __post_init__(self) -> None:
        if self.delta == 0:
            raise ValidationError("Un movimiento de stock no puede ser nulo.")
        if not (self.reason or "").strip():
            self.reason = self.REASON_ADJUSTMENT


@dataclass(slots=True)
class ApartadoItem:
    """Ítem de un apartado (producto retenido con su precio de aquel momento)."""

    product_id: int
    product_name: str
    quantity: int
    unit_price: Money
    price_overridden: bool = False

    @property
    def subtotal(self) -> Money:
        return self.unit_price * self.quantity


@dataclass(slots=True)
class ApartadoAbono:
    """Pago parcial (abono) aplicado sobre un apartado."""

    method: PaymentMethod
    amount: Money
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass(slots=True)
class Apartado(AggregateEvents):
    """Agregado Apartado: productos reservados para un cliente con abonos a cuenta.

    Al crearlo se descuenta stock (los productos quedan reservados). Los abonos
    reducen el saldo; cuando llega a cero el apartado se liquida. Si se cancela,
    el stock de los ítems se restituye.
    """

    client_name: str
    client_phone: str
    status: str = "ACTIVO"
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)
    items: list[ApartadoItem] = field(default_factory=list)
    abonos: list[ApartadoAbono] = field(default_factory=list)
    note: str = ""

    STATUS_ACTIVE = "ACTIVO"
    STATUS_SETTLED = "LIQUIDADO"
    STATUS_CANCELLED = "CANCELADO"

    def __post_init__(self) -> None:
        AggregateEvents.__init__(self)
        self.client_name = (self.client_name or "").strip()
        self.client_phone = (self.client_phone or "").strip()
        if not self.client_name:
            raise ValidationError("El nombre del cliente es obligatorio para el apartado.")

    def add_item(self, product: Product, quantity: int, *, unit_price: Money | None = None) -> None:
        """Agrega un ítem, valida stock y reserva existencias.

        ``unit_price`` fija el precio de la partida distinto al de catálogo. El
        apartado congela el precio al crearse (ver ``ApartadoItem``), así que el
        ajuste inline solo aplica en el momento del alta.
        """
        quantity = _require_positive_quantity(quantity)
        if not product.active:
            raise ValidationError(f"El producto {product.code} ({product.name}) está desactivado.")
        product_id = product.id or 0
        price = _resolve_line_price(product.unit_price, unit_price)
        existing = next((i for i in self.items if i.product_id == product_id), None)
        if existing:
            if unit_price is not None and existing.unit_price != price:
                raise ValidationError(
                    f"{product.name} ya está en el apartado a {existing.unit_price.format()}. "
                    "Quita la línea antes de apartarla a otro precio."
                )
            _require_positive_quantity(existing.quantity + quantity)
        product.adjust_stock(-quantity, reason="APARTADO")
        if existing:
            existing.quantity += quantity
        else:
            self.items.append(
                ApartadoItem(
                    product_id=product_id,
                    product_name=product.name,
                    quantity=quantity,
                    unit_price=price,
                    price_overridden=unit_price is not None and price != product.unit_price,
                )
            )

    @property
    def total(self) -> Money:
        return sum((i.subtotal for i in self.items), Money.zero())

    def amount_paid(self) -> Money:
        return sum((a.amount for a in self.abonos), Money.zero())

    @property
    def balance(self) -> Money:
        return max(self.total - self.amount_paid(), Money.zero())

    @property
    def is_fully_paid(self) -> bool:
        return self.balance == Money.zero()

    def add_abono(self, method: PaymentMethod, amount: Money) -> None:
        if self.status != self.STATUS_ACTIVE:
            raise ValidationError("Solo se pueden registrar abonos en un apartado activo.")
        if amount.amount <= 0:
            raise ValidationError("El abono debe ser mayor a cero.")
        if amount > self.balance:
            raise ValidationError(f"El abono no puede superar el saldo de {self.balance.format()}.")
        self.abonos.append(ApartadoAbono(method=method, amount=amount))
        self.record_event(
            ApartadoAbonado(
                apartado_id=self.id or 0,
                method=method.value,
                amount=amount,
                balance=self.balance,
            )
        )
        if self.is_fully_paid:
            self.settle()

    def settle(self) -> None:
        if self.status != self.STATUS_ACTIVE:
            return
        self.status = self.STATUS_SETTLED
        self.record_event(ApartadoSettled(apartado_id=self.id or 0, client_name=self.client_name))

    def cancel(self, reason: str = "") -> None:
        if self.status != self.STATUS_ACTIVE:
            raise ValidationError("Solo se puede cancelar un apartado activo.")
        self.status = self.STATUS_CANCELLED
        self.record_event(
            ApartadoCancelled(apartado_id=self.id or 0, client_name=self.client_name, reason=reason)
        )


@dataclass(slots=True)
class Vale(AggregateEvents):
    """Vale de compra: crédito del cliente redimible en el negocio.

    Se entrega al cliente cuando paga con tarjeta, porque en ese medio no se
    acepta devolución directa. El monto lo define el cajero en el momento del
    cobro. La redención es **parcial**: si el vale vale más que la compra, el
    sobrante queda disponible para la próxima.

    No tiene vencimiento y sólo sirve en la sucursal que lo emitió, de modo que
    la sucursal viaja en el agregado y el saldo nunca se mezcla entre cajas.
    """

    code: str
    amount: Money
    branch_id: str
    #: ``None`` significa "recién emitido": el saldo arranca en el monto total.
    #: Cero explícito es un saldo real (vale consumido) y debe respetarse.
    balance: Money | None = None
    status: str = "ACTIVO"
    sale_id: int | None = None
    receipt_number: str = ""
    issued_by: str = ""
    note: str = ""
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)
    voided_at: datetime | None = None

    STATUS_ACTIVE = "ACTIVO"
    STATUS_VOID = "ANULADO"
    STATUS_USED_UP = "AGOTADO"

    def __post_init__(self) -> None:
        AggregateEvents.__init__(self)
        if self.balance is None:
            # Al emitir, el saldo inicial es el propio monto del vale.
            self.balance = self.amount
        self._refresh_status()

    def _refresh_status(self) -> None:
        """El estado derivado del saldo, salvo que esté anulado."""
        if self.status == self.STATUS_VOID:
            return
        self.status = self.STATUS_USED_UP if self.balance <= Money.zero() else self.STATUS_ACTIVE

    def is_usable(self) -> bool:
        return self.status == self.STATUS_ACTIVE and self.balance > Money.zero()

    def redeem(self, amount: Money, sale_id: int = 0, receipt_number: str = "") -> Money:
        """Consume saldo del vale y devuelve lo efectivamente aplicado.

        Exige que el saldo cubra el importe: como la redención es parcial, nunca
        queda saldo negativo. Devolver el sobrante es una decisión del cajero al
        cobrar, no del vale.
        """
        if self.status == self.STATUS_VOID:
            raise ValeAlreadyVoidedError(f"El vale {self.code} está anulado.")
        if amount <= Money.zero():
            raise ValidationError("El importe a aplicar del vale debe ser mayor a cero.")
        if amount > self.balance:
            raise InsufficientValeBalanceError(
                f"El vale {self.code} tiene {self.balance.format()} disponibles; "
                f"se pidieron {amount.format()}."
            )
        self.balance = self.balance - amount
        self._refresh_status()
        self.record_event(
            ValeRedeemed(
                vale_id=self.id or 0,
                code=self.code,
                amount=amount,
                balance=self.balance,
                sale_id=sale_id,
                receipt_number=receipt_number,
            )
        )
        return amount

    def void(self, reason: str = "") -> None:
        """Anula el vale: deja de ser redimible y su saldo vuelve a cero.

        Se usa cuando la venta que lo originó se anula, para que el cliente no
        conserve un crédito de una compra que ya no existe.

        Un vale ya agotado no se anula: su saldo se gastó en compras reales y
        no hay nada que devolver. Queda ``AGOTADO`` como registro histórico.
        """
        if self.status == self.STATUS_VOID:
            raise ValeAlreadyVoidedError(f"El vale {self.code} ya está anulado.")
        if self.status == self.STATUS_USED_UP:
            return
        self.status = self.STATUS_VOID
        self.balance = Money.zero()
        self.voided_at = datetime.now()
        self.record_event(ValeVoided(self.id or 0, self.code, (reason or "").strip()))

    def record_issued(self) -> None:
        self.record_event(
            ValeIssued(
                vale_id=self.id or 0,
                code=self.code,
                amount=self.amount,
                sale_id=self.sale_id or 0,
                receipt_number=self.receipt_number,
            )
        )


@dataclass(slots=True)
class CashMovement(AggregateEvents):
    """Movimiento de efectivo de caja (ingreso o egreso no asociado a una venta).

    Permite registrar pagos a proveedores, retiros, vueltos de caja chica o
    devoluciones que salen por la caja. Cada movimiento queda fechado para el corte.
    """

    movement_type: str
    amount: Money
    reason: str = ""
    note: str = ""
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)

    TYPE_IN = "ENTRADA"
    TYPE_OUT = "SALIDA"
    REASON_INGRESO = "INGRESO"
    REASON_RETIRO = "RETIRO"
    REASON_DEVOLUCION = "DEVOLUCION"
    REASON_GASTO = "GASTO"

    def __post_init__(self) -> None:
        AggregateEvents.__init__(self)
        self.movement_type = (self.movement_type or "").strip().upper()
        if self.movement_type not in (self.TYPE_IN, self.TYPE_OUT):
            raise ValidationError(f"Tipo de movimiento de caja inválido: {self.movement_type!r}.")
        if self.amount.amount <= 0:
            raise ValidationError("El monto del movimiento de caja debe ser mayor a cero.")
        self.record_event(
            CashMovementRegistered(
                movement_type=self.movement_type,
                amount=self.amount,
                reason=(self.reason or "").strip(),
                note=(self.note or "").strip(),
            )
        )


@dataclass(slots=True)
class CashDay(AggregateEvents):
    """Jornada de caja: apertura con fondo inicial y cierre con arqueo (corte).

    El corte compara el efectivo esperado (fondo + ventas en efectivo + ingresos
    − egresos del período) contra el efectivo contado por el cajero.
    """

    opening_cash: Money
    opened_by: str = ""
    note: str = ""
    id: int | None = None
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: datetime | None = None
    closing_cash: Money | None = None
    expected_cash: Money | None = None
    difference: Money | None = None
    status: str = "ABIERTA"

    STATUS_OPEN = "ABIERTA"
    STATUS_CLOSED = "CERRADA"

    def __post_init__(self) -> None:
        AggregateEvents.__init__(self)
        self.opened_by = (self.opened_by or "").strip()
        self.note = (self.note or "").strip()
        self.record_event(
            CashDayOpened(day_id=self.id or 0, opening_cash=self.opening_cash, opened_by=self.opened_by)
        )

    def is_open(self) -> bool:
        return self.status == self.STATUS_OPEN

    @property
    def difference_kind(self) -> str:
        """Soberante / faltante / exacto según el arqueo realizado."""
        if self.closing_cash is None or self.expected_cash is None:
            return ""
        delta = self.closing_cash.amount - self.expected_cash.amount
        if delta > 0:
            return "SOBRANTE"
        if delta < 0:
            return "FALTANTE"
        return "EXACTO"

    def close(self, closing_cash: Money, expected_cash: Money, note: str = "") -> None:
        """Cierra la jornada: fija el arqueo, el esperado y la diferencia (por magnitud)."""
        if not self.is_open():
            raise CashDayAlreadyClosedError("La jornada de caja ya está cerrada.")
        self.closing_cash = closing_cash
        self.expected_cash = expected_cash
        delta = closing_cash.amount - expected_cash.amount
        self.difference = Money.from_input(abs(delta))
        self.closed_at = datetime.now()
        self.note = (note or "").strip()
        self.status = self.STATUS_CLOSED
        self.record_event(
            CashDayClosed(
                day_id=self.id or 0,
                closing_cash=closing_cash,
                expected_cash=expected_cash,
                difference=self.difference,
                difference_kind=self.difference_kind,
            )
        )


@dataclass(slots=True)
class Provider:
    """Proveedor para cotizar pedidos.

    Es un área independiente: no altera el inventario ni el catálogo de productos.
    """

    id: int | None
    name: str
    phone: str = ""
    note: str = ""
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        self.name = (self.name or "").strip()
        self.phone = (self.phone or "").strip()
        self.note = (self.note or "").strip()
        if not self.name:
            raise ValidationError("El nombre del proveedor es obligatorio.")

    def update(self, *, name: str | None = None, phone: str | None = None, note: str | None = None) -> None:
        if name is not None:
            self.name = (name or "").strip()
        if phone is not None:
            self.phone = (phone or "").strip()
        if note is not None:
            self.note = (note or "").strip()
        if not self.name:
            raise ValidationError("El nombre del proveedor es obligatorio.")


@dataclass(slots=True)
class ProviderItem:
    """Artículo de la lista de precios de un proveedor (solo para cotizar).

    ``product_id`` enlaza el artículo con el producto del inventario cuando la
    recepción de un pedido lo relaciona; así el próximo pedido coincide solo.
    """

    code: str
    description: str
    price: Money
    provider_id: int | None = None
    product_id: int | None = None
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        self.code = (self.code or "").strip()
        self.description = (self.description or "").strip()
        if not self.code:
            raise ValidationError("El código del artículo es obligatorio.")
        if not self.description:
            raise ValidationError("La descripción del artículo es obligatoria.")
        if self.price.amount <= 0:
            raise ValidationError(f"El precio del artículo {self.code} debe ser mayor a cero.")


@dataclass(slots=True)
class PurchaseOrderLine:
    """Renglón de un pedido guardado (los precios quedan congelados al guardarlo).

    ``provider_item_id`` conserva el vínculo con la lista del proveedor para que
    la recepción relacione el artículo con el producto del inventario.
    """

    code: str
    description: str
    provider_name: str
    quantity: int
    unit_price: Money
    provider_item_id: int | None = None
    product_id: int | None = None
    id: int | None = None

    @property
    def subtotal(self) -> Money:
        return self.unit_price * self.quantity


@dataclass(slots=True)
class PurchaseOrder(AggregateEvents):
    """Agregado Pedido: encabezado persistido con sus renglones.

    Un pedido se guarda (``PENDIENTE``) para recibirse el mismo día o en otra
    fecha; al recibirse pasa a ``RECIBIDO``. También puede cancelarse.
    """

    id: int | None = None
    order_number: str = ""
    status: str = "PENDIENTE"
    note: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    lines: list[PurchaseOrderLine] = field(default_factory=list)

    STATUS_PENDING = "PENDIENTE"
    STATUS_RECEIVED = "RECIBIDO"
    STATUS_CANCELLED = "CANCELADO"

    def __post_init__(self) -> None:
        AggregateEvents.__init__(self)
        self.note = (self.note or "").strip()

    def add_line(self, line: PurchaseOrderLine) -> None:
        if line.quantity <= 0:
            raise ValidationError("La cantidad del pedido debe ser mayor a cero.")
        if line.unit_price.amount < 0:
            raise ValidationError("El precio del pedido no puede ser negativo.")
        self.lines.append(line)

    @property
    def total(self) -> Money:
        return sum((line.subtotal for line in self.lines), Money.zero())

    @property
    def item_count(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def provider_names(self) -> list[str]:
        return sorted({l.provider_name for l in self.lines if l.provider_name})

    def mark_received(self) -> None:
        if self.status != self.STATUS_PENDING:
            raise ValidationError(f"El pedido {self.order_number} no está pendiente ({self.status}).")
        self.status = self.STATUS_RECEIVED

    def cancel(self) -> None:
        if self.status != self.STATUS_PENDING:
            raise ValidationError(f"El pedido {self.order_number} no puede cancelarse ({self.status}).")
        self.status = self.STATUS_CANCELLED