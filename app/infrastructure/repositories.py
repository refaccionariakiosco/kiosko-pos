"""Implementación de los repositorios del dominio sobre SQLAlchemy."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

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
    SaleItem,
    StockMovement,
)
from app.domain.exceptions import ProviderNotFoundError, ValidationError
from app.domain.repositories import (
    ApartadoRepository,
    CashDayRepository,
    CashMovementRepository,
    CategoryRepository,
    ProductRepository,
    ProviderRepository,
    PurchaseOrderRepository,
    SaleRepository,
    StockMovementRepository,
)
from app.domain.value_objects import Money
from app.infrastructure.orm import (
    ApartadoAbonoRow,
    ApartadoItemRow,
    ApartadoRow,
    CashDayRow,
    CashMovementRow,
    CategoryRow,
    InventoryRow,
    ProductRow,
    ProviderItemRow,
    ProviderRow,
    PurchaseOrderLineRow,
    PurchaseOrderRow,
    SaleItemRow,
    SalePaymentRow,
    SaleRow,
    StockMovementRow,
)
from app.infrastructure.topology import DEFAULT_BRANCH_ID


def _to_money(value: object | None) -> Money | None:
    return Money.zero() if value is None else Money.from_input(value)


def _category_from_row(row: CategoryRow) -> Category:
    return Category(id=row.id, name=row.name, description=row.description)


def _coerce_stock(value: object, label: str) -> int:
    """Convierte el valor de stock leído de la BD a un entero válido.

    El schema histórico puede traer ``None``, ``float`` (p. ej. 3.0),
    ``Decimal`` o ``str`` en columnas de inventario; el dominio exige ``int``.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValidationError(f"{label} debe ser un entero >= 0.")
    if isinstance(value, Decimal):
        value = int(value)
    elif isinstance(value, float):
        value = int(value)
    elif isinstance(value, str):
        value = (value or "").strip()
        if not value:
            return 0
        try:
            value = int(float(value))
        except ValueError:
            raise ValidationError(f"{label} debe ser un entero >= 0.") from None
    if not isinstance(value, int):
        raise ValidationError(f"{label} debe ser un entero >= 0.")
    # El inventario físico nunca puede ser negativo: datos corruptos/legados
    # que violaron la regla en el pasado no deben tumbar el dashboard.
    if value < 0:
        value = 0
    return value


def _product_from_row(row: ProductRow, inv: InventoryRow | None = None) -> Product:
    """Arma el Product de una fila del catálogo + una fila de inventario.

    El stock SIEMPRE viene de la fila de inventario de la sucursal consultada:
    si no existe ese inventario (catálogo global sin existencias locales), el
    stock es 0. Nunca se lee de la columna legado de products para no filtrar
    existencias de otra sucursal.
    """
    stock = _coerce_stock(inv.stock if inv is not None else 0, "Stock")
    min_stock = _coerce_stock(inv.min_stock if inv is not None else 0, "Stock mínimo")
    return Product(
        id=row.id,
        code=row.code,
        name=row.name,
        unit_price=Money.from_input(row.unit_price),
        cost=_to_money(row.cost),
        wholesale_price=None if row.wholesale_price is None else Money.from_input(row.wholesale_price),
        stock=stock,
        min_stock=min_stock,
        category_id=row.category_id,
        description=row.description,
        active=row.active,
    )


def inventory_map(session: Session, branch_id: str) -> dict[int, InventoryRow]:
    """Mapa producto_id -> fila de inventario para una sucursal (evita N+1)."""
    rows = session.scalars(select(InventoryRow).where(InventoryRow.branch_id == branch_id)).all()
    return {r.product_id: r for r in rows}


def inventory_snapshot(session: Session, branch_id: str) -> dict[int, tuple[int, int]]:
    """Snapshot (stock, min_stock) de una sucursal, usado por el worker de sync."""
    return {pid: (r.stock, r.min_stock) for pid, r in inventory_map(session, branch_id).items()}


