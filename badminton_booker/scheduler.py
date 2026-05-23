from __future__ import annotations

import time

from .config import AppConfig
from .models import BookingResult, Slot
from .notifier import Notifier
from .providers.base import BookingProvider


class BookingRunner:
    def __init__(self, config: AppConfig, provider: BookingProvider, notifier: Notifier) -> None:
        self.config = config
        self.provider = provider
        self.notifier = notifier

    def run_once(self) -> BookingResult:
        for slot in self._matching_slots():
            self.notifier.slot_found(slot)
            result = self.provider.reserve(slot, self.config.booking)
            self.notifier.booking_finished(slot, result)
            if result.success:
                return result
        return BookingResult(False, "No available matching slots")

    def watch(self) -> BookingResult:
        attempt = 0
        while True:
            attempt += 1
            print(f"[WATCH] attempt={attempt}")
            result = self.run_once()
            if result.success:
                return result

            if self.config.max_attempts and attempt >= self.config.max_attempts:
                return result

            time.sleep(self.config.poll_interval_seconds)

    def _matching_slots(self) -> list[Slot]:
        slots = self.provider.list_slots(self.config.target)
        return [slot for slot in slots if slot.available and self._matches_target(slot)]

    def _matches_target(self, slot: Slot) -> bool:
        target = self.config.target
        if target.dates and slot.date not in target.dates:
            return False
        if target.time_ranges and slot.time_range not in target.time_ranges:
            return False
        if target.court_names and slot.court_name not in target.court_names:
            return False
        return True

