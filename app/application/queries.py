"""Consultas (lectura)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.application.bus import Query


@dataclass(frozen=True, slots=True)
class GetCategoriesQuery(Query):
    pass


@dataclass(frozen=True, slots=True)
class GetCatalogQuery(Query):
    search: str = ""
    include_inactive: bool = False
    category_id: int | None = None


@dataclass(frozen=True, slots=True)
class GetProductQuery(Query):
    product_id: int


@dataclass(frozen=True, slots=True)
class GetSalesHistoryQuery(Query):
    start: datetime | None = None
    end: datetime | None = None
    status: str | None = None
    search: str = ""
    limit: int | None = 500


@dataclass(frozen=True, slots=True)
class GetSaleQuery(Query):
    sale_id: int


@dataclass(frozen=True, slots=True)
class GetDashboardQuery(Query):
    pass


@dataclass(frozen=True, slots=True)
class GetStockMovementsQuery(Query):
    product_id: int
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetApartadosQuery(Query):
    status: str | None = None
    search: str = ""


@dataclass(frozen=True, slots=True)
class GetApartadoQuery(Query):
    apartado_id: int


@dataclass(frozen=True, slots=True)
class GetCashMovementsQuery(Query):
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True, slots=True)
class GetOpenCashDayQuery(Query):
    pass


@dataclass(frozen=True, slots=True)
class GetLastCashDayQuery(Query):
    pass


@dataclass(frozen=True, slots=True)
class GetCorteQuery(Query):
    day_id: int | None = None


@dataclass(frozen=True, slots=True)
class ListProvidersQuery(Query):
    pass


@dataclass(frozen=True, slots=True)
class ListProviderItemsQuery(Query):
    provider_id: int | None = None


@dataclass(frozen=True, slots=True)
class ListPurchaseOrdersQuery(Query):
    """Lista pedidos guardados.

    ``status`` en None busca todos; ``provider`` filtra los pedidos que
    contienen al menos un renglón de ese proveedor (coincidencia por nombre).
    """
    status: str | None = None
    provider: str | None = None


@dataclass(frozen=True, slots=True)
class ListValesQuery(Query):
    """Lista los vales de la sucursal para consulta y anulación.

    ``status`` en None busca todos; ``term`` busca por código, ticket o cajero.
    """
    status: str | None = None
    term: str = ""
    limit: int | None = 200


@dataclass(frozen=True, slots=True)
class GetValeQuery(Query):
    code: str = ""
    vale_id: int | None = None