from __future__ import annotations

from .config import NotificationConfig
from .models import BookingResult, Slot


class Notifier:
    def __init__(self, config: NotificationConfig) -> None:
        self.config = config

    def slot_found(self, slot: Slot) -> None:
        print(
            f"[FOUND] {slot.date} {slot.time_range} {slot.court_name}"
            f" price={slot.price if slot.price is not None else 'unknown'}"
        )

    def booking_finished(self, slot: Slot, result: BookingResult) -> None:
        status = "SUCCESS" if result.success else "FAILED"
        print(f"[{status}] {slot.date} {slot.time_range} {slot.court_name}: {result.message}")
        if result.order_id:
            print(f"[ORDER] {result.order_id}")

