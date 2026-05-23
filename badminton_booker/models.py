from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    date: str
    time_range: str
    court_name: str
    slot_id: str
    available: bool
    price: int | None = None


@dataclass(frozen=True)
class BookingResult:
    success: bool
    message: str
    order_id: str | None = None

