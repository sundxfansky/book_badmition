from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request

from ..capture import CaptureStore
from ..config import BookingConfig, RequestFileConfig, TargetConfig
from ..http_client import send_request
from ..models import BookingResult, Slot
from .base import BookingProvider


class RequestFileProvider(BookingProvider):
    def __init__(self, config: RequestFileConfig) -> None:
        self.config = config
        self._capture = CaptureStore(config.path, config.submit_path)
        self._body = self._capture.submit_body()

    def list_slots(self, target: TargetConfig) -> list[Slot]:
        date = str(self._body.get("venues_date", "")).replace("/", "-")
        slots = []
        for item in self._body.get("venues_site_time", []):
            time_range = f"{item.get('start_time', '')}-{item.get('end_time', '')}"
            court_name = str(item.get("site_name", ""))
            slot_id = f"{date}:{time_range}:{court_name}:{item.get('site_id', '')}"
            slots.append(
                Slot(
                    date=date,
                    time_range=time_range,
                    court_name=court_name,
                    slot_id=slot_id,
                    available=True,
                    price=_to_int(item.get("price")),
                )
            )
        return slots

    def reserve(self, slot: Slot, booking: BookingConfig) -> BookingResult:
        if self.config.dry_run:
            return BookingResult(
                True,
                "Dry run: request parsed successfully, no network request was sent",
                order_id=f"DRY-RUN-{slot.slot_id}",
            )

        request_data = self._capture.build_submit_request({})
        data = json.dumps(request_data["body"], ensure_ascii=False).encode("utf-8")
        request = Request(
            request_data["url"],
            data=data,
            headers=request_data["headers"],
            method=request_data["method"],
        )

        try:
            response_body, _ = send_request(
                request,
                timeout=self.config.timeout_seconds,
                verify_ssl=self.config.verify_ssl,
            )
        except HTTPError as exc:
            return BookingResult(False, f"HTTP {exc.code}: {exc.reason}")
        except URLError as exc:
            return BookingResult(False, f"Network error: {exc.reason}")

        return self._parse_response(response_body)

    def _parse_response(self, response_body: str) -> BookingResult:
        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError:
            return BookingResult(False, f"Non-JSON response: {response_body[:200]}")

        success = payload.get("code") == 0
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        order_id = data.get("order_id") or data.get("reserve_id")
        message = payload.get("msg") or data.get("fail_msg") or str(payload)
        return BookingResult(success, str(message), order_id=str(order_id) if order_id else None)


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
