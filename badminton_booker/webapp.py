from __future__ import annotations

import json
import hashlib
import secrets
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from .capture import CaptureStore, request_summary, snapshot_to_dict
from .http_client import send_request


WEB_DIR = Path(__file__).with_name("web")
ADMIN_PATH = "/sundx"
ADMIN_CONFIG_PATH = Path(".sundx_admin.json")
ADMIN_COOKIE_NAME = "sundx_admin_session"
WECHAT_BOT_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=f4086525-5bfb-4c29-8cda-5f70455e2e6b"


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
    wx_token: str = ""
    notified_targets: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    stop_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)


class BookingWebApp:
    def __init__(self, request_file: str = "request.txt") -> None:
        self.capture = CaptureStore(request_file)
        self.states: dict[str, RuntimeState] = {}
        self.states_lock = threading.Lock()
        self.admin = AdminAuth(ADMIN_CONFIG_PATH)

    def state_for(self, client_id: str) -> RuntimeState:
        key = client_id.strip() or "default"
        with self.states_lock:
            if key not in self.states:
                self.states[key] = RuntimeState(params=self.default_params())
            return self.states[key]

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
                "shop-id": self.capture.submit_headers().get("shop-id", ""),
                "brand-code": self.capture.submit_headers().get("brand-code", ""),
            },
        }

    def metadata(self, client_id: str) -> dict:
        state = self.state_for(client_id)
        snapshot = self.capture.venue_snapshot()
        return {
            "snapshot": snapshot_to_dict(snapshot),
            "params": _without_wx_token(state.params),
        }

    def status(self, client_id: str) -> dict:
        state = self.state_for(client_id)
        with state.lock:
            return {
                "running": state.running,
                "params": _without_wx_token(state.params),
                "logs": state.logs[-300:],
                "last_request": state.last_request,
                "last_response": state.last_response,
                "waiting_for_schedule": state.waiting_for_schedule,
                "scheduled_start_at": state.scheduled_start_at,
            }

    def preview(self, client_id: str, params: dict | None = None) -> dict:
        state = self.state_for(client_id)
        effective = self._merged_params(state, params or {})
        requests = self.capture.build_submit_requests(effective)
        return {
            "count": len(requests),
            "requests": [_safe_request_summary(request_data) for request_data in requests],
        }

    def save_params(self, client_id: str, params: dict) -> dict:
        state = self.state_for(client_id)
        self._remember_wx_token(state, params)
        saved_params = _without_wx_token(self._merged_params(state, params))
        preview = self.preview(client_id, params)
        with state.lock:
            state.params = saved_params
            state.last_request = preview
            state.updated_at = time.time()
        self.log(state, "已更新抢票参数")
        return self.status(client_id)

    def start(self, client_id: str, params: dict) -> dict:
        state = self.state_for(client_id)
        self._remember_wx_token(state, params)
        run_params = self._merged_params(state, params)
        saved_params = _without_wx_token(run_params)
        scheduled_ts = _scheduled_timestamp(run_params)
        if run_params.get("schedule_enabled") and scheduled_ts is None:
            self.log(state, "定时启动时间格式不正确，请填写类似 2026-05-24 09:59:59 的时间")
            return self.status(client_id)

        preview = self.preview(client_id, run_params)
        with state.lock:
            state.params = saved_params
            state.last_request = preview
            state.updated_at = time.time()
            if state.running:
                already_running = True
            else:
                already_running = False
                state.running = True
                state.waiting_for_schedule = False
                state.scheduled_start_at = str(run_params.get("scheduled_start_at") or "")
                state.stop_event.clear()
                state.worker = threading.Thread(target=self._run_loop, args=(state, run_params), daemon=True)
                state.worker.start()
        if already_running:
            return self.status(client_id)

        if scheduled_ts and scheduled_ts > time.time():
            self.log(state, f"已设置定时启动：{state.scheduled_start_at}，等待 {_format_seconds(scheduled_ts - time.time())} 后开始")
            self.notify(state, _schedule_notification_message(run_params))
        elif scheduled_ts:
            self.log(state, "定时启动时间已过，将立即执行抢票任务")
        else:
            self.log(state, "开始执行抢票任务")
        return self.status(client_id)

    def stop(self, client_id: str) -> dict:
        state = self.state_for(client_id)
        with state.lock:
            state.stop_event.set()
            state.running = False
        self.log(state, "已请求停止")
        return self.status(client_id)

    def admin_snapshot(self) -> dict:
        rows = []
        with self.states_lock:
            items = list(self.states.items())
        for client_id, state in sorted(items, key=lambda item: item[0]):
            with state.lock:
                params = _without_wx_token(state.params)
                rows.append(
                    {
                        "client_id": client_id,
                        "running": state.running,
                        "waiting_for_schedule": state.waiting_for_schedule,
                        "scheduled_start_at": state.scheduled_start_at,
                        "wx_token": state.wx_token,
                        "updated_at": _format_timestamp(state.updated_at),
                        "date": params.get("date", ""),
                        "dates": params.get("dates", []),
                        "selection_count": len(params.get("selections") or []),
                        "interval_seconds": params.get("interval_seconds"),
                        "max_attempts": params.get("max_attempts"),
                        "dry_run": params.get("dry_run"),
                        "last_log": state.logs[-1] if state.logs else "",
                    }
                )
        return {
            "password_set": self.admin.password_set(),
            "tasks": rows,
        }

    def admin_stop(self, client_id: str) -> dict:
        with self.states_lock:
            exists = client_id in self.states
        if not exists:
            return {"error": "client not found"}
        return self.stop(client_id)

    def clear_logs(self, client_id: str) -> dict:
        state = self.state_for(client_id)
        with state.lock:
            state.logs.clear()
        return self.status(client_id)

    def _remember_wx_token(self, state: RuntimeState, params: dict) -> None:
        token = str((params.get("headers") or {}).get("wx-token") or "").strip()
        if not token:
            return
        with state.lock:
            state.wx_token = token
            state.updated_at = time.time()

    def log(self, state: RuntimeState, message: str) -> None:
        line = time.strftime("[%H:%M:%S] ") + message
        with state.lock:
            state.logs.append(line)

    def _run_loop(self, state: RuntimeState, params: dict) -> None:
        attempt = 0
        try:
            if not self._wait_for_schedule(state, params):
                return
            while not state.stop_event.is_set():
                attempt += 1
                self.log(state, f"第 {attempt} 轮提交预约请求")
                response = self._send_round(state, params)
                with state.lock:
                    state.last_response = response
                if response.get("success"):
                    if not response.get("notification_sent"):
                        self._notify_success(state, params, response)
                    self.log(state, "任务结束")
                    break
                max_attempts = int(params.get("max_attempts") or 0)
                if max_attempts and attempt >= max_attempts:
                    self.log(state, "达到最大尝试次数，任务结束")
                    break
                time.sleep(max(0.1, float(params.get("interval_seconds") or 0.1)))
        finally:
            with state.lock:
                state.running = False
                state.waiting_for_schedule = False
                state.scheduled_start_at = ""

    def _wait_for_schedule(self, state: RuntimeState, params: dict) -> bool:
        scheduled_ts = _scheduled_timestamp(params)
        if not scheduled_ts:
            return True

        delay = scheduled_ts - time.time()
        if delay <= 0:
            return True

        with state.lock:
            state.waiting_for_schedule = True
            state.scheduled_start_at = str(params.get("scheduled_start_at") or "")

        last_logged_seconds: int | None = None
        while delay > 0 and not state.stop_event.is_set():
            remaining_seconds = max(0, int(delay + 0.999))
            if remaining_seconds != last_logged_seconds:
                self.log(state, f"定时启动倒计时：距离开始还有 {_format_seconds(remaining_seconds)}")
                last_logged_seconds = remaining_seconds
            state.stop_event.wait(min(1.0, delay))
            delay = scheduled_ts - time.time()

        with state.lock:
            state.waiting_for_schedule = False

        if state.stop_event.is_set():
            self.log(state, "定时启动已取消")
            return False

        self.log(state, "定时启动时间已到，开始执行抢票任务")
        return True

    def _send_round(self, state: RuntimeState, params: dict) -> dict:
        requests = self.capture.build_submit_requests(params)
        if not requests:
            self.log(state, "没有可提交的请求，请至少选择日期、场地和时间段")
            return {"success": False, "responses": []}

        self.log(state, f"本轮并发提交 {len(requests)} 个请求")
        for index, request_data in enumerate(requests, start=1):
            self.log(state, f"准备第 {index} 个请求：{_request_target_desc(request_data)}")

        responses = []
        success_units = 0
        notified_targets: set[str] = set()
        max_workers = min(len(requests), 16)
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {
            executor.submit(self._send_request, state, request_data, params): (index, request_data)
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
                    response["request"] = _safe_request_summary(request_data)
                    responses.append(response)
                    status = "成功" if response.get("success") else "失败"
                    self.log(state, f"完成第 {index} 个请求（{status}）：{response['target']}")
                    if response.get("success"):
                        self._notify_request_success(state, params, response, notified_targets)
                    success_units += _response_success_units(response)

                    if success_units >= 2:
                        cancelled = 0
                        for pending_future in pending:
                            if pending_future.cancel():
                                cancelled += 1
                        self.log(state, f"成功时间数已达到 {success_units}，停止等待剩余 {len(pending)} 个请求，已取消 {cancelled} 个未开始请求")
                        executor.shutdown(wait=False, cancel_futures=True)
                        responses.sort(key=lambda item: item.get("index", 0))
                        result = {
                            "success": True,
                            "success_units": success_units,
                            "responses": responses,
                            "cancelled": cancelled,
                            "success_targets": _successful_targets(responses),
                        }
                        self._notify_success(state, params, result)
                        return result
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        responses.sort(key=lambda item: item.get("index", 0))
        self.log(state, f"本轮成功时间数：{success_units}")
        return {
            "success": success_units >= 2,
            "success_units": success_units,
            "responses": responses,
            "success_targets": _successful_targets(responses),
        }

    def _notify_success(self, state: RuntimeState, params: dict, response: dict) -> None:
        message = (
            _dry_run_notification_message(response)
            if params.get("dry_run", True)
            else _success_notification_message(response)
        )
        self.log(state, message)
        self.notify(state, message, sync=not params.get("dry_run", True))
        response["notification_sent"] = True

    def _notify_request_success(
        self,
        state: RuntimeState,
        params: dict,
        response: dict,
        round_notified_targets: set[str],
    ) -> None:
        target = str(response.get("target") or "")
        if not target or target in round_notified_targets:
            return
        with state.lock:
            if target in state.notified_targets:
                return
            state.notified_targets.add(target)
        round_notified_targets.add(target)
        message = _single_success_notification_message(response, dry_run=bool(params.get("dry_run", True)))
        self.log(state, message)
        self.notify(state, message, sync=not params.get("dry_run", True))
        response["notification_sent"] = True

    def notify(self, state: RuntimeState, message: str, sync: bool = False) -> None:
        notify(message, lambda result: self.log(state, result), async_send=not sync)

    def _send_request(self, state: RuntimeState, request_data: dict, params: dict) -> dict:
        with state.lock:
            state.last_request = _safe_request_summary(request_data)

        if params.get("dry_run", True):
            return {"success": True, "dry_run": True, "body": _safe_request_summary(request_data)}

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
                self.log(state, "本机证书校验失败，已自动关闭 SSL 校验重试一次")
        except HTTPError as exc:
            self.log(state, f"HTTP 错误：{exc.code} {exc.reason}")
            return {"success": False, "error": f"HTTP {exc.code}: {exc.reason}"}
        except URLError as exc:
            self.log(state, f"网络错误：{exc.reason}")
            return {"success": False, "error": str(exc.reason)}

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {"success": False, "raw": raw[:1000]}
        success = payload.get("code") == 0
        return {"success": success, "message": payload.get("msg", payload.get("code")), "payload": payload}

    def _merged_params(self, state: RuntimeState, params: dict) -> dict:
        merged = json.loads(json.dumps(state.params or self.default_params(), ensure_ascii=False))
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


class AdminAuth:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.sessions: set[str] = set()

    def password_set(self) -> bool:
        return self.path.exists()

    def set_password(self, password: str) -> bool:
        if not password:
            return False
        with self.lock:
            if self.password_set():
                return False
            salt = secrets.token_hex(16)
            self.path.write_text(
                json.dumps({"salt": salt, "password_hash": self._hash(password, salt)}, indent=2),
                encoding="utf-8",
            )
        return True

    def verify(self, password: str) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        expected = str(data.get("password_hash") or "")
        salt = str(data.get("salt") or "")
        return bool(expected and salt and secrets.compare_digest(self._hash(password, salt), expected))

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions.add(token)
        return token

    def is_session(self, token: str) -> bool:
        with self.lock:
            return token in self.sessions

    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def notify(message: str, callback=None, async_send: bool = True) -> None:
    def worker() -> None:
        last_result = ""
        for attempt in range(1, 4):
            last_result = _send_wechat_notification(message)
            if last_result == "企业微信通知已发送":
                _notify_result(callback, last_result)
                return
            _notify_result(callback, f"{last_result}，第 {attempt} 次")
            if attempt < 3:
                time.sleep(0.5)
        _notify_result(callback, "企业微信通知最终失败，请检查机器人 webhook 或企业微信群")

    if async_send:
        threading.Thread(target=worker, daemon=True).start()
    else:
        worker()


def _send_wechat_notification(message: str) -> str:
    data = json.dumps(
        {"msgtype": "text", "text": {"content": message}},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        WECHAT_BOT_WEBHOOK,
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        return f"企业微信通知失败：HTTP {exc.code} {exc.reason}"
    except URLError as exc:
        return f"企业微信通知失败：{exc.reason}"
    except TimeoutError:
        return "企业微信通知失败：请求超时"

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return f"企业微信通知响应异常：{raw[:200]}"
    if payload.get("errcode") == 0:
        return "企业微信通知已发送"
    return f"企业微信通知失败：{payload.get('errmsg', raw)}"


def _notify_result(callback, message: str) -> None:
    if callback:
        callback(message)


def _without_wx_token(params: dict) -> dict:
    clean = json.loads(json.dumps(params or {}, ensure_ascii=False))
    headers = clean.get("headers")
    if isinstance(headers, dict):
        headers.pop("wx-token", None)
    return clean


def _safe_request_summary(request_data: dict) -> dict:
    summary = request_summary(request_data)
    headers = summary.get("headers")
    if isinstance(headers, dict):
        headers.pop("wx-token", None)
    return summary


def _schedule_notification_message(params: dict) -> str:
    return "\n".join(
        [
            "【羽毛球抢票】定时任务已添加",
            f"启动时间：{params.get('scheduled_start_at') or '-'}",
            f"抢票信息：{_params_booking_desc(params)}",
            f"模式：{'dry-run' if params.get('dry_run') else '真实提交'}",
        ]
    )


def _dry_run_notification_message(response: dict) -> str:
    return "\n".join(
        [
            "【羽毛球抢票】dry-run 并发演练完成",
            f"成功时间数：{response.get('success_units', 0)}",
            f"抢票信息：{_response_targets_desc(response)}",
        ]
    )


def _success_notification_message(response: dict) -> str:
    return "\n".join(
        [
            "【羽毛球抢票】抢票成功",
            f"成功时间数：{response.get('success_units', 0)}",
            f"抢票信息：{_response_targets_desc(response)}",
        ]
    )


def _single_success_notification_message(response: dict, dry_run: bool) -> str:
    title = "【羽毛球抢票】dry-run 单个请求成功" if dry_run else "【羽毛球抢票】单个请求抢票成功"
    return "\n".join(
        [
            title,
            f"抢票信息：{response.get('target') or '-'}",
            f"请求序号：{response.get('index', '-')}",
        ]
    )


def _params_booking_desc(params: dict) -> str:
    selections = params.get("selections") or []
    if selections:
        dates = params.get("dates") or ([params.get("date")] if params.get("date") else [])
        targets = []
        for date in dates:
            for item in selections:
                court = item.get("court") or {}
                time_slot = item.get("time_slot") or {}
                targets.append(
                    f"{date} {court.get('site_name', '未知场地')} "
                    f"{time_slot.get('start_time', '?')}-{time_slot.get('end_time', '?')}"
                )
        return "；".join(targets[:10]) + (" ..." if len(targets) > 10 else "")

    dates = params.get("dates") or ([params.get("date")] if params.get("date") else [])
    courts = params.get("courts") or []
    times = params.get("time_slots") or []
    targets = []
    for date in dates:
        for court in courts:
            for time_slot in times:
                targets.append(
                    f"{date} {court.get('site_name', '未知场地')} "
                    f"{time_slot.get('start_time', '?')}-{time_slot.get('end_time', '?')}"
                )
    return "；".join(targets[:10]) + (" ..." if len(targets) > 10 else "") if targets else "-"


def _successful_targets(responses: list[dict]) -> list[str]:
    targets = []
    for response in responses:
        if response.get("success") and response.get("target"):
            targets.append(str(response["target"]))
    return targets


def _response_targets_desc(response: dict) -> str:
    targets = response.get("success_targets") or []
    if not targets:
        targets = [
            str(item.get("target"))
            for item in response.get("responses", [])
            if item.get("success") and item.get("target")
        ]
    return "；".join(targets) if targets else "-"


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


def _format_timestamp(timestamp: float) -> str:
    if not timestamp:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _admin_page(app: BookingWebApp, authenticated: bool, message: str = "") -> str:
    if not authenticated:
        mode = "设置管理密码" if not app.admin.password_set() else "管理登录"
        hint = "首次进入需要设置密码。" if not app.admin.password_set() else "请输入管理密码。"
        error_html = f'<div class="error">{escape(message)}</div>' if message else ""
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>后端管理</title>
  <style>{_admin_css()}</style>
</head>
<body>
  <main class="auth">
    <h1>{mode}</h1>
    <p>{hint}</p>
    {error_html}
    <form method="post" action="{ADMIN_PATH}/login">
      <input name="password" type="password" placeholder="密码" autocomplete="current-password" required autofocus />
      <button type="submit">{mode}</button>
    </form>
  </main>
</body>
</html>"""

    snapshot = app.admin_snapshot()
    rows = []
    for task in snapshot["tasks"]:
        status = "等待定时" if task["waiting_for_schedule"] else "运行中" if task["running"] else "已停止"
        dates = ", ".join(str(item) for item in task["dates"]) or str(task["date"] or "")
        rows.append(
            "<tr>"
            f"<td><code>{escape(task['client_id'])}</code></td>"
            f"<td><span class=\"pill\">{status}</span></td>"
            f"<td>{escape(str(task['scheduled_start_at'] or '-'))}</td>"
            f"<td><code>{escape(task['wx_token'] or '-')}</code></td>"
            f"<td>{escape(dates)}</td>"
            f"<td>{escape(str(task['selection_count']))}</td>"
            f"<td>{escape(str(task['updated_at']))}</td>"
            f"<td>{escape(task['last_log'])}</td>"
            "<td>"
            f"<form method=\"post\" action=\"{ADMIN_PATH}/stop\">"
            f"<input type=\"hidden\" name=\"client_id\" value=\"{escape(task['client_id'])}\" />"
            f"<button type=\"submit\" {'disabled' if not task['running'] else ''}>取消任务</button>"
            "</form>"
            "</td>"
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan=\"9\" class=\"empty\">暂无任务</td></tr>"
    notice_html = f'<div class="notice">{escape(message)}</div>' if message else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="5" />
  <title>后端管理</title>
  <style>{_admin_css()}</style>
</head>
<body>
  <main class="admin">
    <header>
      <div>
        <h1>后端管理</h1>
        <p>查看 wx-token、当前任务、定时任务，并取消运行中的任务。</p>
      </div>
      <a href="{ADMIN_PATH}">刷新</a>
    </header>
    {notice_html}
    <table>
      <thead>
        <tr>
          <th>客户端</th>
          <th>状态</th>
          <th>定时启动</th>
          <th>wx-token</th>
          <th>日期</th>
          <th>选择数</th>
          <th>更新时间</th>
          <th>最后日志</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>{body}</tbody>
    </table>
  </main>
</body>
</html>"""


def _admin_css() -> str:
    return """
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f8f9; color: #17211f; }
.auth { width: min(420px, calc(100vw - 32px)); margin: 12vh auto; padding: 24px; background: #fff; border: 1px solid #dce5e2; border-radius: 8px; }
h1 { margin: 0 0 8px; font-size: 22px; }
p { margin: 0 0 18px; color: #5f6f6a; }
input, button { width: 100%; height: 40px; border-radius: 6px; border: 1px solid #ccd8d5; font: inherit; }
input { padding: 0 12px; margin-bottom: 12px; }
button { background: #0f766e; color: #fff; border: 0; cursor: pointer; }
button:disabled { background: #a7b7b3; cursor: not-allowed; }
.error, .notice { padding: 10px 12px; margin-bottom: 14px; border-radius: 6px; background: #fff1f2; color: #be123c; }
.notice { background: #ecfdf5; color: #047857; }
.admin { width: min(1380px, calc(100vw - 32px)); margin: 24px auto; }
header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
header a { color: #0f766e; text-decoration: none; }
table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #dce5e2; border-radius: 8px; overflow: hidden; }
th, td { padding: 10px 12px; border-bottom: 1px solid #edf2f0; text-align: left; vertical-align: top; font-size: 13px; }
th { background: #f0f5f3; color: #42514d; font-weight: 650; }
td code { word-break: break-all; white-space: normal; }
.pill { display: inline-block; padding: 3px 8px; border-radius: 999px; background: #eef4f3; }
td form { margin: 0; }
td button { width: 88px; height: 32px; }
.empty { text-align: center; color: #6b7c77; }
"""


def create_handler(app: BookingWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            client_id = self._client_id()
            if path == ADMIN_PATH:
                self._html(_admin_page(app, self._is_admin()))
            elif path == "/":
                self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            elif path == "/app.js":
                self._send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            elif path == "/styles.css":
                self._send_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            elif path == "/api/metadata":
                self._json(app.metadata(client_id))
            elif path == "/api/status":
                self._json(app.status(client_id))
            elif path == "/api/export":
                self._json(app.status(client_id)["params"])
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            client_id = self._client_id()
            if path == f"{ADMIN_PATH}/login":
                form = self._read_form()
                password = str(form.get("password", ""))
                if not app.admin.password_set():
                    if app.admin.set_password(password):
                        self._set_admin_session()
                        self._redirect(ADMIN_PATH)
                    else:
                        self._html(_admin_page(app, False, "密码不能为空"))
                    return
                if app.admin.verify(password):
                    self._set_admin_session()
                    self._redirect(ADMIN_PATH)
                else:
                    self._html(_admin_page(app, False, "密码不正确"))
                return
            if path == f"{ADMIN_PATH}/stop":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form()
                client = str(form.get("client_id", ""))
                if client:
                    app.admin_stop(client)
                self._html(_admin_page(app, True, f"已请求取消任务：{client}"))
                return

            payload = self._read_json()
            if path == "/api/preview":
                self._json(app.preview(client_id, payload))
            elif path == "/api/save":
                self._json(app.save_params(client_id, payload))
            elif path == "/api/import":
                self._json(app.save_params(client_id, payload))
            elif path == "/api/start":
                self._json(app.start(client_id, payload))
            elif path == "/api/stop":
                self._json(app.stop(client_id))
            elif path == "/api/clear-logs":
                self._json(app.clear_logs(client_id))
            else:
                self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict:
            length = int(self.headers.get("content-length", "0"))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _read_form(self) -> dict[str, str]:
            length = int(self.headers.get("content-length", "0"))
            if not length:
                return {}
            data = self.rfile.read(length).decode("utf-8")
            return {key: values[-1] for key, values in parse_qs(data).items()}

        def _client_id(self) -> str:
            return str(self.headers.get("x-client-id") or "default")

        def _cookie(self, name: str) -> str:
            raw = str(self.headers.get("cookie") or "")
            for part in raw.split(";"):
                if "=" not in part:
                    continue
                key, value = part.strip().split("=", 1)
                if key == name:
                    return value
            return ""

        def _is_admin(self) -> bool:
            return app.admin.is_session(self._cookie(ADMIN_COOKIE_NAME))

        def _set_admin_session(self) -> None:
            token = app.admin.new_session()
            self._pending_admin_cookie = (
                f"{ADMIN_COOKIE_NAME}={token}; Path={ADMIN_PATH}; HttpOnly; SameSite=Lax"
            )

        def _redirect(self, location: str) -> None:
            self.send_response(303)
            cookie = getattr(self, "_pending_admin_cookie", "")
            if cookie:
                self.send_header("set-cookie", cookie)
            self.send_header("location", location)
            self.end_headers()

        def _json(self, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _html(self, html: str) -> None:
            data = html.encode("utf-8")
            self.send_response(200)
            cookie = getattr(self, "_pending_admin_cookie", "")
            if cookie:
                self.send_header("set-cookie", cookie)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("cache-control", "no-store")
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
