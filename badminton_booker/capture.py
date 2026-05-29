from __future__ import annotations

import copy
import base64
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, unquote, urlparse, urlunparse

from .venue_defaults import FIXED_COURTS, FIXED_TIME_SLOTS, default_date


SUBMIT_PATH = "/v2/reserve/submit?"
SITE_LIST_PREFIX = "/v1/venues/venues_site_list"
CALENDAR_PREFIX = "/v1/venues/calendar"


@dataclass(frozen=True)
class CourtOption:
    site_id: int
    site_name: str


@dataclass(frozen=True)
class TimeOption:
    start_time: str
    end_time: str
    start_timestamp: int
    end_timestamp: int
    price: str
    times: str
    source_date: str = ""


@dataclass(frozen=True)
class VenueSnapshot:
    venues_id: str
    date: str
    dates: list[str]
    courts: list[CourtOption]
    times: list[TimeOption]
    selected_site_id: int | None
    selected_site_name: str
    fixed_courts: list[CourtOption]
    selected_times: list[TimeOption]


class CaptureStore:
    def __init__(self, path: str | Path = "request.txt", submit_path: str = SUBMIT_PATH) -> None:
        self.path = Path(path)
        self.submit_path = submit_path
        self.entries = json.loads(self.path.read_text(encoding="utf-8"))

    def submit_entry(self) -> dict:
        for entry in self.entries:
            if entry.get("path") == self.submit_path:
                return entry
        raise ValueError(f"No request found for path: {self.submit_path}")

    def submit_body(self) -> dict:
        return decode_json_body(self.submit_entry().get("req", {}))

    def submit_headers(self) -> dict[str, str]:
        headers = dict(self.submit_entry().get("req", {}).get("headers", {}))
        headers = sanitize_headers(headers)
        headers["content-type"] = "application/json"
        return headers

    def site_list_entry(self) -> dict:
        entry = self.latest_site_list_entry()
        if not entry:
            raise ValueError("No venues_site_list request found")
        return entry

    def site_list_headers(self) -> dict[str, str]:
        headers = dict(self.site_list_entry().get("req", {}).get("headers", {}))
        return sanitize_headers(headers)

    def build_site_list_request(self, params: dict, date: str) -> dict:
        entry = self.site_list_entry()
        headers = self.site_list_headers()
        headers.update(params.get("headers") or {})

        parsed = urlparse(str(entry.get("url") or ""))
        query = parse_qs(parsed.query)
        query["date"] = [date]
        url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        return {
            "method": entry.get("req", {}).get("method", "GET"),
            "url": url,
            "headers": headers,
            "body": None,
        }

    def latest_site_list_entry(self) -> dict | None:
        site_entries = [
            entry
            for entry in self.entries
            if str(entry.get("path", "")).startswith(SITE_LIST_PREFIX)
            and entry.get("hostname") == "stmember.styd.cn"
        ]
        if not site_entries:
            return None
        return max(site_entries, key=lambda entry: int(entry.get("order") or 0))

    def decode_site_list_response(self, entry: dict | None = None) -> dict:
        target = entry or self.site_list_entry()
        return decode_json_body(target.get("res", {}))

    def available_dates(self) -> list[str]:
        dates: list[str] = []
        for entry in self.entries:
            path = str(entry.get("path", ""))
            if path.startswith(CALENDAR_PREFIX):
                payload = decode_json_body(entry.get("res", {}))
                data = payload.get("data", {}) if isinstance(payload, dict) else {}
                for item in data.get("list", []):
                    date = item.get("date")
                    if date:
                        dates.append(str(date))
            if path.startswith(SITE_LIST_PREFIX):
                parsed = urlparse(path)
                date = parse_qs(parsed.query).get("date", [None])[0]
                if date:
                    dates.append(unquote(date))

        submit_date = str(self.submit_body().get("venues_date", ""))
        if submit_date:
            dates.append(submit_date)
        return _unique(dates)

    def venue_snapshot(self) -> VenueSnapshot:
        body = self.submit_body()
        courts = [
            CourtOption(site_id=int(item["site_id"]), site_name=str(item["site_name"])) for item in FIXED_COURTS
        ]

        submit_date = str(body.get("venues_date", ""))
        selected_times = [_time_from_submit_item(item, submit_date) for item in body.get("venues_site_time", [])]
        selected_site_id = selected_times and int(body["venues_site_time"][0].get("site_id")) or None
        selected_site_name = ""
        if body.get("venues_site_time"):
            selected_site_name = str(body["venues_site_time"][0].get("site_name", ""))

        times = [_time_from_fixed_item(item) for item in FIXED_TIME_SLOTS]

        return VenueSnapshot(
            venues_id=str(body.get("venues_id", "")),
            date=str(body.get("venues_date", "")),
            dates=self.available_dates(),
            courts=courts,
            times=times,
            selected_site_id=selected_site_id,
            selected_site_name=selected_site_name,
            fixed_courts=[
                CourtOption(site_id=selected_site_id, site_name=selected_site_name)
            ]
            if selected_site_id and selected_site_name
            else [],
            selected_times=selected_times,
        )

    def build_submit_request(self, params: dict) -> dict:
        body = copy.deepcopy(self.submit_body())
        headers = self.submit_headers()
        headers.update(params.get("headers") or {})

        venues_date = params.get("date") or body.get("venues_date")
        court = params.get("court") or _first_court(params) or {}
        time_slots = _limited_time_slots(params.get("time_slots") or [])
        if not time_slots:
            time_slots = _limited_time_slots(body.get("venues_site_time", []))

        site_id = court.get("site_id") or _first(time_slots, "site_id")
        site_name = court.get("site_name") or _first(time_slots, "site_name")

        body["venues_date"] = venues_date
        body["venues_site_time"] = [
            {
                "site_id": int(slot.get("site_id") or site_id),
                "site_name": str(slot.get("site_name") or site_name),
                "start_time": str(slot["start_time"]),
                "start_timestamp": shifted_timestamp(slot, venues_date, "start_timestamp"),
                "end_timestamp": shifted_timestamp(slot, venues_date, "end_timestamp"),
                "end_time": str(slot["end_time"]),
                "times": str(slot.get("times", "1")),
                "price": str(slot.get("price", "0")),
            }
            for slot in time_slots
        ]
        return {
            "method": self.submit_entry().get("req", {}).get("method", "POST"),
            "url": self.submit_entry().get("url"),
            "headers": headers,
            "body": body,
        }

    def build_submit_requests(self, params: dict) -> list[dict]:
        selections = params.get("selections") or []
        if selections:
            return self._build_selection_requests(params, selections)

        courts = params.get("courts") or []
        if not courts and params.get("court"):
            courts = [params["court"]]
        if not courts:
            courts = [_first_court(params)]
        dates = params.get("dates") or []
        if not dates and params.get("date"):
            dates = [params["date"]]
        if not dates:
            dates = [self.submit_body().get("venues_date", "") or default_date()]

        requests = []
        for date in dates:
            for court in courts:
                if court:
                    requests.append(self.build_submit_request({**params, "date": date, "court": court}))
        return requests

    def _build_selection_requests(self, params: dict, selections: list[dict]) -> list[dict]:
        dates = params.get("dates") or []
        if not dates and params.get("date"):
            dates = [params["date"]]
        if not dates:
            dates = [self.submit_body().get("venues_date", "") or default_date()]

        mode = params.get("request_mode", "single")
        groups = _selection_groups(selections, mode)
        requests = []
        for date in dates:
            for group in groups:
                court = group[0]["court"]
                time_slots = [item["time_slot"] for item in group]
                requests.append(self.build_submit_request({**params, "date": date, "court": court, "time_slots": time_slots}))
        return requests


