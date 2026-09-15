"""Importación masiva de un inventario (xlsx) a la base local de SQLite."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.infrastructure.db import create_session_factory, init_database
from app.infrastructure.importers.xlsx import InventoryRow, read_inventory_xlsx
from app.infrastructure.orm import CategoryRow, InventoryRow as InventoryRowORM, ProductRow
from app.infrastructure.topology import DEFAULT_BRANCH_ID


@dataclass(slots=True)
class ImportResult:
    products: int = 0
    categories_created: int = 0
    categories_total: int = 0
    errors: list[str] = field(default_factory=list)


class DatabaseNotEmptyError(RuntimeError):
    """La base destino ya contiene productos; se rehúsa a mezclar inventarios."""


def _existing_categories(session) -> dict[str, int]:
    rows = session.execute(select(CategoryRow.id, CategoryRow.name)).all()
    return {name: id_ for id_, name in rows}


def import_inventory_rows(engine: Engine, rows: list[InventoryRow], *, limit: int | None = None) -> ImportResult:
    """Inserta las filas en una base vacía. Levanta ``DatabaseNotEmptyError`` si ya hay productos."""
    init_database(engine)
    result = ImportResult()
    with create_session_factory(engine)() as session:
        product_count = session.execute(select(func.count(ProductRow.id))).scalar_one()
        if product_count > 0:
            raise DatabaseNotEmptyError(
                f"La base ya contiene {product_count} productos. Usá una base nueva para importar el inventario."
            )

        names = _existing_categories(session)
        for row in rows:
            department = (row.department or "").strip()
            if not department:
                continue
            if department not in names:
                category = CategoryRow(name=department)
                session.add(category)
                session.flush()
                names[department] = category.id or 0
                result.categories_created += 1
        result.categories_total = len(names)

        rows_to_insert = rows if limit is None else rows[:limit]
        for row in rows_to_insert:
            result.products += 1
            product = ProductRow(
                code=row.code,
                name=row.name,
                description="",
                category_id=names.get((row.department or "").strip()),
                unit_price=row.unit_price,
                cost=row.cost,
                wholesale_price=row.wholesale_price,
                stock=row.stock,
                min_stock=row.min_stock,
                active=True,
            )
            session.add(product)
            session.flush()
            session.add(
                InventoryRowORM(
                    product_id=product.id,
                    branch_id=DEFAULT_BRANCH_ID,
                    stock=row.stock,
                    min_stock=row.min_stock,
                )
            )
        session.commit()
    return result


def import_inventory_xlsx(engine: Engine, path: str, *, limit: int | None = None) -> ImportResult:
    """Lee ``path`` (xlsx de inventario) y lo importa en ``engine``."""
    rows = read_inventory_xlsx(path)
    return import_inventory_rows(engine, rows, limit=limit)