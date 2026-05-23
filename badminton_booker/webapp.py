from __future__ import annotations

import json
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from .capture import CaptureStore, request_summary, snapshot_to_dict
from .http_client import send_request


WEB_DIR = Path(__file__).with_name("web")


@dataclass
class RuntimeState:
    params: dict = field(default_factory=dict)
    running: bool = False
    logs: list[str] = field(default_factory=list)
    last_request: dict | None = None
    last_response: dict | None = None
    worker: threading.Thread | None = None
    waiting_for_schedule: bool = False
    scheduled_start_at: str = ""
    stop_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


class BookingWebApp:
    def __init__(self, request_file: str = "request.txt") -> None:
        self.capture = CaptureStore(request_file)
        self.state = RuntimeState(params=self.default_params())

    def default_params(self) -> dict:
        snapshot = self.capture.venue_snapshot()
        return {
            "request_file": str(self.capture.path),
            "dry_run": False,
            "verify_ssl": False,
            "interval_seconds": 0.1,
            "max_attempts": 100000,
            "schedule_enabled": False,
            "scheduled_start_at": "",
            "date": snapshot.date,
            "dates": [snapshot.date],
            "courts": [
                {
                    "site_id": snapshot.selected_site_id,
                    "site_name": snapshot.selected_site_name,
                }
            ],
            "time_slots": [time.__dict__ for time in snapshot.selected_times],
            "headers": {
                "wx-token": self.capture.submit_headers().get("wx-token", ""),
                "shop-id": self.capture.submit_headers().get("shop-id", ""),
                "brand-code": self.capture.submit_headers().get("brand-code", ""),
            },
        }

    def metadata(self) -> dict:
        snapshot = self.capture.venue_snapshot()
        return {
            "snapshot": snapshot_to_dict(snapshot),
            "params": self.state.params,
        }

    def status(self) -> dict:
        with self.state.lock:
            return {
                "running": self.state.running,
                "params": self.state.params,
                "logs": self.state.logs[-300:],
                "last_request": self.state.last_request,
                "last_response": self.state.last_response,
                "waiting_for_schedule": self.state.waiting_for_schedule,
                "scheduled_start_at": self.state.scheduled_start_at,
            }

    def preview(self, params: dict | None = None) -> dict:
        effective = self._merged_params(params or {})
        requests = self.capture.build_submit_requests(effective)
        return {
            "count": len(requests),
            "requests": [request_summary(request_data) for request_data in requests],
        }

    def save_params(self, params: dict) -> dict:
        with self.state.lock:
            self.state.params = self._merged_params(params)
            self.state.last_request = self.preview(self.state.params)
        self.log("已更新抢票参数")
        return self.status()

    def start(self, params: dict) -> dict:
        self.save_params(params)
        scheduled_ts = _scheduled_timestamp(self.state.params)
        if self.state.params.get("schedule_enabled") and scheduled_ts is None:
            self.log("定时启动时间格式不正确，请填写类似 2026-05-24 09:59:59 的时间")
            return self.status()
        with self.state.lock:
            if self.state.running:
                already_running = True
            else:
                already_running = False
                self.state.running = True
                self.state.waiting_for_schedule = False
                self.state.scheduled_start_at = str(self.state.params.get("scheduled_start_at") or "")
                self.state.stop_event.clear()
                self.state.worker = threading.Thread(target=self._run_loop, daemon=True)
                self.state.worker.start()
        if already_running:
            return self.status()

        if scheduled_ts and scheduled_ts > time.time():
            self.log(f"已设置定时启动：{self.state.scheduled_start_at}，等待 {_format_seconds(scheduled_ts - time.time())} 后开始")
        elif scheduled_ts:
            self.log("定时启动时间已过，将立即执行抢票任务")
        else:
            self.log("开始执行抢票任务")
        return self.status()

    def stop(self) -> dict:
        with self.state.lock:
            self.state.stop_event.set()
            self.state.running = False
        self.log("已请求停止")
        return self.status()

    def clear_logs(self) -> dict:
        with self.state.lock:
            self.state.logs.clear()
        return self.status()

    def log(self, message: str) -> None:
        line = time.strftime("[%H:%M:%S] ") + message
        with self.state.lock:
            self.state.logs.append(line)

    def _run_loop(self) -> None:
        attempt = 0
        try:
            if not self._wait_for_schedule():
                return
            while not self.state.stop_event.is_set():
                attempt += 1
                params = self.state.params
                self.log(f"第 {attempt} 轮提交预约请求")
                response = self._send_round(params)
                with self.state.lock:
                    self.state.last_response = response
                if response.get("success"):
                    self.log("dry-run 并发演练完成，任务结束" if params.get("dry_run", True) else "本轮存在成功请求，任务结束")
                    break
                max_attempts = int(params.get("max_attempts") or 0)
                if max_attempts and attempt >= max_attempts:
                    self.log("达到最大尝试次数，任务结束")
                    break
                time.sleep(max(0.1, float(params.get("interval_seconds") or 0.1)))
        finally:
            with self.state.lock:
                self.state.running = False
                self.state.waiting_for_schedule = False
                self.state.scheduled_start_at = ""

    def _wait_for_schedule(self) -> bool:
        params = self.state.params
        scheduled_ts = _scheduled_timestamp(params)
        if not scheduled_ts:
            return True

        delay = scheduled_ts - time.time()
        if delay <= 0:
            return True

        with self.state.lock:
            self.state.waiting_for_schedule = True
            self.state.scheduled_start_at = str(params.get("scheduled_start_at") or "")

        last_logged_seconds: int | None = None
        while delay > 0 and not self.state.stop_event.is_set():
            remaining_seconds = max(0, int(delay + 0.999))
            if remaining_seconds != last_logged_seconds:
                self.log(f"定时启动倒计时：距离开始还有 {_format_seconds(remaining_seconds)}")
                last_logged_seconds = remaining_seconds
            self.state.stop_event.wait(min(1.0, delay))
            delay = scheduled_ts - time.time()

        with self.state.lock:
            self.state.waiting_for_schedule = False

        if self.state.stop_event.is_set():
            self.log("定时启动已取消")
            return False

        self.log("定时启动时间已到，开始执行抢票任务")
        return True

    def _send_round(self, params: dict) -> dict:
        requests = self.capture.build_submit_requests(params)
        if not requests:
            self.log("没有可提交的请求，请至少选择日期、场地和时间段")
            return {"success": False, "responses": []}

        self.log(f"本轮并发提交 {len(requests)} 个请求")
        for index, request_data in enumerate(requests, start=1):
            self.log(f"准备第 {index} 个请求：{_request_target_desc(request_data)}")

        responses = []
        success_units = 0
        max_workers = min(len(requests), 16)
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {
            executor.submit(self._send_request, request_data, params): (index, request_data)
            for index, request_data in enumerate(requests, start=1)
        }
        pending = set(futures)
        try:
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda item: futures[item][0]):
                    index, request_data = futures[future]
                    response = future.result()
                    response["index"] = index
                    response["target"] = _request_target_desc(request_data)
                    response["request"] = request_summary(request_data)
                    responses.append(response)
                    status = "成功" if response.get("success") else "失败"
                    self.log(f"完成第 {index} 个请求（{status}）：{response['target']}")
                    success_units += _response_success_units(response)

                    if success_units >= 2:
                        cancelled = 0
                        for pending_future in pending:
                            if pending_future.cancel():
                                cancelled += 1
                        self.log(f"成功时间数已达到 {success_units}，停止等待剩余 {len(pending)} 个请求，已取消 {cancelled} 个未开始请求")
                        executor.shutdown(wait=False, cancel_futures=True)
                        responses.sort(key=lambda item: item.get("index", 0))
                        return {"success": True, "success_units": success_units, "responses": responses, "cancelled": cancelled}
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        responses.sort(key=lambda item: item.get("index", 0))
        self.log(f"本轮成功时间数：{success_units}")
        return {"success": success_units >= 2, "success_units": success_units, "responses": responses}

    def _send_request(self, request_data: dict, params: dict) -> dict:
        with self.state.lock:
            self.state.last_request = request_summary(request_data)

        if params.get("dry_run", True):
            return {"success": True, "dry_run": True, "body": request_summary(request_data)}

        data = json.dumps(request_data["body"], ensure_ascii=False).encode("utf-8")
        request = Request(
            request_data["url"],
            data=data,
            headers=request_data["headers"],
            method=request_data["method"],
        )
        try:
            raw, ssl_fallback = send_request(request, timeout=10, verify_ssl=params.get("verify_ssl", True))
            if ssl_fallback:
                self.log("本机证书校验失败，已自动关闭 SSL 校验重试一次")
        except HTTPError as exc:
            self.log(f"HTTP 错误：{exc.code} {exc.reason}")
            return {"success": False, "error": f"HTTP {exc.code}: {exc.reason}"}
        except URLError as exc:
            self.log(f"网络错误：{exc.reason}")
            return {"success": False, "error": str(exc.reason)}

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {"success": False, "raw": raw[:1000]}
        success = payload.get("code") == 0
        return {"success": success, "message": payload.get("msg", payload.get("code")), "payload": payload}

    def _merged_params(self, params: dict) -> dict:
        merged = json.loads(json.dumps(self.state.params or self.default_params(), ensure_ascii=False))
        for key, value in params.items():
            if key == "headers":
                merged.setdefault("headers", {}).update(value or {})
            elif key == "time_slots":
                merged[key] = list(value or [])[:2]
            elif key == "dates":
                merged[key] = _unique_dates(value or [])
            else:
                merged[key] = value
        return merged