def decode_json_body(section: dict) -> dict:
    encoded = section.get("base64")
    if not encoded:
        return {}
    decoded = base64.b64decode(encoded).decode("utf-8")
    return json.loads(decoded)


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked = {"host", "content-length", "accept-encoding", "connection", "priority"}
    return {
        key: value
        for key, value in headers.items()
        if key
        and value
        and not key.startswith(":")
        and key.lower() not in blocked
    }


def snapshot_to_dict(snapshot: VenueSnapshot) -> dict:
    return {
        "venues_id": snapshot.venues_id,
        "date": snapshot.date,
        "dates": snapshot.dates,
        "courts": [court.__dict__ for court in snapshot.courts],
        "times": [time.__dict__ for time in snapshot.times],
        "selected_site_id": snapshot.selected_site_id,
        "selected_site_name": snapshot.selected_site_name,
        "fixed_courts": [court.__dict__ for court in snapshot.fixed_courts],
        "selected_times": [time.__dict__ for time in snapshot.selected_times],
    }


def site_list_payload_to_dict(payload: dict | None, date: str = "") -> dict:
    if not isinstance(payload, dict):
        return {"date": unquote(str(date or "")), "items": []}
    date = unquote(str(date or ""))
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    items = []
    for court in data.get("list", []):
        court_info = {"site_id": court.get("site_id"), "site_name": court.get("site_name")}
        for slot in court.get("site_data", []):
            items.append(
                {
                    "court": court_info,
                    "time_slot": {
                        "start_time": str(slot.get("start_time", "")),
                        "end_time": str(slot.get("end_time", "")),
                        "start_timestamp": int(slot.get("start_timestamp", 0)),
                        "end_timestamp": int(slot.get("end_timestamp", 0)),
                        "price": str(slot.get("price", "0")),
                        "times": str(slot.get("times", "1")),
                        "source_date": date,
                    },
                    "status": slot.get("status"),
                    "available": str(slot.get("status")) == "2" and str(slot.get("times", "0")) != "0",
                    "disabled_desc": str(slot.get("disabled_desc", "")),
                    "disabled_reason": str(slot.get("disabled_reason", "")),
                    "member_name": str(slot.get("member_name", "")),
                    "mobile": str(slot.get("mobile", "")),
                }
            )
    return {"date": date, "items": items}


