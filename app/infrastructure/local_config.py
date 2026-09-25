"""Ajustes locales persistentes en la tabla ``sys_config`` (clave/valor).

Complementa la topología: además de sucursal/terminal/servidor, aquí viven los
datos del local, moneda, credenciales de acceso e impresora de etiquetas.

Los valores se leen al arrancar (``build_services`` -> ``load_local_config``)
y se escriben desde la pantalla Configuración.
"""

from __future__ import annotations

from sqlalchemy import select

KEY_STORE_NAME = "store_name"
KEY_STORE_ADDRESS = "store_address"
KEY_STORE_PHONE = "store_phone"
KEY_STORE_FOOTER = "store_footer"
KEY_CURRENCY = "currency"
KEY_LOGIN_USERNAME = "login_username"
KEY_LOGIN_PASSWORD = "login_password"
KEY_LABEL_PRINTER_KIND = "label_printer_kind"
KEY_BROTHER_PRINTER_IP = "brother_printer_ip"
KEY_LABEL_WIDTH_MM = "label_width_mm"
KEY_LABEL_HEIGHT_MM = "label_height_mm"
KEY_LABEL_DPI = "label_dpi"
KEY_TICKET_PRINTER = "ticket_printer"

LOCAL_CONFIG_KEYS: tuple[str, ...] = (
    KEY_STORE_NAME,
    KEY_STORE_ADDRESS,
    KEY_STORE_PHONE,
    KEY_STORE_FOOTER,
    KEY_CURRENCY,
    KEY_LOGIN_USERNAME,
    KEY_LOGIN_PASSWORD,
    KEY_LABEL_PRINTER_KIND,
    KEY_BROTHER_PRINTER_IP,
    KEY_LABEL_WIDTH_MM,
    KEY_LABEL_HEIGHT_MM,
    KEY_LABEL_DPI,
    KEY_TICKET_PRINTER,
)


def _to_float(value: object | None, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: object | None, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def load_local_config(settings, session) -> None:
    """Lee ``sys_config`` y reescribe las opciones del ``Settings`` en memoria.

    ``Settings`` es un dataclass frozen; se reescribe con ``object.__setattr__``
    conservando la misma referencia (los consumidores no quedan stale).
    """
    from app.infrastructure.orm import SysConfigRow
    from app.settings import StoreInfo

    rows = session.execute(
        select(SysConfigRow).where(SysConfigRow.key.in_(LOCAL_CONFIG_KEYS))
    ).scalars().all()
    values = {row.key: row.value for row in rows}
    if not values:
        return

    store = StoreInfo(
        name=values.get(KEY_STORE_NAME, settings.store.name),
        address=values.get(KEY_STORE_ADDRESS, settings.store.address),
        phone=values.get(KEY_STORE_PHONE, settings.store.phone),
        footer=values.get(KEY_STORE_FOOTER, settings.store.footer),
    )
    object.__setattr__(settings, "store", store)
    object.__setattr__(
        settings,
        "currency",
        values.get(KEY_CURRENCY, settings.currency),
    )
    object.__setattr__(
        settings,
        "login_username",
        values.get(KEY_LOGIN_USERNAME, settings.login_username),
    )
    object.__setattr__(
        settings,
        "login_password",
        values.get(KEY_LOGIN_PASSWORD, settings.login_password),
    )
    object.__setattr__(
        settings,
        "label_printer_kind",
        values.get(KEY_LABEL_PRINTER_KIND, settings.label_printer_kind),
    )
    object.__setattr__(
        settings,
        "brother_printer_ip",
        values.get(KEY_BROTHER_PRINTER_IP, settings.brother_printer_ip),
    )
    object.__setattr__(
        settings,
        "label_width_mm",
        _to_float(values.get(KEY_LABEL_WIDTH_MM), settings.label_width_mm),
    )
    object.__setattr__(
        settings,
        "label_height_mm",
        _to_float(values.get(KEY_LABEL_HEIGHT_MM), settings.label_height_mm),
    )
    object.__setattr__(
        settings,
        "label_dpi",
        _to_int(values.get(KEY_LABEL_DPI), settings.label_dpi),
    )
    object.__setattr__(
        settings,
        "ticket_printer",
        values.get(KEY_TICKET_PRINTER, settings.ticket_printer),
    )


def apply_local_config(settings, *, store_name=None, store_address=None, store_phone=None,
                       store_footer=None, currency=None, login_username=None,
                       login_password=None, label_printer_kind=None, brother_printer_ip=None,
                       label_width_mm=None, label_height_mm=None, label_dpi=None,
                       ticket_printer=None) -> None:
    """Aplica en memoria un subconjunto de ajustes (sólo los no ``None``)."""
    from app.settings import StoreInfo

    if any(value is not None for value in (store_name, store_address, store_phone, store_footer)):
        store = StoreInfo(
            name=store_name if store_name is not None else settings.store.name,
            address=store_address if store_address is not None else settings.store.address,
            phone=store_phone if store_phone is not None else settings.store.phone,
            footer=store_footer if store_footer is not None else settings.store.footer,
        )
        object.__setattr__(settings, "store", store)
    if currency is not None:
        object.__setattr__(settings, "currency", currency)
    if login_username is not None:
        object.__setattr__(settings, "login_username", login_username)
    if login_password is not None:
        object.__setattr__(settings, "login_password", login_password)
    if label_printer_kind is not None:
        object.__setattr__(settings, "label_printer_kind", label_printer_kind)
    if brother_printer_ip is not None:
        object.__setattr__(settings, "brother_printer_ip", brother_printer_ip)
    if label_width_mm is not None:
        object.__setattr__(settings, "label_width_mm", label_width_mm)
    if label_height_mm is not None:
        object.__setattr__(settings, "label_height_mm", label_height_mm)
    if label_dpi is not None:
        object.__setattr__(settings, "label_dpi", label_dpi)
    if ticket_printer is not None:
        object.__setattr__(settings, "ticket_printer", ticket_printer)