def _sale_from_row(row: SaleRow) -> Sale:
    sale = Sale(
        id=row.id,
        receipt_number=row.receipt_number,
        tendered=_to_money(row.tendered),
        status=row.status,
        created_at=row.created_at,
        discount=Money.from_input(row.discount),
        void_reason=row.void_reason,
        voided_at=row.voided_at,
    )
    sale.subtotal = Money.from_input(row.subtotal)
    for item_row in row.items:
        sale.items.append(
            SaleItem(
                product_id=item_row.product_id,
                product_name=item_row.product_name,
                quantity=item_row.quantity,
                unit_price=Money.from_input(item_row.unit_price),
                refunded_qty=item_row.refunded_qty or 0,
            )
        )
    for pay_row in row.payments:
        sale.payments.append(
            Payment(method=PaymentMethod.from_value(pay_row.method), amount=Money.from_input(pay_row.amount))
        )
    return sale


def _apply_product_to_row(row: ProductRow, product: Product) -> None:
    row.code = product.code
    row.name = product.name
    row.description = product.description
    row.category_id = product.category_id
    row.unit_price = product.unit_price.as_decimal()
    row.cost = product.cost.as_decimal() if product.cost else None
    row.wholesale_price = product.wholesale_price.as_decimal() if product.wholesale_price else None
    row.stock = product.stock
    row.min_stock = product.min_stock
    row.active = product.active


class SqlAlchemyCategoryRepository(CategoryRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, category: Category) -> Category:
        row = CategoryRow(name=category.name, description=category.description)
        self._session.add(row)
        self._session.flush()
        category.id = row.id
        return category

    def update(self, category: Category) -> None:
        row = self._session.get(CategoryRow, category.id)
        if row is None:
            return
        row.name = category.name
        row.description = category.description

    def get(self, category_id: int) -> Category | None:
        row = self._session.get(CategoryRow, category_id)
        return _category_from_row(row) if row else None

    def list_all(self) -> list[Category]:
        rows = self._session.scalars(select(CategoryRow).order_by(CategoryRow.name)).all()
        return [_category_from_row(r) for r in rows]


