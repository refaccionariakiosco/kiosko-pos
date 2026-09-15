"""Motor de base de datos y creación del esquema (SQLite + SQLAlchemy)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def _foreign_keys_on(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def create_engine_for(db_path: Path | str) -> Engine:
    if db_path == ":memory:":
        return create_engine("sqlite://", poolclass=StaticPool)
    if isinstance(db_path, str) and db_path.startswith("sqlite://"):
        return create_engine(db_path)
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_database(engine: Engine) -> None:
    # importar modelos vía convención para registrar el metadata
    from app.infrastructure import orm  # noqa: F401

    from app.infrastructure.orm import GLOBAL_METADATA

    GLOBAL_METADATA.create_all(engine)
    apply_migrations(engine)
    log.info("Esquema de base de datos inicializado.")


def apply_migrations(engine: Engine) -> None:
    """Migraciones ligeras para bases existentes (ALTER TABLE idempotente)."""
    if engine.url.drivername.startswith("sqlite") is False:
        return
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "sales" not in inspector.get_table_names():
        return
    statements: list[str] = []
    sales_columns = {c["name"] for c in inspector.get_columns("sales")}
    if "discount" not in sales_columns:
        statements.append("ALTER TABLE sales ADD COLUMN discount NUMERIC(12,2) NOT NULL DEFAULT 0")
    sale_items_columns = {c["name"] for c in inspector.get_columns("sale_items")}
    if "refunded_qty" not in sale_items_columns:
        statements.append("ALTER TABLE sale_items ADD COLUMN refunded_qty INTEGER NOT NULL DEFAULT 0")
    if "provider_items" in inspector.get_table_names():
        provider_items_columns = {c["name"] for c in inspector.get_columns("provider_items")}
        if "product_id" not in provider_items_columns:
            statements.append("ALTER TABLE provider_items ADD COLUMN product_id INTEGER")
    if "stock_movements" in inspector.get_table_names():
        stock_movements_columns = {c["name"] for c in inspector.get_columns("stock_movements")}
        if "document" not in stock_movements_columns:
            statements.append("ALTER TABLE stock_movements ADD COLUMN document VARCHAR(64) NOT NULL DEFAULT ''")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        log.info("Migraciones aplicadas: %s", statements)

    _backfill_stock_movement_documents(engine)

    # Fase 1 (offline-first): separación catálogo vs inventario.
    # Crea la tabla inventory (si faltara) y migra las existencias históricas de
    # products -> inventory para la sucursal por defecto (single-site heredado).
    from app.infrastructure.topology import DEFAULT_BRANCH_ID

    tables = set(inspector.get_table_names())
    if "products" not in tables:
        return
    if "inventory" not in tables:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE inventory ("
                    "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
                    "product_id INTEGER NOT NULL, "
                    "branch_id VARCHAR(128) NOT NULL, "
                    "stock INTEGER NOT NULL DEFAULT 0, "
                    "min_stock INTEGER NOT NULL DEFAULT 0, "
                    "updated_at DATETIME NOT NULL, "
                    "CONSTRAINT uq_inventory_product_branch UNIQUE (product_id, branch_id), "
                    "FOREIGN KEY (product_id) REFERENCES products (id)"
                    ")"
                )
            )
        log.info("Tabla inventory creada por migración.")
    with engine.begin() as connection:
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_branch_id ON inventory (branch_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_inventory_product_id ON inventory (product_id)"))
    # Solo migra el stock histórico a la sucursal por defecto en instalaciones
    # sin sucursal configurada todavía. Si el operador ya asignó id_sucursal,
    # su inventario vive bajo esa sucursal y no debe recrearse BRANCH-LOCAL.
    custom_branch = None
    if "sys_config" in tables:
        with engine.connect() as connection:
            custom_branch = connection.execute(
                text("SELECT value FROM sys_config WHERE key = 'id_sucursal'")
            ).scalar()
    if not custom_branch:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO inventory (product_id, branch_id, stock, min_stock, updated_at) "
                    "SELECT p.id, :branch, p.stock, p.min_stock, COALESCE(p.updated_at, CURRENT_TIMESTAMP) "
                    "FROM products p "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM inventory i WHERE i.product_id = p.id AND i.branch_id = :branch"
                    ")"
                ),
                {"branch": DEFAULT_BRANCH_ID},
            )
        log.info("Inventario separado del catálogo: stock migrado a sucursal %s.", DEFAULT_BRANCH_ID)


# Folios aceptados: R-000123 (legado) y R-1-000123 (multi-caja), P-0001, A-0001.
_FOLIO_RE = re.compile(r"\b(?:R|P|A)-\d+(?:-\d+)?\b")


def _backfill_stock_movement_documents(engine: Engine) -> None:
    """Llena el folio de documento de movimientos históricos a partir de la nota."""
    if engine.url.drivername.startswith("sqlite") is False:
        return
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "stock_movements" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("stock_movements")}
    if "document" not in columns:
        return
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT id, note FROM stock_movements WHERE document = ''")
        ).fetchall()
        updates = []
        for row_id, note in rows:
            match = _FOLIO_RE.search(note or "")
            if match:
                updates.append((row_id, match.group(0)))
        for row_id, folio in updates:
            connection.execute(text("UPDATE stock_movements SET document = :folio WHERE id = :id"), {"folio": folio, "id": row_id})
    if updates:
        log.info("Backfill de folios en stock_movements: %s filas.", len(updates))


def drop_all(engine: Engine) -> None:
    from app.infrastructure import orm  # noqa: F401

    from app.infrastructure.orm import GLOBAL_METADATA

    GLOBAL_METADATA.drop_all(engine)