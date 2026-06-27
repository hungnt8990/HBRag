"""Application services."""

from importlib import import_module
from types import ModuleType

_LEGACY_MODULES = {
    "ingestion_queue": "app.services.ingestion.ingestion_queue",
    "vector_store": "app.services.vector.vector_store",
}

__all__ = sorted(_LEGACY_MODULES)


def __getattr__(name: str) -> ModuleType:
    if name in _LEGACY_MODULES:
        module = import_module(_LEGACY_MODULES[name])
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