class SqlAlchemyProductRepository(ProductRepository):
    """Catálogo global + inventario por sucursal.

    La clase conoce la ``branch_id`` de la instalación: el stock se lee y escribe
    SIEMPRE en la tabla ``inventory`` (físico local de la sucursal); la tabla
    ``products`` queda como catálogo global. El catálogo de otras sucursales
    nunca se mezcla con el de la propia.
    """

    def __init__(self, session: Session, branch_id: str | None = None):
        self._session = session
        self._branch = (branch_id or "") or DEFAULT_BRANCH_ID

    def _inventory(self, product_id: int) -> InventoryRow:
        """Fila de inventario de la sucursal; la crea con 0 si aún no existe."""
        row = self._session.execute(
            select(InventoryRow).where(
                InventoryRow.product_id == product_id, InventoryRow.branch_id == self._branch
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
        row = InventoryRow(
            product_id=product_id,
            branch_id=self._branch,
            stock=0,
            min_stock=0,
            updated_at=datetime.now(),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def add(self, product: Product) -> Product:
        row = ProductRow()
        _apply_product_to_row(row, product)
        self._session.add(row)
        self._session.flush()
        product.id = row.id
        inv = self._inventory(product.id)
        inv.stock = product.stock
        inv.min_stock = product.min_stock
        inv.updated_at = datetime.now()
        return product

    def update(self, product: Product) -> None:
        row = self._session.get(ProductRow, product.id)
        if row is None:
            return
        _apply_product_to_row(row, product)
        row.updated_at = datetime.now()
        inv = self._inventory(product.id)
        inv.stock = product.stock
        inv.min_stock = product.min_stock
        inv.updated_at = datetime.now()

    def set_active(self, product_id: int, active: bool) -> None:
        self._session.execute(
            update(ProductRow).where(ProductRow.id == product_id).values(active=active, updated_at=datetime.now())
        )

    def update_stock(self, product_id: int, new_stock: int) -> None:
        self._session.execute(
            update(ProductRow)
            .where(ProductRow.id == product_id)
            .values(stock=int(new_stock), updated_at=datetime.now())
        )
        inv = self._inventory(product_id)
        inv.stock = int(new_stock)
        inv.updated_at = datetime.now()

    def get(self, product_id: int) -> Product | None:
        row = self._session.get(ProductRow, product_id)
        if row is None:
            return None
        return _product_from_row(row, self._inventory(product_id))

    def get_by_code(self, code: str) -> Product | None:
        row = self._session.execute(
            select(ProductRow).where(ProductRow.code == (code or "").strip().upper())
        ).scalar_one_or_none()
        if row is None:
            return None
        return _product_from_row(row, self._inventory(row.id))

    def search(
        self, *, search: str = "", include_inactive: bool = False, category_id: int | None = None
    ) -> list[Product]:
        stmt = select(ProductRow)
        term = (search or "").strip()
        if term:
            like = f"%{term.upper()}%"
            stmt = stmt.where(
                (ProductRow.code.ilike(like)) | (ProductRow.name.ilike(f"%{term}%"))
            )
        if not include_inactive:
            stmt = stmt.where(ProductRow.active.is_(True))
        if category_id is not None:
            stmt = stmt.where(ProductRow.category_id == category_id)
        rows = self._session.scalars(stmt.order_by(ProductRow.name)).all()
        by_product = inventory_map(self._session, self._branch)
        return [_product_from_row(r, by_product.get(r.id)) for r in rows]

    def list_extant_codes(self) -> Iterable[str]:
        return self._session.scalars(select(ProductRow.code)).all()


class SqlAlchemySaleRepository(SaleRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, sale: Sale) -> Sale:
        row = SaleRow(
            receipt_number=sale.receipt_number,
            status=sale.status,
            tendered=sale.tendered.as_decimal() if sale.tendered else None,
            subtotal=sale.subtotal.as_decimal(),
            discount=sale.discount.as_decimal(),
            void_reason=sale.void_reason,
            voided_at=sale.voided_at,
        )
        self._session.add(row)
        self._session.flush()
        for item in sale.items:
            self._session.add(
                SaleItemRow(
                    sale_id=row.id,
                    product_id=item.product_id,
                    product_name=item.product_name,
                    quantity=item.quantity,
                    unit_price=item.unit_price.as_decimal(),
                    subtotal=item.subtotal.as_decimal(),
                    refunded_qty=item.refunded_qty,
                )
            )
        for payment in sale.payments:
            self._session.add(
                SalePaymentRow(sale_id=row.id, method=payment.method.value, amount=payment.amount.as_decimal())
            )
        self._session.flush()
        sale.id = row.id
        return sale

    def update(self, sale: Sale) -> None:
        row = self._session.get(SaleRow, sale.id)
        if row is None:
            raise ValueError(f"No existe la venta #{sale.id}.")
        row.status = sale.status
        row.voided_at = sale.voided_at
        row.void_reason = sale.void_reason
        row.discount = sale.discount.as_decimal()
        row.tendered = sale.tendered.as_decimal() if sale.tendered else None
        item_rows = {ir.product_id: ir for ir in row.items}
        for item in sale.items:
            item_row = item_rows.get(item.product_id)
            if item_row is not None:
                item_row.refunded_qty = item.refunded_qty

    def get(self, sale_id: int) -> Sale | None:
        row = self._session.get(SaleRow, sale_id)
        return _sale_from_row(row) if row else None

    def get_by_receipt(self, receipt_number: str) -> Sale | None:
        row = self._session.execute(
            select(SaleRow).where(SaleRow.receipt_number == receipt_number)
        ).scalar_one_or_none()
        return _sale_from_row(row) if row else None

    def list_sales(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[Sale]:
        stmt = select(SaleRow)
        if start is not None:
            stmt = stmt.where(SaleRow.created_at >= start)
        if end is not None:
            stmt = stmt.where(SaleRow.created_at <= end)
        if status:
            stmt = stmt.where(SaleRow.status == status)
        term = (search or "").strip()
        if term:
            like = f"%{term}%"
            stmt = (
                stmt.join(SaleItemRow, SaleItemRow.sale_id == SaleRow.id)
                .where((SaleRow.receipt_number.ilike(like)) | (SaleItemRow.product_name.ilike(like)))
                .distinct()
            )
        stmt = stmt.order_by(SaleRow.created_at.desc(), SaleRow.id.desc())
        if limit:
            stmt = stmt.limit(limit)
        rows = self._session.scalars(stmt).all()
        return [_sale_from_row(r) for r in rows]

    def next_receipt_number(self) -> str:
        """Folio del siguiente recibo con prefijo de caja: ``R-<caja>-<secuencia>``.

        Cada caja numera sus propios tickets (p. ej. ``R-1-000004`` y
        ``R-2-000004``), de modo que los folios nunca colisionan entre
        terminales aunque vendan sin conexión.
        """
        from app.infrastructure.topology import get_terminal_num

        prefix = f"R-{get_terminal_num(self._session)}-"
        numbers = self._session.execute(
            select(SaleRow.receipt_number).where(SaleRow.receipt_number.like(prefix + "%"))
        ).scalars().all()
        seq = 0
        for number in numbers:
            try:
                seq = max(seq, int(number.rsplit("-", 1)[-1]))
            except (ValueError, IndexError):
                continue
        return f"{prefix}{seq + 1:06d}"


class SqlAlchemyStockMovementRepository(StockMovementRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, movement: StockMovement) -> StockMovement:
        row = StockMovementRow(
            product_id=movement.product_id,
            delta=movement.delta,
            reason=movement.reason,
            note=movement.note,
            document=movement.document or "",
        )
        self._session.add(row)
        self._session.flush()
        movement.id = row.id
        return movement

    def list_for_product(self, product_id: int, *, limit: int = 100) -> list[StockMovement]:
        stmt = (
            select(StockMovementRow)
            .where(StockMovementRow.product_id == product_id)
            .order_by(StockMovementRow.created_at.desc(), StockMovementRow.id.desc())
            .limit(limit)
        )
        rows = self._session.scalars(stmt).all()
        return [
            StockMovement(
                id=r.id,
                product_id=r.product_id,
                delta=r.delta,
                reason=r.reason,
                note=r.note,
                document=r.document or "",
                created_at=r.created_at,
            )
            for r in rows
        ]


def _apartado_from_row(row: ApartadoRow) -> Apartado:
    apartado = Apartado(
        id=row.id,
        client_name=row.client_name,
        client_phone=row.client_phone,
        status=row.status,
        created_at=row.created_at,
        note=row.note,
    )
    for item_row in row.items:
        apartado.items.append(
            ApartadoItem(
                product_id=item_row.product_id,
                product_name=item_row.product_name,
                quantity=item_row.quantity,
                unit_price=Money.from_input(item_row.unit_price),
            )
        )
    for abono_row in row.abonos:
        apartado.abonos.append(
            ApartadoAbono(
                id=abono_row.id,
                method=PaymentMethod.from_value(abono_row.method),
                amount=Money.from_input(abono_row.amount),
                created_at=abono_row.created_at,
            )
        )
    return apartado


class SqlAlchemyApartadoRepository(ApartadoRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, apartado: Apartado) -> Apartado:
        row = ApartadoRow(
            client_name=apartado.client_name,
            client_phone=apartado.client_phone,
            status=apartado.status,
            note=apartado.note,
        )
        self._session.add(row)
        self._session.flush()
        for item in apartado.items:
            self._session.add(
                ApartadoItemRow(
                    apartado_id=row.id,
                    product_id=item.product_id,
                    product_name=item.product_name,
                    quantity=item.quantity,
                    unit_price=item.unit_price.as_decimal(),
                    subtotal=item.subtotal.as_decimal(),
                )
            )
        for abono in apartado.abonos:
            self._session.add(
                ApartadoAbonoRow(
                    apartado_id=row.id,
                    method=abono.method.value,
                    amount=abono.amount.as_decimal(),
                    created_at=abono.created_at,
                )
            )
        self._session.flush()
        apartado.id = row.id
        return apartado

    def update(self, apartado: Apartado) -> None:
        row = self._session.get(ApartadoRow, apartado.id)
        if row is None:
            raise ValueError(f"No existe el apartado #{apartado.id}.")
        row.status = apartado.status
        row.note = apartado.note
        for abono in apartado.abonos:
            if abono.id is None:
                self._session.add(
                    ApartadoAbonoRow(
                        apartado_id=row.id,
                        method=abono.method.value,
                        amount=abono.amount.as_decimal(),
                        created_at=abono.created_at,
                    )
                )

    def get(self, apartado_id: int) -> Apartado | None:
        row = self._session.get(ApartadoRow, apartado_id)
        return _apartado_from_row(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[Apartado]:
        stmt = select(ApartadoRow)
        if status:
            stmt = stmt.where(ApartadoRow.status == status)
        term = (search or "").strip()
        if term:
            like = f"%{term}%"
            stmt = stmt.where(
                (ApartadoRow.client_name.ilike(like)) | (ApartadoRow.client_phone.ilike(like))
            )
        stmt = stmt.order_by(ApartadoRow.created_at.desc(), ApartadoRow.id.desc())
        if limit:
            stmt = stmt.limit(limit)
        rows = self._session.scalars(stmt).all()
        return [_apartado_from_row(r) for r in rows]


class SqlAlchemyCashMovementRepository(CashMovementRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, movement: CashMovement) -> CashMovement:
        row = CashMovementRow(
            movement_type=movement.movement_type,
            amount=movement.amount.as_decimal(),
            reason=movement.reason,
            note=movement.note,
        )
        self._session.add(row)
        self._session.flush()
        movement.id = row.id
        return movement

    def list(self, *, start: datetime | None = None, end: datetime | None = None) -> list[CashMovement]:
        stmt = select(CashMovementRow)
        if start is not None:
            stmt = stmt.where(CashMovementRow.created_at >= start)
        if end is not None:
            stmt = stmt.where(CashMovementRow.created_at <= end)
        stmt = stmt.order_by(CashMovementRow.created_at.desc(), CashMovementRow.id.desc())
        rows = self._session.scalars(stmt).all()
        return [
            CashMovement(
                id=r.id,
                movement_type=r.movement_type,
                amount=Money.from_input(r.amount),
                reason=r.reason,
                note=r.note,
                created_at=r.created_at,
            )
            for r in rows
        ]


def _cash_day_from_row(row: CashDayRow) -> CashDay:
    day = CashDay(
        id=row.id,
        opening_cash=Money.from_input(row.opening_cash),
        opened_by=row.opened_by,
        note=row.note,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        closing_cash=_to_money(row.closing_cash),
        expected_cash=_to_money(row.expected_cash),
        difference=_to_money(row.difference),
        status=row.status,
    )
    return day


class SqlAlchemyCashDayRepository(CashDayRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, day: CashDay) -> CashDay:
        row = CashDayRow(
            opened_at=day.opened_at,
            opened_by=day.opened_by,
            opening_cash=day.opening_cash.as_decimal(),
            status=day.status,
            note=day.note,
        )
        self._session.add(row)
        self._session.flush()
        day.id = row.id
        return day

    def update(self, day: CashDay) -> None:
        row = self._session.get(CashDayRow, day.id)
        if row is None:
            raise ValueError(f"No existe la jornada de caja #{day.id}.")
        row.closed_at = day.closed_at
        row.closing_cash = day.closing_cash.as_decimal() if day.closing_cash else None
        row.expected_cash = day.expected_cash.as_decimal() if day.expected_cash else None
        row.difference = day.difference.as_decimal() if day.difference else None
        row.status = day.status
        row.note = day.note

    def get(self, day_id: int) -> CashDay | None:
        row = self._session.get(CashDayRow, day_id)
        return _cash_day_from_row(row) if row else None

    def get_open(self) -> CashDay | None:
        row = self._session.execute(
            select(CashDayRow)
            .where(CashDayRow.status == CashDay.STATUS_OPEN)
            .order_by(CashDayRow.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        return _cash_day_from_row(row) if row else None

    def list_recent(self, *, limit: int = 20) -> list[CashDay]:
        stmt = select(CashDayRow).order_by(CashDayRow.opened_at.desc(), CashDayRow.id.desc()).limit(limit)
        rows = self._session.scalars(stmt).all()
        return [_cash_day_from_row(r) for r in rows]


def _provider_from_row(row: ProviderRow) -> Provider:
    return Provider(id=row.id, name=row.name, phone=row.phone, note=row.note, created_at=row.created_at)


def _provider_item_from_row(row: ProviderItemRow) -> ProviderItem:
    return ProviderItem(
        id=row.id,
        provider_id=row.provider_id,
        code=row.code,
        description=row.description,
        price=Money.from_input(row.price),
        product_id=row.product_id,
        created_at=row.created_at,
    )


class SqlAlchemyProviderRepository(ProviderRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, provider: Provider) -> Provider:
        row = ProviderRow(name=provider.name, phone=provider.phone, note=provider.note)
        self._session.add(row)
        self._session.flush()
        provider.id = row.id
        return provider

    def update(self, provider: Provider) -> None:
        row = self._session.get(ProviderRow, provider.id)
        if row is None:
            return
        row.name = provider.name
        row.phone = provider.phone
        row.note = provider.note

    def delete(self, provider_id: int) -> None:
        row = self._session.get(ProviderRow, provider_id)
        if row is None:
            return
        self._session.delete(row)

    def get(self, provider_id: int) -> Provider | None:
        row = self._session.get(ProviderRow, provider_id)
        return _provider_from_row(row) if row else None

    def list_all(self) -> list[Provider]:
        rows = self._session.scalars(select(ProviderRow).order_by(ProviderRow.name)).all()
        return [_provider_from_row(r) for r in rows]

    def count_items(self, provider_id: int) -> int:
        return self._session.query(ProviderItemRow).filter(ProviderItemRow.provider_id == provider_id).count()

    def replace_items(self, provider_id: int, items: list[ProviderItem]) -> int:
        if self._session.get(ProviderRow, provider_id) is None:
            raise ProviderNotFoundError(f"No existe el proveedor #{provider_id}.")
        self._session.execute(delete(ProviderItemRow).where(ProviderItemRow.provider_id == provider_id))
        for item in items:
            self._session.add(
                ProviderItemRow(
                    provider_id=provider_id,
                    code=item.code,
                    description=item.description,
                    price=item.price.as_decimal(),
                )
            )
        self._session.flush()
        return len(items)

    def relate_item(self, provider_item_id: int, product_id: int | None) -> None:
        item_row = self._session.get(ProviderItemRow, provider_item_id)
        if item_row is None:
            return
        item_row.product_id = product_id

    def upsert_items(
        self, provider_id: int, items: Iterable[ProviderItem]
    ) -> dict[str, ProviderItem]:
        """Agrega o actualiza artículos del proveedor (por código) sin borrar la lista."""
        if self._session.get(ProviderRow, provider_id) is None:
            raise ProviderNotFoundError(f"No existe el proveedor #{provider_id}.")
        existing = {
            (r.code or "").strip().upper(): r
            for r in self._session.scalars(
                select(ProviderItemRow).where(ProviderItemRow.provider_id == provider_id)
            ).all()
        }
        result: dict[str, ProviderItem] = {}
        for item in items:
            key = (item.code or "").strip().upper()
            row = existing.get(key)
            if row is None:
                row = ProviderItemRow(
                    provider_id=provider_id,
                    code=item.code,
                    description=item.description,
                    price=item.price.as_decimal(),
                )
                self._session.add(row)
                self._session.flush()
                existing[key] = row
            else:
                row.description = item.description
                row.price = item.price.as_decimal()
                self._session.flush()
            result[key] = _provider_item_from_row(row)
        return result

    def list_items(self, provider_id: int) -> list[ProviderItem]:
        stmt = (
            select(ProviderItemRow)
            .where(ProviderItemRow.provider_id == provider_id)
            .order_by(ProviderItemRow.description, ProviderItemRow.code)
        )
        rows = self._session.scalars(stmt).all()
        return [_provider_item_from_row(r) for r in rows]

    def list_all_items(self) -> list[ProviderItem]:
        stmt = select(ProviderItemRow).order_by(ProviderItemRow.description, ProviderItemRow.code)
        rows = self._session.scalars(stmt).all()
        return [_provider_item_from_row(r) for r in rows]


def _purchase_order_from_row(row: PurchaseOrderRow) -> PurchaseOrder:
    order = PurchaseOrder(
        id=row.id,
        order_number=row.order_number,
        status=row.status,
        note=row.note,
        created_at=row.created_at,
    )
    for line_row in row.lines:
        order.lines.append(
            PurchaseOrderLine(
                id=line_row.id,
                provider_item_id=line_row.provider_item_id,
                product_id=line_row.product_id,
                code=line_row.code,
                description=line_row.description,
                provider_name=line_row.provider_name,
                quantity=line_row.quantity,
                unit_price=Money.from_input(line_row.unit_price),
            )
        )
    return order


class SqlAlchemyPurchaseOrderRepository(PurchaseOrderRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, order: PurchaseOrder) -> PurchaseOrder:
        if not order.order_number:
            order.order_number = self.next_order_number()
        row = PurchaseOrderRow(
            order_number=order.order_number,
            status=order.status,
            note=order.note,
        )
        self._session.add(row)
        self._session.flush()
        for line in order.lines:
            self._session.add(
                PurchaseOrderLineRow(
                    purchase_order_id=row.id,
                    provider_item_id=line.provider_item_id,
                    product_id=line.product_id,
                    code=line.code,
                    description=line.description,
                    provider_name=line.provider_name,
                    quantity=line.quantity,
                    unit_price=line.unit_price.as_decimal(),
                )
            )
        self._session.flush()
        order.id = row.id
        return order

    def update(self, order: PurchaseOrder) -> None:
        row = self._session.get(PurchaseOrderRow, order.id)
        if row is None:
            raise ValueError(f"No existe el pedido #{order.id}.")
        row.status = order.status
        row.note = order.note

    def delete(self, purchase_order_id: int) -> None:
        row = self._session.get(PurchaseOrderRow, purchase_order_id)
        if row is None:
            return
        self._session.delete(row)

    def get(self, purchase_order_id: int) -> PurchaseOrder | None:
        row = self._session.get(PurchaseOrderRow, purchase_order_id)
        return _purchase_order_from_row(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        provider: str | None = None,
        limit: int | None = None,
    ) -> list[PurchaseOrder]:
        stmt = select(PurchaseOrderRow)
        if status:
            stmt = stmt.where(PurchaseOrderRow.status == status)
        stmt = stmt.order_by(PurchaseOrderRow.created_at.desc(), PurchaseOrderRow.id.desc())
        if limit:
            stmt = stmt.limit(limit)
        rows = self._session.scalars(stmt).all()
        orders = [_purchase_order_from_row(r) for r in rows]
        if provider:
            needle = provider.strip().lower()
            orders = [
                o for o in orders if any(needle in (name or "").lower() for name in o.provider_names)
            ]
        return orders

    def next_order_number(self) -> str:
        last = self._session.execute(
            select(PurchaseOrderRow.order_number).order_by(PurchaseOrderRow.id.desc()).limit(1)
        ).scalar_one_or_none()
        seq = 0
        if last:
            try:
                seq = int(last.rsplit("-", 1)[-1])
            except ValueError:
                seq = 0
        return f"P-{seq + 1:06d}"