"""Datos de demostración (solo si la base está vacía)."""

from __future__ import annotations

import logging

from app.application.commands import CreateCategoryCommand, CreateProductCommand
from app.application.queries import GetCatalogQuery, GetCategoriesQuery
from app.application.bus import CommandBus, QueryBus

log = logging.getLogger(__name__)

DEMO_CATEGORIES = [
    ("BEBIDAS", "Gaseosas, aguas, jugos y energéticas"),
    ("SNACKS", "Papas, snacks y palitos"),
    ("GOLOSINAS", "Chocolates y caramelos"),
    ("COMESTIBLES", "Galletitas y alimentos varios"),
    ("OTROS", "Productos varios"),
]

DEMO_PRODUCTS = [
    ("7501055302086", "Agua mineral 500ml", "20", "250", "BEBIDAS", 60, 20),
    ("7790070015037", "Gaseosa Cola 600ml", "40", "320", "BEBIDAS", 40, 15),
    ("7790070015044", "Gaseosa Limón 600ml", "40", "320", "BEBIDAS", 35, 15),
    ("7790070015051", "Jugo de Naranja 1L", "55", "420", "BEBIDAS", 25, 10),
    ("7790352328190", "Energética 473ml", "75", "610", "BEBIDAS", 30, 10),
    ("7790895008474", "Papas fritas clásicas", "30", "230", "SNACKS", 45, 15),
    ("7790895008481", "Papas fritas jamón", "32", "240", "SNACKS", 38, 15),
    ("7790588000457", "Palitos de sal", "28", "200", "SNACKS", 30, 12),
    ("7790588000464", "Palitos queso", "30", "210", "SNACKS", 28, 12),
    ("7790588000471", "Snack maíz picante", "34", "250", "SNACKS", 22, 10),
    ("7622210611505", "Chocolate con leche", "65", "480", "GOLOSINAS", 50, 15),
    ("7622210704115", "Barrita cereal frutilla", "28", "190", "GOLOSINAS", 60, 20),
    ("7790588029861", "Chupetines surtido", "12", "80", "GOLOSINAS", 120, 40),
    ("7790588034506", "Caramelos masticables", "15", "100", "GOLOSINAS", 90, 30),
    ("7622219503931", "Chocolate blanco", "67", "500", "GOLOSINAS", 32, 12),
    ("7790070280107", "Galletitas rellenas vainilla", "38", "280", "COMESTIBLES", 50, 15),
    ("7790070280152", "Galletitas rellenas chocolate", "40", "295", "COMESTIBLES", 45, 15),
    ("7790070280206", "Galletitas de agua", "30", "210", "COMESTIBLES", 40, 15),
    ("7790070280251", "Pan dulce chico", "70", "520", "COMESTIBLES", 15, 6),
    ("7790070280305", "Café con leche en sachet", "45", "350", "COMESTIBLES", 25, 10),
    ("7790070280350", "Alfajores triples", "35", "260", "COMESTIBLES", 40, 12),
    ("7790070280404", "Manteca individual", "18", "120", "COMESTIBLES", 30, 10),
    ("7790070280459", "Harina 1kg", "90", "700", "COMESTIBLES", 20, 8),
    ("7790070280503", "Fósforos", "10", "60", "OTROS", 80, 25),
]


def seed_demo_data(commands: CommandBus, queries: QueryBus) -> None:
    """Carga datos de ejemplo cuando no existe ningún producto."""
    existing = queries.ask(GetCatalogQuery(include_inactive=True))
    if existing:
        log.info("La base ya tiene datos; se omite la carga de demo.")
        return

    categories = queries.ask(GetCategoriesQuery())
    index = {c.name: c.id for c in categories}
    for name, description in DEMO_CATEGORIES:
        if name not in index:
            created = commands.execute(CreateCategoryCommand(name=name, description=description))
            index[name] = created.id

    for code, name, price, cost, category, stock, min_stock in DEMO_PRODUCTS:
        commands.execute(
            CreateProductCommand(
                code=code,
                name=name,
                unit_price=price,
                cost=cost,
                category_id=index[category],
                stock=stock,
                min_stock=min_stock,
            )
        )
    log.info("Datos de demostración cargados: %d productos.", len(DEMO_PRODUCTS))