"""WhatsApp provider registry — lookup by configured provider name."""

from __future__ import annotations

from typing import Callable, Dict, Optional, TypeVar

T = TypeVar("T")

_PROVIDER_FACTORIES: Dict[str, Callable[..., T]] = {}


def register_provider(name: str, factory: Callable[..., T]) -> None:
    """Register a provider factory under ``name`` (e.g. ``meta``, ``wwebjs``)."""
    _PROVIDER_FACTORIES[name.strip().lower()] = factory


def get_provider_factory(name: str) -> Optional[Callable[..., T]]:
    """Return the factory for ``name``, or ``None`` when unregistered."""
    if not name:
        return None
    return _PROVIDER_FACTORIES.get(name.strip().lower())


def registered_provider_names() -> frozenset[str]:
    """Names of all registered providers (for diagnostics/tests)."""
    return frozenset(_PROVIDER_FACTORIES.keys())


def _register_builtin_providers() -> None:
    from .meta_api import MetaWhatsAppAPI
    from .ultramsg import UltraMsgAPI
    from .wppconnect import WPPConnectAPI
    from .wwebjs_api import WWebJSAPI

    register_provider("meta", MetaWhatsAppAPI)
    register_provider("wppconnect", WPPConnectAPI)
    register_provider("wwebjs", WWebJSAPI)
    register_provider("ultramsg", UltraMsgAPI)


_register_builtin_providers()
