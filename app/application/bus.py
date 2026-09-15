"""Buses de comandos y consultas (patrón CQRS en proceso)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from app.domain.exceptions import DomainError

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Command:
    """Marca genérica para todos los comandos de escritura."""


@dataclass(frozen=True, slots=True)
class Query:
    """Marca genérica para todas las consultas de lectura."""


class Handler(Generic[T]):
    def handle(self, command: T) -> Any:  # pragma: no cover - interfaz
        raise NotImplementedError


class CommandBus:
    """Enruta cada comando a su handler y registra errores de dominio."""

    def __init__(self, handlers: dict[type, Handler]):
        self._handlers = handlers

    def execute(self, command: Command) -> Any:
        handler = self._handlers[type(command)]
        try:
            return handler.handle(command)
        except DomainError:
            log.warning("Comando %s rechazado por regla de negocio.", type(command).__name__, exc_info=True)
            raise
        except Exception:
            log.error("Error ejecutando el comando %s.", type(command).__name__, exc_info=True)
            raise


class QueryBus:
    """Enruta cada consulta a su handler."""

    def __init__(self, handlers: dict[type, Handler]):
        self._handlers = handlers

    def ask(self, query: Query) -> Any:
        handler = self._handlers[type(query)]
        return handler.handle(query)