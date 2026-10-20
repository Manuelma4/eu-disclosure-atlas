"""Connector registry with automatic discovery of every module in this package."""

from __future__ import annotations

import importlib
import pkgutil

from .base import _REGISTRY, Connector, ParseContext, RawDocument, date_windows, register

__all__ = ["Connector", "ParseContext", "RawDocument", "all_connectors", "date_windows", "get", "register"]


def _discover() -> None:
    for module in pkgutil.iter_modules(__path__):
        if module.name != "base" and not module.name.startswith("_"):
            importlib.import_module(f"{__name__}.{module.name}")


def all_connectors() -> dict[str, type[Connector]]:
    _discover()
    return dict(sorted(_REGISTRY.items()))


def get(name: str) -> type[Connector]:
    connectors = all_connectors()
    if name not in connectors:
        raise KeyError(f"unknown source {name!r}; available: {', '.join(connectors)}")
    return connectors[name]
