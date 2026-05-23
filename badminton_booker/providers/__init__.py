from __future__ import annotations

from ..config import AppConfig
from .base import BookingProvider
from .demo import DemoProvider
from .request_file import RequestFileProvider


PROVIDERS: dict[str, type[BookingProvider]] = {
    "demo": DemoProvider,
    "request_file": RequestFileProvider,
}


def create_provider(name: str, config: AppConfig | None = None) -> BookingProvider:
    try:
        provider_class = PROVIDERS[name]
    except KeyError as exc:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown provider '{name}'. Available providers: {known}") from exc
    if provider_class is RequestFileProvider:
        if config is None:
            raise ValueError("request_file provider needs app config")
        return RequestFileProvider(config.request_file)
    return provider_class()
