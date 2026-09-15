"""Vista de inicio: indicadores del día y alertas."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.bus import QueryBus
from app.application.queries import GetCatalogQuery, GetDashboardQuery
from app.domain.value_objects import Money
from app.interface.widgets import Card, make_label, make_table

RED = QColor("#b91c1c")


class MetricCard(Card):
    def __init__(self, label: str):
        super().__init__()
        self.value_label = make_label("-", object_name="kpiValue")
        label_widget = make_label(label, object_name="kpiLabel")
        self.add(self.value_label)
        self.add(label_widget)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


def _status_item(text: str, dangerous: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    if dangerous:
        item.setForeground(RED)
    return item


class DashboardView(QWidget):
    def __init__(self, queries: QueryBus):
        super().__init__()
        self._queries = queries

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)
        layout.addWidget(make_label("Inicio", object_name="pageTitle"))

        grid = QGridLayout()
        grid.setSpacing(10)
        self.sales_today = MetricCard("VENTAS HOY")
        self.revenue_today = MetricCard("INGRESOS HOY")
        self.cash_today = MetricCard("EFECTIVO")
        self.card_today = MetricCard("TARJETA / OTROS")
        self.low_stock = MetricCard("STOCK BAJO")
        self.out_stock = MetricCard("SIN STOCK")
        grid.addWidget(self.sales_today, 0, 0)
        grid.addWidget(self.revenue_today, 0, 1)
        grid.addWidget(self.cash_today, 0, 2)
        grid.addWidget(self.card_today, 1, 0)
        grid.addWidget(self.low_stock, 1, 1)
        grid.addWidget(self.out_stock, 1, 2)
        layout.addLayout(grid)

        middle = QHBoxLayout()
        middle.setSpacing(14)

        self.top_card = Card("Productos más vendidos (7 días)")
        self.top_table: QTableWidget = make_table(["Producto", "Cantidad", "Ingresos"], stretch_column=0)
        self.top_card.add(self.top_table)
        middle.addWidget(self.top_card, 3)

        self.low_card = Card("Alerta de stock")
        self.low_table: QTableWidget = make_table(["Producto", "Stock", "Mínimo"], stretch_column=0)
        self.low_btn = QPushButton("Ir a inventario")
        self.low_btn.setObjectName("ghost")
        self.low_btn.clicked.connect(self._goto_inventory)
        low_controls = QHBoxLayout()
        low_controls.addStretch(1)
        low_controls.addWidget(self.low_btn)
        self.low_card.add(self.low_table)
        self.low_card.add_layout(low_controls)
        middle.addWidget(self.low_card, 2)
        layout.addLayout(middle, 1)

        self._goto_inventory_cb = None

    def set_inventory_navigator(self, callback) -> None:
        self._goto_inventory_cb = callback

    def _goto_inventory(self) -> None:
        if self._goto_inventory_cb:
            self._goto_inventory_cb()

    def refresh(self) -> None:
        dash = self._queries.ask(GetDashboardQuery())
        self.sales_today.set_value(str(dash.today_sales_count))
        self.revenue_today.set_value(dash.today_revenue.format())
        self.cash_today.set_value(dash.today_revenue_by_method.get("EFECTIVO", Money.zero()).format())
        others = sum(
            (value for key, value in dash.today_revenue_by_method.items() if key != "EFECTIVO"),
            Money.zero(),
        )
        self.card_today.set_value(others.format())
        self.low_stock.set_value(str(dash.low_stock_count))
        self.out_stock.set_value(str(dash.out_of_stock_count))

        self.top_table.setRowCount(0)
        for top in dash.top_products:
            row = self.top_table.rowCount()
            self.top_table.insertRow(row)
            self.top_table.setItem(row, 0, QTableWidgetItem(top.name))
            self.top_table.setItem(row, 1, QTableWidgetItem(str(top.quantity)))
            self.top_table.setItem(row, 2, QTableWidgetItem(top.revenue.format()))

        low_products = [
            p for p in self._queries.ask(GetCatalogQuery()) if p.low_stock or p.out_of_stock
        ]
        self.low_table.setRowCount(0)
        for product in low_products[:20]:
            row = self.low_table.rowCount()
            self.low_table.insertRow(row)
            self.low_table.setItem(row, 0, QTableWidgetItem(f"{product.name} ({product.code})"))
            self.low_table.setItem(row, 1, _status_item(str(product.stock), dangerous=True))
            self.low_table.setItem(row, 2, QTableWidgetItem(str(product.min_stock)))