def site_list_snapshot_to_dict(entry: dict | None) -> dict:
    if not entry:
        return {"date": "", "items": []}
    parsed = urlparse(str(entry.get("path", "")))
    date = parse_qs(parsed.query).get("date", [""])[0]
    payload = decode_json_body(entry.get("res", {}))
    return site_list_payload_to_dict(payload, date)


def request_summary(request_data: dict) -> dict:
    headers = dict(request_data.get("headers") or {})
    if "wx-token" in headers and headers["wx-token"]:
        headers["wx-token"] = mask_secret(headers["wx-token"])
    return {
        "method": request_data.get("method"),
        "url": request_data.get("url"),
        "headers": headers,
        "body": request_data.get("body"),
    }


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _collect_times_for_site(site_data: dict, selected_site_id: int | None, source_date: str) -> list[TimeOption]:
    for court in site_data.get("list", []):
        if selected_site_id is not None and int(court.get("site_id", 0)) != selected_site_id:
            continue
        return [_time_from_site_item(item, source_date) for item in court.get("site_data", [])]
    return []


def _time_from_site_item(item: dict, source_date: str) -> TimeOption:
    return TimeOption(
        start_time=str(item.get("start_time", "")),
        end_time=str(item.get("end_time", "")),
        start_timestamp=int(item.get("start_timestamp", 0)),
        end_timestamp=int(item.get("end_timestamp", 0)),
        price=str(item.get("price", "0")),
        times=str(item.get("times", "1")),
        source_date=source_date,
    )


def _time_from_fixed_item(item: dict) -> TimeOption:
    return TimeOption(
        start_time=str(item.get("start_time", "")),
        end_time=str(item.get("end_time", "")),
        start_timestamp=int(item.get("start_timestamp", 0)),
        end_timestamp=int(item.get("end_timestamp", 0)),
        price=str(item.get("price", "0")),
        times=str(item.get("times", "1")),
        source_date=default_date(),
    )


def _time_from_submit_item(item: dict, source_date: str) -> TimeOption:
    return TimeOption(
        start_time=str(item.get("start_time", "")),
        end_time=str(item.get("end_time", "")),
        start_timestamp=int(item.get("start_timestamp", 0)),
        end_timestamp=int(item.get("end_timestamp", 0)),
        price=str(item.get("price", "0")),
        times=str(item.get("times", "1")),
        source_date=str(item.get("source_date", source_date)),
    )


def _first(items: list[dict], key: str) -> object:
    if not items:
        return None
    return items[0].get(key)


def _first_court(params: dict) -> dict | None:
    courts = params.get("courts") or []
    if courts:
        return courts[0]
    return params.get("court")


def _limited_time_slots(slots: list[dict]) -> list[dict]:
    return list(slots)[:2]


def _selection_groups(selections: list[dict], mode: str) -> list[list[dict]]:
    normalized = [item for item in selections if item.get("court") and item.get("time_slot")]
    normalized.sort(
        key=lambda item: (
            str(item["court"].get("site_id", "")),
            int(item["time_slot"].get("start_timestamp", 0)),
        )
    )
    if mode != "pair":
        return [[item] for item in normalized]

    groups: list[list[dict]] = []
    used: set[int] = set()
    for index, item in enumerate(normalized):
        if index in used:
            continue
        used.add(index)
        pair = [item]
        for other_index, other in enumerate(normalized):
            if other_index in used:
                continue
            same_court = str(item["court"].get("site_id")) == str(other["court"].get("site_id"))
            adjacent = int(item["time_slot"].get("end_timestamp", 0)) == int(other["time_slot"].get("start_timestamp", -1))
            if same_court and adjacent:
                pair.append(other)
                used.add(other_index)
                break
        groups.append(pair)
    return groups


def shifted_timestamp(slot: dict, target_date: str, key: str) -> int:
    original = int(slot[key])
    source_date = str(slot.get("source_date") or slot.get("date") or "")
    if not source_date:
        return original
    try:
        source = _parse_date(source_date)
        target = _parse_date(target_date)
    except ValueError:
        return original
    return original + int((target - source).total_seconds())


def _parse_date(value: str) -> datetime:
    normalized = value.replace("-", "/")
    return datetime.strptime(normalized, "%Y/%m/%d")


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _site_entry_date(entry: dict | None) -> str:
    if not entry:
        return ""
    parsed = urlparse(str(entry.get("path", "")))
    return parse_qs(parsed.query).get("date", [""])[0]