def _request_target_desc(request_data: dict) -> str:
    body = request_data.get("body", {})
    date = body.get("venues_date", "未知日期")
    slots = body.get("venues_site_time", [])
    if not slots:
        return f"{date} 未知场地 未知时间"
    court_name = str(slots[0].get("site_name", "未知场地"))
    time_ranges = ", ".join(
        f"{slot.get('start_time', '?')}-{slot.get('end_time', '?')}" for slot in slots
    )
    return f"{date} {court_name} {time_ranges}"


def _request_slot_keys(request_data: dict) -> list[str]:
    body = request_data.get("body", {})
    date = str(body.get("venues_date", ""))
    keys = []
    for slot in body.get("venues_site_time", []):
        keys.append(
            "|".join(
                [
                    date,
                    str(slot.get("site_id", "")),
                    str(slot.get("start_time", "")),
                    str(slot.get("end_time", "")),
                ]
            )
        )
    return keys


def _response_success_units(response: dict) -> int:
    if not response.get("success"):
        return 0
    body = response.get("request", {}).get("body")
    if body is None and response.get("dry_run"):
        body = response.get("body", {}).get("body")
    slots = body.get("venues_site_time", []) if isinstance(body, dict) else []
    if slots:
        return len(slots)
    return 1


def _unique_dates(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _scheduled_timestamp(params: dict) -> float | None:
    if not params.get("schedule_enabled"):
        return None
    raw = str(params.get("scheduled_start_at") or "").strip()
    if not raw:
        return None
    try:
        return _parse_scheduled_datetime(raw).timestamp()
    except ValueError:
        return None


def _parse_scheduled_datetime(value: str) -> datetime:
    text = value.strip().replace("T", " ")
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ]
    if len(text.split()) == 1 and ":" in text:
        today = datetime.now().strftime("%Y-%m-%d")
        text = f"{today} {text}"
    for fmt in formats + ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid scheduled datetime: {value}")


def _format_seconds(seconds: float) -> str:
    remaining = max(0, int(seconds))
    hours, remainder = divmod(remaining, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def create_handler(app: BookingWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            elif path == "/app.js":
                self._send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            elif path == "/styles.css":
                self._send_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            elif path == "/api/metadata":
                self._json(app.metadata())
            elif path == "/api/status":
                self._json(app.status())
            elif path == "/api/export":
                self._json(app.state.params)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            payload = self._read_json()
            if path == "/api/preview":
                self._json(app.preview(payload))
            elif path == "/api/save":
                self._json(app.save_params(payload))
            elif path == "/api/import":
                self._json(app.save_params(payload))
            elif path == "/api/start":
                self._json(app.start(payload))
            elif path == "/api/stop":
                self._json(app.stop())
            elif path == "/api/clear-logs":
                self._json(app.clear_logs())
            else:
                self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict:
            length = int(self.headers.get("content-length", "0"))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _json(self, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path: Path, content_type: str) -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def run_server(host: str = "127.0.0.1", port: int = 8765, request_file: str = "request.txt") -> None:
    app = BookingWebApp(request_file)
    server = ThreadingHTTPServer((host, port), create_handler(app))
    print(f"Web console running at http://{host}:{port}")
    server.serve_forever()
