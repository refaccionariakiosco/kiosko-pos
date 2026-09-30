"""Handlers de comandos y consultas. Orquestan casos de uso contra la UoW."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from app.application.ports import UowFactory
from app.application.read_models import (
    AbonoDTO,
    AdjustStockResult,
    ApartadoDTO,
    ApartadoItemDTO,
    CashDayDTO,
    CashMovementDTO,
    CategoryDTO,
    CompleteSaleResult,
    CorteDTO,
    DashboardDTO,
    PaymentDTO,
    ProductDTO,
    ProviderDTO,
    ProviderItemDTO,
    PurchaseLineDTO,
    PurchaseOrderDTO,
    PurchaseOrderLineDTO,
    SaleDTO,
    SaleItemDTO,
    StockMovementDTO,
    TopProductDTO,
    ValeDTO,
)
from app.domain.entities import (
    Apartado,
    ApartadoAbono,
    ApartadoItem,
    CashDay,
    CashMovement,
    Category,
    Payment,
    PaymentMethod,
    Product,
    Provider,
    ProviderItem,
    PurchaseOrder,
    PurchaseOrderLine,
    Sale,
    StockMovement,
    Vale,
)
from app.domain.exceptions import (
    ApartadoNotFoundError,
    CashDayAlreadyOpenError,
    DomainError,
    DuplicateProductCodeError,
    InsufficientValeBalanceError,
    NoOpenCashDayError,
    ProductNotFoundError,
    ProviderNotFoundError,
    SaleNotFoundError,
    ValeAlreadyVoidedError,
    ValeNotFoundError,
    ValidationError,
)
from app.domain.value_objects import Money
from app.infrastructure.topology import DEFAULT_BRANCH_ID
from app.application.commands import (
    AddAbonoCommand,
    AdjustStockCommand,
    ApartadoItemRequest,
    CancelApartadoCommand,
    CloseCashDayCommand,
    CompleteSaleCommand,
    CreateApartadoCommand,
    CreateCategoryCommand,
    CreateProductCommand,
    CreateProviderCommand,
    DeleteProviderCommand,
    DeletePurchaseOrderCommand,
    ImportProviderItemsCommand,
    LoadPurchaseOrderCommand,
    OpenCashDayCommand,
    ProviderItemRequest,
    PurchaseOrderLineRequest,
    ReceivePurchaseOrderCommand,
    RefundSaleItemCommand,
    RegisterCashMovementCommand,
    SaleItemRequest,
    SalePaymentRequest,
    SavePurchaseOrderCommand,
    SaveSettingsCommand,
    SetProductActiveCommand,
    UpdateProductCommand,
    UpdateProviderCommand,
    VoidSaleCommand,
    VoidValeCommand,
)
from app.application.queries import (
    GetApartadoQuery,
    GetApartadosQuery,
    GetCashMovementsQuery,
    GetCategoriesQuery,
    GetCatalogQuery,
    GetCorteQuery,
    GetDashboardQuery,
    GetLastCashDayQuery,
    GetOpenCashDayQuery,
    GetProductQuery,
    GetSaleQuery,
    GetSalesHistoryQuery,
    GetStockMovementsQuery,
    GetValeQuery,
    ListProviderItemsQuery,
    ListProvidersQuery,
    ListPurchaseOrdersQuery,
    ListValesQuery,
)
from app.domain.events import ApartadoCreated

# --------------------------------------------------------------------------- #
# Helpers de caja
# --------------------------------------------------------------------------- #


def _cash_movement_dto(movement: CashMovement) -> CashMovementDTO:
    return CashMovementDTO(
        id=movement.id or 0,
        movement_type=movement.movement_type,
        amount=movement.amount,
        reason=movement.reason,
        note=movement.note,
        created_at=movement.created_at,
    )


def _cash_day_dto(day: CashDay) -> CashDayDTO:
    return CashDayDTO(
        id=day.id or 0,
        opened_at=day.opened_at,
        opening_cash=day.opening_cash,
        opened_by=day.opened_by,
        note=day.note,
        status=day.status,
        closed_at=day.closed_at,
        closing_cash=day.closing_cash,
        expected_cash=day.expected_cash,
        difference=day.difference,
        difference_kind=day.difference_kind,
    )


def _corte_dto(
    uow,
    day: CashDay,
    *,
    end: datetime,
) -> CorteDTO:
    """Consolida el corte de una jornada: ventas, movimientos y efectivo esperado."""
    sales = uow.sales.list_sales(start=day.opened_at, end=end, status=Sale.STATUS_COMPLETED)
    by_method: dict[str, Money] = {}
    credit = Money.zero()
    for sale in sales:
        for payment in sale.payments:
            by_method[payment.method.value] = by_method.get(payment.method.value, Money.zero()) + payment.amount
            if payment.method == PaymentMethod.CREDIT:
                credit = credit + payment.amount
    movements = uow.cash_movements.list(start=day.opened_at, end=end)
    cash_in = Money.zero()
    cash_out = Money.zero()
    refunds = Money.zero()
    for movement in movements:
        if movement.movement_type == CashMovement.TYPE_IN:
            cash_in = cash_in + movement.amount
        else:
            cash_out = cash_out + movement.amount
            if movement.reason == CashMovement.REASON_DEVOLUCION:
                refunds = refunds + movement.amount

    cash_expected = (
        day.opening_cash
        + by_method.get(PaymentMethod.CASH.value, Money.zero())
        + cash_in
        - cash_out
    )
    sales_total = sum((s.total for s in sales), Money.zero())
    return CorteDTO(
        day=_cash_day_dto(day),
        sales_count=len(sales),
        sales_total=sales_total,
        by_method=by_method,
        credit_total=credit,
        cash_in_total=cash_in,
        cash_out_total=cash_out,
        refunds_total=refunds,
        expected_cash=cash_expected,
    )

# --------------------------------------------------------------------------- #
# Mapeadores entity -> DTO
# --------------------------------------------------------------------------- #


def _category_dto(category: Category) -> CategoryDTO:
    return CategoryDTO(id=category.id or 0, name=category.name, description=category.description)


def _product_dto(product: Product, category_names: dict[int, str] | None = None) -> ProductDTO:
    return ProductDTO(
        id=product.id or 0,
        code=product.code,
        name=product.name,
        unit_price=product.unit_price,
        stock=product.stock,
        min_stock=product.min_stock,
        active=product.active,
        category_id=product.category_id,
        category_name=(category_names or {}).get(product.category_id or 0, ""),
        description=product.description,
        cost=product.cost,
        wholesale_price=product.wholesale_price,
    )


def _current_username(uow) -> str:
    """Cajero con la sesión abierta, para dejar constancia de quién emitió el vale."""
    from sqlalchemy import text

    row = uow.session.execute(
        text("SELECT value FROM sys_config WHERE key = 'login_username'")
    ).scalar_one_or_none()
    return (row or "").strip()


def _vale_dto(vale: Vale) -> ValeDTO:
    return ValeDTO(
        id=vale.id or 0,
        code=vale.code,
        amount=vale.amount,
        balance=vale.balance,
        status=vale.status,
        branch_id=vale.branch_id,
        receipt_number=vale.receipt_number,
        issued_by=vale.issued_by,
        note=vale.note,
        created_at=vale.created_at,
    )


def _sale_dto(sale: Sale) -> SaleDTO:
    return SaleDTO(
        id=sale.id or 0,
        receipt_number=sale.receipt_number,
        created_at=sale.created_at,
        status=sale.status,
        items=[
            SaleItemDTO(
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                subtotal=item.subtotal,
                refunded_qty=item.refunded_qty,
                price_overridden=item.price_overridden,
            )
            for item in sale.items
        ],
        payments=[PaymentDTO(method=p.method, amount=p.amount) for p in sale.payments],
        subtotal=sale.subtotal,
        total=sale.total,
        discount=sale.discount,
        tendered=sale.tendered,
        change_amount=sale.change(),
        void_reason=sale.void_reason,
    )


def _require_product(product: Product | None, code: str = "") -> Product:
    if product is None:
        raise ProductNotFoundError(f"El producto {code or '(sin código)'} no existe.")
    return product


def _require_sale(sale: Sale | None, sale_id: int) -> Sale:
    if sale is None:
        raise SaleNotFoundError(f"No existe la venta #{sale_id}.")
    return sale


def _require_apartado(apartado: Apartado | None, apartado_id: int) -> Apartado:
    if apartado is None:
        raise ApartadoNotFoundError(f"No existe el apartado #{apartado_id}.")
    return apartado


def _apartado_dto(apartado: Apartado) -> ApartadoDTO:
    return ApartadoDTO(
        id=apartado.id or 0,
        client_name=apartado.client_name,
        client_phone=apartado.client_phone,
        created_at=apartado.created_at,
        status=apartado.status,
        items=[
            ApartadoItemDTO(
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                subtotal=item.subtotal,
                price_overridden=item.price_overridden,
            )
            for item in apartado.items
        ],
        abonos=[
            AbonoDTO(
                id=abono.id or 0,
                method=abono.method,
                amount=abono.amount,
                created_at=abono.created_at,
            )
            for abono in apartado.abonos
        ],
        total=apartado.total,
        amount_paid=apartado.amount_paid(),
        balance=apartado.balance,
        note=apartado.note,
    )


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #


class CreateCategoryHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: CreateCategoryCommand) -> CategoryDTO:
        with self._uow() as uow:
            category = Category(id=None, name=command.name, description=command.description)
            category = uow.categories.add(category)
            uow.commit()
            return _category_dto(category)


class CreateProductHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: CreateProductCommand) -> ProductDTO:
        with self._uow() as uow:
            existing = uow.products.get_by_code(command.code)
            if existing is not None:
                raise DuplicateProductCodeError(f"Ya existe un producto con el código {command.code}.")
            product = Product(
                code=command.code,
                name=command.name,
                unit_price=Money.from_input(command.unit_price),
                cost=Money.from_input(command.cost) if command.cost is not None else None,
                wholesale_price=Money.from_input(command.wholesale_price)
                if command.wholesale_price is not None
                else None,
                stock=int(command.stock),
                min_stock=int(command.min_stock),
                category_id=command.category_id,
                description=command.description,
            )
            if product.category_id is not None and uow.categories.get(product.category_id) is None:
                raise ValidationError(f"No existe la categoría {product.category_id}.")
            product = uow.products.add(product)
            uow.track(product)
            uow.commit()
            return _product_dto(product, _category_names(uow))


def _category_names(uow) -> dict[int, str] | None:
    return {c.id or 0: c.name for c in uow.categories.list_all()}


class UpdateProductHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: UpdateProductCommand) -> ProductDTO:
        with self._uow() as uow:
            product = uow.products.get(command.product_id)
            product = _require_product(product)
            if command.code and command.code.upper() != product.code:
                clash = uow.products.get_by_code(command.code)
                if clash is not None and clash.id != product.id:
                    raise DuplicateProductCodeError(f"Ya existe un producto con el código {command.code}.")
            product.update(
                code=command.code,
                name=command.name,
                unit_price=Money.from_input(command.unit_price),
                cost=Money.from_input(command.cost) if command.cost is not None else product.cost,
                wholesale_price=Money.from_input(command.wholesale_price)
                if command.wholesale_price is not None
                else product.wholesale_price,
                category_id=command.category_id,
                min_stock=int(command.min_stock),
                description=command.description,
            )
            if command.category_id is None:
                product.category_id = None
            uow.products.update(product)
            uow.track(product)
            uow.commit()
            return _product_dto(product, _category_names(uow))


class SetProductActiveHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: SetProductActiveCommand) -> ProductDTO:
        with self._uow() as uow:
            product = _require_product(uow.products.get(command.product_id))
            if command.active:
                product.activate()
            else:
                product.deactivate()
            uow.products.set_active(product.id or 0, product.active)
            uow.track(product)
            uow.commit()
            return _product_dto(product, _category_names(uow))


class AdjustStockHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: AdjustStockCommand) -> AdjustStockResult:
        with self._uow() as uow:
            product = _require_product(uow.products.get(command.product_id))
            product.adjust_stock(int(command.delta), reason=command.reason or "AJUSTE")
            uow.products.update_stock(product.id or 0, product.stock)
            uow.movements.add(
                StockMovement(
                    product_id=product.id or 0,
                    delta=int(command.delta),
                    reason=command.reason or "AJUSTE",
                    note=command.note,
                )
            )
            uow.track(product)
            uow.commit()
            return AdjustStockResult(product_id=product.id or 0, stock=product.stock, delta=command.delta)


class CompleteSaleHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: CompleteSaleCommand) -> CompleteSaleResult:
        if not command.items:
            raise ValidationError("La venta no tiene productos.")
        with self._uow() as uow:
            receipt = uow.sales.next_receipt_number()
            tendered = Money.from_input(command.tendered) if command.tendered is not None else None
            sale = Sale(receipt_number=receipt, tendered=tendered)

            products: dict[int, Product] = {}
            for item in command.items:
                product = _require_product(uow.products.get_by_code(item.code), code=item.code)
                line_price = Money.from_input(item.unit_price) if item.unit_price is not None else None
                sale.add_item(product, item.quantity, unit_price=line_price)
                products[product.id or 0] = product
                uow.movements.add(
                    StockMovement(
                        product_id=product.id or 0,
                        delta=-item.quantity,
                        reason=StockMovement.REASON_SALE,
                        note=f"Venta {receipt}",
                        document=receipt,
                    )
                )

            for payment in command.payments:
                # Un vale puede cubrir el total completo: el medio de pago
                # llega en cero y un importe cero no es un pago.
                amount = Money.from_input(payment.amount)
                if amount <= Money.zero():
                    continue
                sale.add_payment(PaymentMethod.from_value(payment.method), amount)

            if command.discount is not None:
                sale.apply_discount(Money.from_input(command.discount))

            # Redención de un vale previo: se aplica sólo lo que alcance del
            # total pendiente y el resto del saldo queda para otra compra.
            redeemed: Vale | None = None
            vale_applied = Money.zero()
            if (command.vale_code or "").strip():
                redeemed, applied = self._redeem_vale(
                    uow, command.vale_code.strip(), sale.total - sale.paid_amount()
                )
                vale_applied = redeemed.redeem(applied)
                uow.vales.update(redeemed)
                uow.vales.record_usage(
                    redeemed,
                    vale_applied,
                    sale_id=sale.id or 0,
                    receipt_number=receipt,
                    branch_id=uow.branch_id or DEFAULT_BRANCH_ID,
                )
                sale.add_payment(PaymentMethod.VALE, vale_applied)

            sale.finalize()
            saved = uow.sales.add(sale)
            sale.id = saved.id
            for product in products.values():
                uow.products.update_stock(product.id or 0, product.stock)

            # El vale se guarda recién con la venta persistida para poder
            # colgarle el id del cobro al que pertenece.
            issued: Vale | None = None
            if command.issue_vale:
                issued = self._issue_vale(
                    uow, command.vale_amount, sale_id=sale.id or 0, receipt_number=receipt
                )

            sale.emit_completed()
            uow.track(sale, *products.values())
            for vale in (redeemed, issued):
                if vale is not None:
                    uow.track(vale)
            uow.commit()
            return CompleteSaleResult(
                sale_id=sale.id or 0,
                receipt_number=sale.receipt_number,
                total=sale.total,
                change_amount=sale.change(),
                sale=_sale_dto(sale),
                issued_vale=_vale_dto(issued) if issued is not None else None,
                vale_applied=vale_applied,
                vale_remaining=redeemed.balance if redeemed is not None else Money.zero(),
            )

    @staticmethod
    def _redeem_vale(uow, code: str, pending: Money) -> tuple[Vale, Money]:
        """Valida el vale para esta sucursal y calcula cuánto se puede aplicar."""
        vale = uow.vales.get_by_code(code)
        if vale is None:
            raise ValeNotFoundError(f"No existe el vale {code!r}.")
        if pending <= Money.zero():
            raise ValidationError("El total ya está cubierto: no hay nada que aplicar con el vale.")
        if vale.status == Vale.STATUS_VOID:
            raise ValeAlreadyVoidedError(f"El vale {vale.code} está anulado.")
        if not vale.is_usable():
            raise InsufficientValeBalanceError(f"El vale {vale.code} no tiene saldo disponible.")
        # El vale no viaja entre sucursales: cada caja redime sólo los suyos.
        if uow.branch_id and vale.branch_id and vale.branch_id != uow.branch_id:
            raise ValidationError(
                f"El vale {vale.code} pertenece a otra sucursal y no se puede usar aquí."
            )
        return vale, min(vale.balance, pending)

    @staticmethod
    def _issue_vale(uow, amount, *, sale_id: int, receipt_number: str) -> Vale:
        """Emite un vale por el importe que definió el cajero en el cobro."""
        if amount is None:
            raise ValidationError("Definí el importe del vale a entregar.")
        value = Money.from_input(amount)
        if value <= Money.zero():
            raise ValidationError("El importe del vale debe ser mayor a cero.")
        vale = Vale(
            code=uow.vales.next_code(),
            amount=value,
            balance=value,
            branch_id=uow.branch_id or DEFAULT_BRANCH_ID,
            sale_id=sale_id or None,
            receipt_number=receipt_number,
            issued_by=_current_username(uow),
            note=f"Emitido en la venta {receipt_number}",
        )
        vale.record_issued()
        return uow.vales.add(vale)


class VoidSaleHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: VoidSaleCommand) -> SaleDTO:
        with self._uow() as uow:
            sale = _require_sale(uow.sales.get(command.sale_id), command.sale_id)
            sale.void(command.reason)
            for item in sale.items:
                product = uow.products.get(item.product_id)
                if product is None:
                    continue
                product.adjust_stock(item.quantity, reason=StockMovement.REASON_VOID)
                uow.products.update_stock(product.id or 0, product.stock)
                uow.movements.add(
                    StockMovement(
                        product_id=product.id or 0,
                        delta=item.quantity,
                        reason=StockMovement.REASON_VOID,
                        note=f"Anulación {sale.receipt_number}",
                        document=sale.receipt_number,
                    )
                )
                uow.track(product)
            # Si esta venta entregó un vale, se anula con ella: el cliente no
            # puede conservar crédito de una compra que ya no existe.
            self._void_emitted_vale(uow, sale)
            uow.sales.update(sale)
            uow.track(sale)
            uow.commit()
            return _sale_dto(sale)

    @staticmethod
    def _void_emitted_vale(uow, sale: Sale) -> None:
        """Anula el vale que emitió esta venta, si todavía tiene saldo.

        Un vale ya consumido o ya anulado se deja como está: su saldo se gastó
        en compras reales y la anulación de la venta no debe fallar por eso.
        """
        for candidate in uow.vales.list():
            if candidate.sale_id != (sale.id or 0):
                continue
            if not candidate.is_usable():
                return
            candidate.void(f"Anulación de la venta {sale.receipt_number}")
            uow.vales.update(candidate)
            uow.track(candidate)
            return


class VoidValeHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: VoidValeCommand) -> ValeDTO:
        with self._uow() as uow:
            vale = uow.vales.get_by_code(command.code) if (command.code or "").strip() else None
            if vale is None:
                vale = uow.vales.get(command.vale_id)
            if vale is None:
                raise ValeNotFoundError("El vale indicado no existe.")
            if vale.status == Vale.STATUS_VOID:
                raise ValeAlreadyVoidedError(f"El vale {vale.code} ya está anulado.")
            vale.void(command.reason)
            uow.vales.update(vale)
            uow.track(vale)
            uow.commit()
            return _vale_dto(vale)


class RefundSaleItemHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: RefundSaleItemCommand) -> SaleDTO:
        with self._uow() as uow:
            sale = _require_sale(uow.sales.get(command.sale_id), command.sale_id)
            line = sale.refund_item(command.item_index, int(command.quantity), command.reason)
            quantity = int(command.quantity)

            product = uow.products.get(line.product_id)
            if product is not None:
                product.adjust_stock(quantity, reason=StockMovement.REASON_REFUND)
                uow.products.update_stock(product.id or 0, product.stock)
                uow.movements.add(
                    StockMovement(
                        product_id=product.id or 0,
                        delta=quantity,
                        reason=StockMovement.REASON_REFUND,
                        note=f"Devolución {sale.receipt_number}: {line.product_name}",
                        document=sale.receipt_number,
                    )
                )
                uow.track(product)

            if command.cash_payout:
                uow.cash_movements.add(
                    CashMovement(
                        movement_type=CashMovement.TYPE_OUT,
                        amount=line.unit_price * quantity,
                        reason=CashMovement.REASON_DEVOLUCION,
                        note=f"Devolución {sale.receipt_number}: {line.product_name}",
                    )
                )

            uow.sales.update(sale)
            uow.track(sale)
            uow.commit()
            return _sale_dto(sale)


class RegisterCashMovementHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: RegisterCashMovementCommand) -> CashMovementDTO:
        with self._uow() as uow:
            movement = CashMovement(
                movement_type=command.movement_type,
                amount=Money.from_input(command.amount),
                reason=command.reason,
                note=command.note,
            )
            saved = uow.cash_movements.add(movement)
            movement.id = saved.id
            uow.track(movement)
            uow.commit()
            return _cash_movement_dto(movement)


class OpenCashDayHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: OpenCashDayCommand) -> CashDayDTO:
        with self._uow() as uow:
            if uow.cash_days.get_open() is not None:
                raise CashDayAlreadyOpenError("Ya hay una jornada de caja abierta.")
            day = CashDay(
                opening_cash=Money.from_input(command.opening_cash),
                opened_by=command.opened_by,
                note=command.note,
            )
            saved = uow.cash_days.add(day)
            day.id = saved.id
            uow.track(day)
            uow.commit()
            return _cash_day_dto(day)


class CloseCashDayHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: CloseCashDayCommand) -> CashDayDTO:
        with self._uow() as uow:
            day = uow.cash_days.get_open()
            if day is None:
                raise NoOpenCashDayError("No hay una jornada de caja abierta.")
            corte = _corte_dto(uow, day, end=datetime.now())
            day.close(Money.from_input(command.counted_cash), corte.expected_cash, command.note)
            uow.cash_days.update(day)
            uow.track(day)
            uow.commit()
            return _cash_day_dto(day)


class CreateApartadoHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: CreateApartadoCommand) -> ApartadoDTO:
        if not command.items:
            raise ValidationError("El apartado debe tener al menos un producto.")
        with self._uow() as uow:
            apartado = Apartado(
                client_name=command.client_name,
                client_phone=command.client_phone,
                note=command.note,
            )
            rows: list[tuple[Product, int]] = []
            for item in command.items:
                product = _require_product(uow.products.get_by_code(item.code), code=item.code)
                apartado.add_item(product, item.quantity)
                rows.append((product, int(item.quantity)))

            if command.initial_abono is not None:
                apartado.add_abono(
                    PaymentMethod.from_value(command.abono_method), Money.from_input(command.initial_abono)
                )

            saved = uow.apartados.add(apartado)
            apartado.id = saved.id
            for product, quantity in rows:
                uow.products.update_stock(product.id or 0, product.stock)
                uow.movements.add(
                    StockMovement(
                        product_id=product.id or 0,
                        delta=-quantity,
                        reason=StockMovement.REASON_APARTADO,
                        note=f"Apartado para {apartado.client_name}",
                        document=f"A-{apartado.id or 0}",
                    )
                )

            apartado.record_event(
                ApartadoCreated(
                    apartado_id=apartado.id or 0,
                    client_name=apartado.client_name,
                    total=apartado.total,
                )
            )
            uow.track(apartado, *(product for product, _ in rows))
            uow.commit()
            return _apartado_dto(apartado)


class AddAbonoHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: AddAbonoCommand) -> ApartadoDTO:
        with self._uow() as uow:
            apartado = _require_apartado(uow.apartados.get(command.apartado_id), command.apartado_id)
            apartado.add_abono(PaymentMethod.from_value(command.method), Money.from_input(command.amount))
            uow.apartados.update(apartado)
            uow.track(apartado)
            uow.commit()
            return _apartado_dto(apartado)


class CancelApartadoHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: CancelApartadoCommand) -> ApartadoDTO:
        with self._uow() as uow:
            apartado = _require_apartado(uow.apartados.get(command.apartado_id), command.apartado_id)
            if apartado.status == Apartado.STATUS_CANCELLED:
                raise ValidationError(f"El apartado de {apartado.client_name} ya fue cancelado.")
            apartado.cancel(command.reason)
            for item in apartado.items:
                product = uow.products.get(item.product_id)
                if product is None:
                    continue
                product.adjust_stock(item.quantity, reason=StockMovement.REASON_APARTADO_CANCEL)
                uow.products.update_stock(product.id or 0, product.stock)
                uow.movements.add(
                    StockMovement(
                        product_id=product.id or 0,
                        delta=item.quantity,
                        reason=StockMovement.REASON_APARTADO_CANCEL,
                        note=f"Cancelación apartado de {apartado.client_name}",
                        document=f"A-{apartado.id or 0}",
                    )
                )
                uow.track(product)
            uow.apartados.update(apartado)
            uow.track(apartado)
            uow.commit()
            return _apartado_dto(apartado)


# --------------------------------------------------------------------------- #
# Consultas
# --------------------------------------------------------------------------- #


class GetCategoriesHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetCategoriesQuery) -> list[CategoryDTO]:
        with self._uow() as uow:
            return [_category_dto(c) for c in uow.categories.list_all()]


class GetCatalogHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetCatalogQuery) -> list[ProductDTO]:
        with self._uow() as uow:
            names = {c.id or 0: c.name for c in uow.categories.list_all()}
            products = uow.products.search(
                search=query.search,
                include_inactive=query.include_inactive,
                category_id=query.category_id,
            )
            return [_product_dto(p, names) for p in products]


class GetProductHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetProductQuery) -> ProductDTO:
        with self._uow() as uow:
            product = _require_product(uow.products.get(query.product_id))
            names = {c.id or 0: c.name for c in uow.categories.list_all()}
            return _product_dto(product, names)


class GetSalesHistoryHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetSalesHistoryQuery) -> list[SaleDTO]:
        with self._uow() as uow:
            sales = uow.sales.list_sales(
                start=query.start,
                end=query.end,
                status=query.status,
                search=query.search or None,
                limit=query.limit,
            )
            return [_sale_dto(s) for s in sales]


class GetSaleHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetSaleQuery) -> SaleDTO:
        with self._uow() as uow:
            sale = _require_sale(uow.sales.get(query.sale_id), query.sale_id)
            return _sale_dto(sale)


class GetStockMovementsHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetStockMovementsQuery) -> list[StockMovementDTO]:
        with self._uow() as uow:
            movements = uow.movements.list_for_product(query.product_id, limit=query.limit)
            return [
                StockMovementDTO(
                    id=m.id or 0,
                    delta=m.delta,
                    reason=m.reason,
                    note=m.note,
                    created_at=m.created_at,
                    document=m.document or "",
                )
                for m in movements
            ]


class GetDashboardHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetDashboardQuery) -> DashboardDTO:
        with self._uow() as uow:
            today = datetime.now()
            day_start = datetime.combine(today.date(), time.min)
            day_end = datetime.combine(today.date(), time.max)

            completed = uow.sales.list_sales(start=day_start, end=day_end, status=Sale.STATUS_COMPLETED)
            today_revenue = sum((s.total for s in completed), Money.zero())
            by_method: dict[str, Money] = {}
            for s in completed:
                for p in s.payments:
                    total = by_method.get(p.method.value, Money.zero()) + p.amount
                    by_method[p.method.value] = total

            products = uow.products.search()
            active = [p for p in products if p.active]
            low = [p for p in active if p.low_stock]
            out = [p for p in active if p.out_of_stock]

            week_start = datetime.combine((datetime.now() - timedelta(days=7)).date(), time.min)
            recent = uow.sales.list_sales(start=week_start, end=day_end, status=Sale.STATUS_COMPLETED)
            top: dict[int, TopProductDTO] = {}
            for s in recent:
                for item in s.items:
                    entry = top.get(item.product_id)
                    if entry is None:
                        entry = TopProductDTO(name=item.product_name, code="", quantity=0, revenue=Money.zero())
                        top[item.product_id] = entry
                    entry.quantity += item.quantity
                    entry.revenue = entry.revenue + item.subtotal
            top_list = sorted(top.values(), key=lambda t: t.revenue, reverse=True)[:5]

            return DashboardDTO(
                today_sales_count=len(completed),
                today_revenue=today_revenue,
                today_revenue_by_method=by_method,
                active_products=len(active),
                low_stock_count=len(low),
                out_of_stock_count=len(out),
                top_products=top_list,
            )


class GetApartadosHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetApartadosQuery) -> list[ApartadoDTO]:
        with self._uow() as uow:
            status = None if query.status in (None, "TODOS") else query.status
            apartados = uow.apartados.list(status=status, search=query.search or None)
            return [_apartado_dto(a) for a in apartados]


class GetApartadoHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetApartadoQuery) -> ApartadoDTO:
        with self._uow() as uow:
            apartado = _require_apartado(uow.apartados.get(query.apartado_id), query.apartado_id)
            return _apartado_dto(apartado)


class GetCashMovementsHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetCashMovementsQuery) -> list[CashMovementDTO]:
        with self._uow() as uow:
            movements = uow.cash_movements.list(start=query.start, end=query.end)
            return [_cash_movement_dto(m) for m in movements]


class GetOpenCashDayHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetOpenCashDayQuery) -> CashDayDTO | None:
        with self._uow() as uow:
            day = uow.cash_days.get_open()
            return _cash_day_dto(day) if day else None


class GetLastCashDayHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetLastCashDayQuery) -> CashDayDTO | None:
        with self._uow() as uow:
            days = uow.cash_days.list_recent(limit=1)
            return _cash_day_dto(days[0]) if days else None


class GetCorteHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetCorteQuery) -> CorteDTO | None:
        with self._uow() as uow:
            if query.day_id is not None:
                day = uow.cash_days.get(query.day_id)
                if day is None:
                    return None
                if day.is_open():
                    return _corte_dto(uow, day, end=datetime.now())
                return _corte_dto(uow, day, end=day.closed_at or datetime.now())
            day = uow.cash_days.get_open()
            if day is None:
                return None
            return _corte_dto(uow, day, end=datetime.now())


# --------------------------------------------------------------------------- #
# Proveedores
# --------------------------------------------------------------------------- #


def _provider_dto(provider: Provider, item_count: int = 0) -> ProviderDTO:
    return ProviderDTO(
        id=provider.id or 0,
        name=provider.name,
        phone=provider.phone,
        note=provider.note,
        created_at=provider.created_at,
        item_count=item_count,
    )


def _provider_item_dto(item: ProviderItem, provider_name: str = "") -> ProviderItemDTO:
    return ProviderItemDTO(
        id=item.id or 0,
        provider_id=item.provider_id or 0,
        code=item.code,
        description=item.description,
        price=item.price,
        provider_name=provider_name,
        product_id=item.product_id,
    )


def _purchase_order_line_dto(line: PurchaseOrderLine) -> PurchaseOrderLineDTO:
    return PurchaseOrderLineDTO(
        id=line.id or 0,
        provider_item_id=line.provider_item_id,
        product_id=line.product_id,
        code=line.code,
        description=line.description,
        provider_name=line.provider_name,
        quantity=line.quantity,
        unit_price=line.unit_price,
    )


def _purchase_order_dto(order: PurchaseOrder) -> PurchaseOrderDTO:
    return PurchaseOrderDTO(
        id=order.id or 0,
        order_number=order.order_number,
        created_at=order.created_at,
        status=order.status,
        note=order.note,
        lines=[_purchase_order_line_dto(line) for line in order.lines],
    )


class CreateProviderHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: CreateProviderCommand) -> ProviderDTO:
        with self._uow() as uow:
            provider = uow.providers.add(
                Provider(id=None, name=command.name, phone=command.phone, note=command.note)
            )
            uow.commit()
            return _provider_dto(provider)


class UpdateProviderHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: UpdateProviderCommand) -> ProviderDTO:
        with self._uow() as uow:
            provider = uow.providers.get(command.provider_id)
            if provider is None:
                raise ProviderNotFoundError(f"No existe el proveedor #{command.provider_id}.")
            provider.update(name=command.name, phone=command.phone, note=command.note)
            uow.providers.update(provider)
            uow.commit()
            return _provider_dto(provider, item_count=uow.providers.count_items(provider.id or 0))


class DeleteProviderHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: DeleteProviderCommand) -> None:
        with self._uow() as uow:
            provider = uow.providers.get(command.provider_id)
            if provider is None:
                raise ProviderNotFoundError(f"No existe el proveedor #{command.provider_id}.")
            uow.providers.delete(command.provider_id)
            uow.commit()


class ImportProviderItemsHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: ImportProviderItemsCommand) -> int:
        items = [
            ProviderItem(
                code=req.code,
                description=req.description,
                price=Money.from_input(req.price),
                provider_id=command.provider_id,
            )
            for req in command.items
        ]
        with self._uow() as uow:
            if uow.providers.get(command.provider_id) is None:
                raise ProviderNotFoundError(f"No existe el proveedor #{command.provider_id}.")
            count = uow.providers.replace_items(command.provider_id, items)
            uow.commit()
            return count


class ListProvidersHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: ListProvidersQuery) -> list[ProviderDTO]:
        with self._uow() as uow:
            providers = uow.providers.list_all()
            return [
                _provider_dto(p, item_count=uow.providers.count_items(p.id or 0))
                for p in providers
            ]


class ListProviderItemsHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: ListProviderItemsQuery) -> list[ProviderItemDTO]:
        with self._uow() as uow:
            if query.provider_id is not None:
                provider = uow.providers.get(query.provider_id)
                name = provider.name if provider else ""
                items = uow.providers.list_items(query.provider_id)
                return [_provider_item_dto(item, name) for item in items]
            names = {p.id or 0: p.name for p in uow.providers.list_all()}
            return [
                _provider_item_dto(item, names.get(item.provider_id or 0, ""))
                for item in uow.providers.list_all_items()
            ]


class ReceivePurchaseOrderHandler:
    """Recibe un pedido y afecta el inventario: suma stock, registra el movimiento
    y deja relacionado el artículo del proveedor con el producto.

    Cada renglón debe indicar un producto del inventario; si además se pasa el
    artículo del proveedor, la relación queda persistida para futuros pedidos.
    """

    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: ReceivePurchaseOrderCommand) -> int:
        if not command.lines:
            raise ValidationError("La recepción de pedido no tiene renglones.")
        with self._uow() as uow:
            order: PurchaseOrder | None = None
            if command.purchase_order_id is not None:
                order = uow.purchase_orders.get(command.purchase_order_id)
                if order is None:
                    raise ValidationError(f"No existe el pedido #{command.purchase_order_id}.")
                if order.status != PurchaseOrder.STATUS_PENDING:
                    raise ValidationError(f"El pedido {order.order_number} ya no está pendiente ({order.status}).")
            folio = order.order_number if order is not None else ""
            total = 0
            for line in command.lines:
                if line.quantity <= 0:
                    raise ValidationError("La cantidad recibida debe ser mayor a cero.")
                product = _require_product(uow.products.get(line.product_id), code="(producto)")
                product.adjust_stock(int(line.quantity), reason=StockMovement.REASON_PURCHASE)
                uow.products.update_stock(product.id or 0, product.stock)
                cost = Money.from_input(line.unit_price) if line.unit_price is not None else None
                if cost is not None and cost > Money.zero() and (product.cost is None or cost != product.cost):
                    product.update(cost=cost)
                    uow.products.update(product)
                uow.movements.add(
                    StockMovement(
                        product_id=product.id or 0,
                        delta=int(line.quantity),
                        reason=StockMovement.REASON_PURCHASE,
                        note=command.note or "Recepción de pedido",
                        document=folio,
                    )
                )
                if line.provider_item_id is not None:
                    uow.providers.relate_item(line.provider_item_id, product.id or 0)
                uow.track(product)
                total += int(line.quantity)
            if command.purchase_order_id is not None:
                order.mark_received()
                uow.purchase_orders.update(order)
            uow.commit()
            return total


class SavePurchaseOrderHandler:
    """Guarda un pedido pendiente con sus renglones para recibirlo después."""

    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: SavePurchaseOrderCommand) -> PurchaseOrderDTO:
        if not command.lines:
            raise ValidationError("El pedido no tiene renglones.")
        order = PurchaseOrder(note=command.note)
        for req in command.lines:
            order.add_line(
                PurchaseOrderLine(
                    provider_item_id=req.provider_item_id,
                    product_id=req.product_id,
                    code=req.code,
                    description=req.description,
                    provider_name=req.provider_name,
                    quantity=int(req.quantity),
                    unit_price=Money.from_input(req.unit_price),
                )
            )
        with self._uow() as uow:
            saved = uow.purchase_orders.add(order)
            order.id = saved.id
            uow.commit()
            return _purchase_order_dto(order)


class DeletePurchaseOrderHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: DeletePurchaseOrderCommand) -> None:
        with self._uow() as uow:
            order = uow.purchase_orders.get(command.purchase_order_id)
            if order is None:
                raise ValidationError(f"No existe el pedido #{command.purchase_order_id}.")
            if order.status != PurchaseOrder.STATUS_PENDING:
                raise ValidationError(f"Solo se pueden eliminar pedidos pendientes ({order.order_number}).")
            uow.purchase_orders.delete(command.purchase_order_id)
            uow.commit()


class LoadPurchaseOrderHandler:
    """Carga un pedido proveniente de PDF/Excel como pedido pendiente nuevo.

    · Actualiza (o agrega) los artículos de la lista del proveedor por código.
    · Crea en el catálogo los productos que aún no existan (stock en 0, sin
      tocar inventario) y los relaciona con el artículo del proveedor.
    · Guarda el pedido en estado PENDIENTE; el stock solo cambia al recibirlo.
    """

    DEFAULT_CATEGORY = "PROVEEDORES"

    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: LoadPurchaseOrderCommand) -> PurchaseOrderDTO:
        if not command.lines:
            raise ValidationError("El pedido importado no tiene renglones.")
        provider_name = (command.provider_name or "").strip()
        if not provider_name:
            raise ValidationError("Debe indicarse el proveedor del pedido.")
        with self._uow() as uow:
            provider = self._ensure_provider(uow, provider_name)
            category = self._ensure_category(uow)

            items = uow.providers.upsert_items(
                provider.id or 0,
                [
                    ProviderItem(
                        code=req.code,
                        description=req.description,
                        price=Money.from_input(req.unit_price),
                        provider_id=provider.id or 0,
                    )
                    for req in command.lines
                ],
            )

            order = PurchaseOrder(note=command.note.strip())
            for req in command.lines:
                product = uow.products.get_by_code(req.code)
                purchase_price = Money.from_input(req.unit_price)
                requested_sale = Money.from_input(req.sale_price) if req.sale_price is not None else None
                sale_price = requested_sale if requested_sale is not None and requested_sale > Money.zero() else None
                if product is None:
                    product = uow.products.add(
                        Product(
                            code=req.code,
                            name=req.description,
                            unit_price=sale_price or purchase_price,
                            cost=purchase_price,
                            stock=0,
                            min_stock=0,
                            category_id=category.id,
                        )
                    )
                    uow.track(product)
                else:
                    product.update(cost=purchase_price, unit_price=sale_price)
                    uow.products.update(product)
                    uow.track(product)
                provider_item = items.get((req.code or "").strip().upper())
                if provider_item is not None and provider_item.product_id != product.id:
                    uow.providers.relate_item(provider_item.id or 0, product.id or 0)
                order.add_line(
                    PurchaseOrderLine(
                        provider_item_id=provider_item.id if provider_item else None,
                        product_id=product.id or 0,
                        code=req.code,
                        description=req.description,
                        provider_name=provider.name,
                        quantity=int(req.quantity),
                        unit_price=purchase_price,
                    )
                )
            saved = uow.purchase_orders.add(order)
            order.id = saved.id
            uow.commit()
            return _purchase_order_dto(order)

    @staticmethod
    def _ensure_provider(uow, name: str) -> Provider:
        for provider in uow.providers.list_all():
            if provider.name.strip().lower() == name.lower():
                return provider
        return uow.providers.add(Provider(id=None, name=name))

    @staticmethod
    def _ensure_category(uow) -> Category:
        for category in uow.categories.list_all():
            if category.name.strip().upper() == LoadPurchaseOrderHandler.DEFAULT_CATEGORY:
                return category
        return uow.categories.add(Category(id=None, name=LoadPurchaseOrderHandler.DEFAULT_CATEGORY))


class ListPurchaseOrdersHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: ListPurchaseOrdersQuery) -> list[PurchaseOrderDTO]:
        with self._uow() as uow:
            status = None if query.status in (None, "TODOS") else query.status
            return [
                _purchase_order_dto(o)
                for o in uow.purchase_orders.list(status=status, provider=query.provider)
            ]


class ListValesHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: ListValesQuery) -> list[ValeDTO]:
        with self._uow() as uow:
            status = None if query.status in (None, "TODOS") else query.status
            return [
                _vale_dto(v)
                for v in uow.vales.list(status=status, term=query.term, limit=query.limit)
            ]


class GetValeHandler:
    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, query: GetValeQuery) -> ValeDTO | None:
        with self._uow() as uow:
            vale = None
            if (query.code or "").strip():
                vale = uow.vales.get_by_code(query.code)
            elif query.vale_id:
                vale = uow.vales.get(query.vale_id)
            return _vale_dto(vale) if vale is not None else None


class SaveSettingsHandler:
    """Persiste los ajustes de la pantalla Configuración en ``sys_config``."""

    def __init__(self, uow_factory: UowFactory):
        self._uow = uow_factory

    def handle(self, command: SaveSettingsCommand) -> None:
        from sqlalchemy import text

        from app.infrastructure.local_config import (
            KEY_BROTHER_PRINTER_IP,
            KEY_CURRENCY,
            KEY_LABEL_DPI,
            KEY_LABEL_HEIGHT_MM,
            KEY_LABEL_PRINTER_KIND,
            KEY_LABEL_WIDTH_MM,
            KEY_LOGIN_PASSWORD,
            KEY_LOGIN_USERNAME,
            KEY_STORE_ADDRESS,
            KEY_STORE_FOOTER,
            KEY_STORE_NAME,
            KEY_STORE_PHONE,
            KEY_TICKET_PRINTER,
        )
        from app.infrastructure.topology import (
            KEY_BRANCH,
            KEY_POCKETBASE_TOKEN,
            KEY_POCKETBASE_URL,
            KEY_TERMINAL_NUM,
        )

        values = {
            KEY_STORE_NAME: command.store_name,
            KEY_STORE_ADDRESS: command.store_address,
            KEY_STORE_PHONE: command.store_phone,
            KEY_STORE_FOOTER: command.store_footer,
            KEY_CURRENCY: command.currency,
            KEY_LOGIN_USERNAME: command.login_username,
            KEY_LOGIN_PASSWORD: command.login_password,
            KEY_LABEL_PRINTER_KIND: command.label_printer_kind,
            KEY_BROTHER_PRINTER_IP: command.brother_printer_ip,
            KEY_LABEL_WIDTH_MM: None if command.label_width_mm is None else str(command.label_width_mm),
            KEY_LABEL_HEIGHT_MM: None if command.label_height_mm is None else str(command.label_height_mm),
            KEY_LABEL_DPI: None if command.label_dpi is None else str(command.label_dpi),
            KEY_TICKET_PRINTER: command.ticket_printer,
            KEY_BRANCH: command.id_sucursal,
            KEY_TERMINAL_NUM: None if command.terminal_num is None else str(command.terminal_num).strip(),
            KEY_POCKETBASE_URL: command.pocketbase_url,
            KEY_POCKETBASE_TOKEN: command.pocketbase_token,
        }
        values = {key: value for key, value in values.items() if value is not None}
        if not values:
            return
        with self._uow() as uow:
            statement = (
                "INSERT INTO sys_config (key, value, updated_at) "
                "VALUES (:k, :v, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at"
            )
            for key, value in values.items():
                uow.session.execute(text(statement), {"k": key, "v": str(value)})
            uow.commit()