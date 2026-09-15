"""Importa un inventario desde un archivo Excel al sistema.

Uso:
    python -m app.inventory_import "app/total inventario.xlsx" [--db data/kiosco.db] [--dry-run] [--limit N]

El importador requiere una base vacía (sin productos). La columna 'Código' se
usa como código de barras (EAN-13 si tiene 13 dígitos, CODE128 en caso
contrario), 'Producto' como nombre, 'P. Venta' como precio y 'Departamento'
como categoría (se crean al vuelo).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.infrastructure.db import create_engine_for
from app.infrastructure.importers.inventory import DatabaseNotEmptyError, import_inventory_xlsx
from app.infrastructure.importers.xlsx import read_inventory_xlsx
from app.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importa un inventario Excel al Kiosco POS.")
    parser.add_argument("path", help="Ruta al archivo .xlsx de inventario.")
    parser.add_argument("--db", default=Settings().database_path, help="Base SQLite destino.")
    parser.add_argument("--dry-run", action="store_true", help="Solo informa qué se importaría.")
    parser.add_argument("--limit", type=int, default=None, help="Importa solo las primeras N filas.")
    args = parser.parse_args(argv)

    source = Path(args.path)
    if not source.is_file():
        print(f"ERROR: no existe el archivo {source}")
        return 2

    rows = read_inventory_xlsx(source)
    if not rows:
        print("ERROR: no se encontraron productos válidos en el archivo.")
        return 2

    departments = len({r.department for r in rows if r.department})
    no_dept = sum(1 for r in rows if not r.department)
    no_stock = sum(1 for r in rows if r.stock == 0)
    print(f"Archivo:  {source}")
    print(f"Leídos:   {len(rows)} productos | {departments} departamentos | {no_dept} sin depto | {no_stock} sin stock")

    if args.dry_run:
        print("(solo prueba): no se escribió nada en la base.")
        return 0

    engine = create_engine_for(args.db)
    try:
        result = import_inventory_xlsx(engine, args.path, limit=args.limit)
    except DatabaseNotEmptyError as exc:
        print(f"ERROR: {exc}")
        return 1

    print("Importación completada:")
    print(f"  productos      : {result.products}")
    print(f"  categorías     : {result.categories_total} (creadas: {result.categories_created})")
    print(f"  base           : {args.db}")
    if result.errors:
        print(f"  errores        : {len(result.errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())