from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import BookingConfig, TargetConfig
from ..models import BookingResult, Slot


class BookingProvider(ABC):
    @abstractmethod
    def list_slots(self, target: TargetConfig) -> list[Slot]:
        """Return slots that may match the target."""

    @abstractmethod
    def reserve(self, slot: Slot, booking: BookingConfig) -> BookingResult:
        """Try to reserve one slot."""

