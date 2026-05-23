from __future__ import annotations

from ..config import BookingConfig, TargetConfig
from ..models import BookingResult, Slot
from .base import BookingProvider


class DemoProvider(BookingProvider):
    """A deterministic local provider for development and tests."""

    def __init__(self) -> None:
        self._reserved_slot_ids: set[str] = set()

    def list_slots(self, target: TargetConfig) -> list[Slot]:
        slots: list[Slot] = []
        court_names = target.court_names or ["Court 1", "Court 2", "Court 3"]

        for date in target.dates:
            for time_range in target.time_ranges:
                for court_name in court_names:
                    slot_id = f"{date}:{time_range}:{court_name}"
                    slots.append(
                        Slot(
                            date=date,
                            time_range=time_range,
                            court_name=court_name,
                            slot_id=slot_id,
                            available=slot_id not in self._reserved_slot_ids,
                            price=60,
                        )
                    )
        return slots

    def reserve(self, slot: Slot, booking: BookingConfig) -> BookingResult:
        if not booking.user_name or not booking.phone:
            return BookingResult(False, "Missing booking user_name or phone")

        if slot.slot_id in self._reserved_slot_ids:
            return BookingResult(False, "Slot already reserved")

        self._reserved_slot_ids.add(slot.slot_id)
        return BookingResult(True, "Reserved by demo provider", order_id=f"DEMO-{slot.slot_id}")

