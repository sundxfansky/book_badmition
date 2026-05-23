from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TargetConfig:
    dates: list[str]
    time_ranges: list[str]
    court_names: list[str]


@dataclass(frozen=True)
class BookingConfig:
    user_name: str
    phone: str
    participants: int


@dataclass(frozen=True)
class NotificationConfig:
    webhook_url: str = ""


@dataclass(frozen=True)
class RequestFileConfig:
    path: str = "request.txt"
    submit_path: str = "/v2/reserve/submit?"
    dry_run: bool = True
    timeout_seconds: int = 10
    verify_ssl: bool = False


@dataclass(frozen=True)
class AppConfig:
    provider: str
    poll_interval_seconds: int
    max_attempts: int
    target: TargetConfig
    booking: BookingConfig
    notification: NotificationConfig
    request_file: RequestFileConfig


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    target = raw.get("target", {})
    booking = raw.get("booking", {})
    notification = raw.get("notification", {})
    request_file = raw.get("request_file", {})

    return AppConfig(
        provider=str(raw.get("provider", "demo")),
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 10)),
        max_attempts=int(raw.get("max_attempts", 0)),
        target=TargetConfig(
            dates=list(target.get("dates", [])),
            time_ranges=list(target.get("time_ranges", [])),
            court_names=list(target.get("court_names", [])),
        ),
        booking=BookingConfig(
            user_name=str(booking.get("user_name", "")),
            phone=str(booking.get("phone", "")),
            participants=int(booking.get("participants", 1)),
        ),
        notification=NotificationConfig(
            webhook_url=str(notification.get("webhook_url", "")),
        ),
        request_file=RequestFileConfig(
            path=str(request_file.get("path", "request.txt")),
            submit_path=str(request_file.get("submit_path", "/v2/reserve/submit?")),
            dry_run=bool(request_file.get("dry_run", True)),
            timeout_seconds=int(request_file.get("timeout_seconds", 10)),
            verify_ssl=bool(request_file.get("verify_ssl", False)),
        ),
    )
