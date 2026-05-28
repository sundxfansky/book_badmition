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

from .capture import (
    CaptureStore,
    request_summary,
    site_list_payload_to_dict,
    site_list_snapshot_to_dict,
    snapshot_to_dict,
)
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
        self.backends: list[dict] = self._load_backends()
        self._backend_clients: dict[str, BackendClient] = {}

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
            "monitor_enabled": False,
            "monitor_date": snapshot.date,
            "monitor_interval_seconds": 20,
            "monitor_selections": [],
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
            "site_list_snapshot": site_list_snapshot_to_dict(self.capture.latest_site_list_entry()),
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
        if effective.get("monitor_enabled"):
            dates = _monitor_dates(effective)
            status_requests = [
                _safe_request_summary(self.capture.build_site_list_request(effective, date))
                for date in dates
            ]
            submit_requests = [
                _safe_request_summary(
                    self.capture.build_submit_request(
                        {
                            **effective,
                            "date": item["date"],
                            "court": item["court"],
                            "time_slots": [item["time_slot"]],
                        }
                    )
                )
                for item in _monitor_targets(effective)
            ]
            return {
                "mode": "monitor",
                "count": len(status_requests),
                "requests": status_requests,
                "monitor_targets": [_monitor_target_desc(item) for item in _monitor_targets(effective)],
                "submit_requests_when_released": submit_requests,
            }
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
        self.log(state, "已更新抢票配置")
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
                        "params": params,
                        "selection_count": _selection_count(params),
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

    def admin_task_export(self, client_id: str) -> dict:
        state = self._state_by_id(client_id)
        if not state:
            return {"error": "client not found"}
        with state.lock:
            return {
                "client_id": client_id,
                "exported_at": _format_timestamp(time.time()),
                "params": _with_wx_token(state.params, state.wx_token),
            }

    def admin_all_export(self) -> dict:
        with self.states_lock:
            client_ids = sorted(self.states)
        return {
            "exported_at": _format_timestamp(time.time()),
            "tasks": [self.admin_task_export(client_id) for client_id in client_ids],
        }

    def admin_stop(self, client_id: str) -> dict:
        with self.states_lock:
            exists = client_id in self.states
        if not exists:
            return {"error": "client not found"}
        return self.stop(client_id)

    def admin_start(self, client_id: str) -> dict:
        state = self._state_by_id(client_id)
        if not state:
            return {"error": "client not found"}
        with state.lock:
            params = _with_wx_token(state.params, state.wx_token)
        return self.start(client_id, params)

    def admin_update(self, form: dict[str, list[str]]) -> dict:
        client_id = _form_value(form, "client_id", "default").strip() or "default"
        state = self.state_for(client_id)
        current = self._merged_params(state, {})
        token = _form_value(form, "wx_token", "").strip()
        with state.lock:
            state.wx_token = token
        params = _admin_form_to_params(self.capture.venue_snapshot(), current, form, token)
        return self.save_params(client_id, params)

    def admin_import(self, payload_text: str, fallback_client_id: str = "") -> dict:
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            return {"error": f"JSON 格式错误：{exc}"}

        if isinstance(payload, dict) and isinstance(payload.get("tasks"), list):
            entries = payload["tasks"]
        else:
            entries = [payload]

        imported = []
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue
            params = entry.get("params") if isinstance(entry.get("params"), dict) else entry
            client_id = str(entry.get("client_id") or fallback_client_id or f"imported-{index}").strip()
            if not client_id or not isinstance(params, dict):
                continue
            state = self.state_for(client_id)
            headers = params.get("headers") if isinstance(params.get("headers"), dict) else {}
            if "wx-token" in headers:
                with state.lock:
                    state.wx_token = str(headers.get("wx-token") or "").strip()
            self.save_params(client_id, params)
            imported.append(client_id)

        if not imported:
            return {"error": "没有找到可导入的任务参数"}
        return {"imported": imported}

    def admin_import_action_message(self, imported: list[str], action: str) -> str:
        if action == "start":
            for client_id in imported:
                self.admin_start(client_id)
            return f"已导入并开启任务：{', '.join(imported)}"
        if action == "stop":
            for client_id in imported:
                self.admin_stop(client_id)
            return f"已导入并停止任务：{', '.join(imported)}"
        return f"已导入任务：{', '.join(imported)}"

    def _load_backends(self) -> list[dict]:
        try:
            data = json.loads(ADMIN_CONFIG_PATH.read_text(encoding="utf-8"))
            return data.get("backends") or []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_backends(self) -> None:
        try:
            data = json.loads(ADMIN_CONFIG_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data["backends"] = self.backends
        ADMIN_CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_backend_client(self, backend_id: str) -> BackendClient | None:
        if backend_id in self._backend_clients:
            return self._backend_clients[backend_id]
        for b in self.backends:
            if b.get("id") == backend_id:
                client = BackendClient(b)
                self._backend_clients[backend_id] = client
                return client
        return None

    def admin_add_backend(self, name: str, url: str, password: str) -> dict:
        url = url.strip().rstrip("/")
        name = name.strip()
        if not url or not password:
            return {"error": "地址和密码不能为空"}
        backend_id = hashlib.md5(url.encode()).hexdigest()[:8]
        for b in self.backends:
            if b.get("id") == backend_id:
                return {"error": "该节点已存在"}
        backend = {"id": backend_id, "name": name or url, "url": url, "password": password}
        self.backends.append(backend)
        self._save_backends()
        return {"success": True, "id": backend_id}

    def admin_remove_backend(self, backend_id: str) -> dict:
        self.backends = [b for b in self.backends if b.get("id") != backend_id]
        self._backend_clients.pop(backend_id, None)
        self._save_backends()
        return {"success": True}

    def admin_test_backend(self, backend_id: str) -> dict:
        client = self._get_backend_client(backend_id)
        if not client:
            return {"error": "节点不存在"}
        if client.login():
            snapshot = client.snapshot()
            if snapshot:
                task_count = len(snapshot.get("tasks") or [])
                return {"success": True, "message": f"连接成功，{task_count} 个任务"}
            return {"error": "登录成功但获取数据失败"}
        return {"error": "连接失败或密码错误"}

    def admin_full_snapshot(self) -> dict:
        local = self.admin_snapshot()
        local["backend_id"] = "local"
        local["backend_name"] = "本地"
        remotes = []
        futures_map = {}
        with ThreadPoolExecutor(max_workers=max(1, len(self.backends))) as executor:
            for backend in self.backends:
                client = self._get_backend_client(backend["id"])
                if client:
                    future = executor.submit(client.snapshot)
                    futures_map[future] = backend
            for future in futures_map:
                backend = futures_map[future]
                try:
                    snapshot = future.result(timeout=5)
                    if snapshot:
                        snapshot["backend_id"] = backend["id"]
                        snapshot["backend_name"] = backend.get("name", backend["url"])
                        remotes.append(snapshot)
                    else:
                        remotes.append({
                            "backend_id": backend["id"],
                            "backend_name": backend.get("name", backend["url"]),
                            "error": "连接失败",
                            "tasks": [],
                        })
                except Exception:
                    remotes.append({
                        "backend_id": backend["id"],
                        "backend_name": backend.get("name", backend["url"]),
                        "error": "连接超时",
                        "tasks": [],
                    })
        return {"local": local, "remotes": remotes, "backends": self.backends}

    def admin_sync_task(self, source_backend: str, source_client_id: str, target_backend: str) -> dict:
        if source_backend == "local":
            export_data = self.admin_task_export(source_client_id)
        else:
            client = self._get_backend_client(source_backend)
            if not client:
                return {"error": "源节点不存在"}
            export_data = client.export(source_client_id)
            if not export_data:
                return {"error": "从源节点导出失败"}

        if target_backend == "local":
            result = self.admin_import(json.dumps(export_data, ensure_ascii=False))
        else:
            client = self._get_backend_client(target_backend)
            if not client:
                return {"error": "目标节点不存在"}
            result = client.import_tasks(export_data)
            if not result:
                return {"error": "导入到目标节点失败"}
        return result

    def list_tasks(self, client_ids: list[str]) -> dict:
        results = []
        for cid in client_ids:
            key = cid.strip() or "default"
            with self.states_lock:
                st = self.states.get(key)
            if not st:
                results.append({"client_id": key, "exists": False})
                continue
            with st.lock:
                params = st.params or {}
                results.append({
                    "client_id": key,
                    "exists": True,
                    "running": st.running,
                    "waiting_for_schedule": st.waiting_for_schedule,
                    "date": params.get("date", ""),
                    "dates": params.get("dates", []),
                })
        return {"tasks": results}

    def _state_by_id(self, client_id: str) -> RuntimeState | None:
        key = client_id.strip() or "default"
        with self.states_lock:
            return self.states.get(key)

    def check_token(self, client_id: str, params: dict) -> dict:
        state = self.state_for(client_id)
        self._remember_wx_token(state, params)
        effective = self._merged_params(state, params)
        headers = self.capture.submit_headers()
        headers.update(effective.get("headers") or {})
        token = str(headers.get("wx-token") or "").strip()
        if not token:
            return {"success": False, "member_name": "", "error": "token 为空"}
        request = Request(
            "https://stmember.styd.cn/v1/member/is_parent?",
            headers=headers,
            method="GET",
        )
        try:
            raw, _ = send_request(request, timeout=10, verify_ssl=False)
            payload = json.loads(raw)
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            return {"success": False, "member_name": "", "error": str(exc)}
        if payload.get("code") != 0:
            return {"success": False, "member_name": "", "error": payload.get("msg", "接口返回错误")}
        member_name = (payload.get("data") or {}).get("info") or {}
        name = str(member_name.get("member_name") or "").strip()
        if name:
            return {"success": True, "member_name": name}
        return {"success": False, "member_name": "", "error": "无法提取 member_name"}

    def clear_logs(self, client_id: str) -> dict:
        state = self.state_for(client_id)
        with state.lock:
            state.logs.clear()
        return self.status(client_id)

    def site_status(self, client_id: str, params: dict) -> dict:
        state = self.state_for(client_id)
        self._remember_wx_token(state, params)
        effective = self._merged_params(state, params)
        saved_params = _without_wx_token(effective)
        date = _monitor_date(effective)
        if not date:
            message = "请先选择监听日期"
            self.log(state, message)
            return {"success": False, "error": message, "snapshot": {"date": "", "items": []}}

        request_data = self.capture.build_site_list_request(effective, date)
        request_summary_data = _safe_request_summary(request_data)
        with state.lock:
            state.params = saved_params
            state.last_request = request_summary_data
            state.updated_at = time.time()

        self.log(state, f"查询当前场地预约情况：{date}")
        response = self._send_site_list_request(state, request_data, effective)
        if not response.get("success"):
            message = response.get("error") or response.get("message") or "查询失败"
            self.log(state, f"查询当前场地预约情况失败：{date} {message}")
            return {
                "success": False,
                "error": message,
                "request": request_summary_data,
                "response": response,
                "snapshot": {"date": date, "items": []},
            }

        snapshot = site_list_payload_to_dict(response.get("payload"), date)
        available_count = sum(1 for item in snapshot["items"] if item.get("available"))
        occupied_count = len(snapshot["items"]) - available_count
        message = f"查询完成：{date}，可约 {available_count} 个，已约 {occupied_count} 个"
        self.log(state, message)
        with state.lock:
            state.last_response = {
                "success": True,
                "message": message,
                "available_count": available_count,
                "occupied_count": occupied_count,
            }
        return {
            "success": True,
            "message": message,
            "request": request_summary_data,
            "snapshot": snapshot,
            "available_count": available_count,
            "occupied_count": occupied_count,
        }

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
        total_success_units = 0
        successful_slot_keys: set[str] = set()
        required_success_units = _required_success_units(params)
        try:
            if not self._wait_for_schedule(state, params):
                return
            if params.get("monitor_enabled"):
                self._run_monitor_loop(state, params)
                return
            while not state.stop_event.is_set():
                attempt += 1
                self.log(state, f"第 {attempt} 轮提交预约请求")
                response = self._send_round(state, params, successful_slot_keys)
                with state.lock:
                    state.last_response = response
                total_success_units += response.get("success_units", 0)
                if total_success_units >= required_success_units:
                    if not response.get("notification_sent"):
                        response["success"] = True
                        response["success_units"] = total_success_units
                        response["stop_reason"] = f"累计成功 {total_success_units}/{required_success_units} 个场地小时，任务停止"
                        self._notify_success(state, params, response)
                    self.log(state, f"累计成功 {total_success_units}/{required_success_units} 个场地小时，任务结束")
                    break
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

    def _run_monitor_loop(self, state: RuntimeState, params: dict) -> None:
        attempt = 0
        successful_slot_keys: set[str] = set()
        max_attempts = int(params.get("max_attempts") or 0)
        interval = max(0.1, float(params.get("monitor_interval_seconds") or 20))
        targets = _monitor_targets(params)
        if not targets:
            self.log(state, "监听下单未选择目标，请选择已经被预约的场地时间")
            return

        self.log(state, f"开始监听下单：{len(targets)} 个目标，监听间隔 {interval:g} 秒")
        while not state.stop_event.is_set():
            attempt += 1
            self.log(state, f"第 {attempt} 轮监听场地释放")
            response = self._send_monitor_round(state, params, targets, successful_slot_keys)
            with state.lock:
                state.last_response = response
            if response.get("success"):
                self.log(state, "监听下单任务结束")
                break
            if max_attempts and attempt >= max_attempts:
                self.log(state, "达到最大监听次数，任务结束")
                break
            state.stop_event.wait(interval)

    def _send_monitor_round(
        self,
        state: RuntimeState,
        params: dict,
        targets: list[dict],
        successful_slot_keys: set[str],
    ) -> dict:
        released = []
        status_responses = []
        for date in _monitor_dates(params):
            request_data = self.capture.build_site_list_request(params, date)
            self.log(state, f"查询场地状态：{date}")
            site_response = self._send_site_list_request(state, request_data, params)
            site_response["target"] = date
            site_response["request"] = _safe_request_summary(request_data)
            status_responses.append(site_response)
            if not site_response.get("success"):
                self.log(state, f"查询场地状态失败：{date} {site_response.get('error') or site_response.get('message') or ''}")
                continue
            if params.get("dry_run"):
                available = [item for item in targets if item.get("date") == date]
            else:
                available = _available_monitor_targets(site_response.get("payload") or {}, targets, date)
            for item in available:
                key = _selection_key(item["date"], item["court"], item["time_slot"])
                if key in successful_slot_keys:
                    continue
                released.append(item)

        if not released:
            self.log(state, "本轮没有监听目标释放")
            return {"success": False, "released": [], "responses": status_responses}

        self.log(state, f"发现 {len(released)} 个监听目标可预约，立即下单")
        submit_params = {**params, "selections": [_selection_without_date(item) for item in released]}
        submit_responses = []
        success_units = 0
        max_workers = min(len(released), 16)
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {}
        try:
            for index, item in enumerate(released, start=1):
                request_data = self.capture.build_submit_request(
                    {
                        **submit_params,
                        "date": item["date"],
                        "court": item["court"],
                        "time_slots": [item["time_slot"]],
                    }
                )
                futures[executor.submit(self._send_request, state, request_data, params)] = (index, item, request_data)
            pending = set(futures)
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda item: futures[item][0]):
                    index, item, request_data = futures[future]
                    response = future.result()
                    response["index"] = index
                    response["target"] = _request_target_desc(request_data)
                    response["request"] = _safe_request_summary(request_data)
                    submit_responses.append(response)
                    status = "成功" if response.get("success") else "失败"
                    self.log(state, f"监听下单第 {index} 个请求（{status}）：{response['target']}")
                    if response.get("success"):
                        self._notify_request_success(state, params, response, set())
                    success_units += _response_success_units(response, successful_slot_keys)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        submit_responses.sort(key=lambda item: item.get("index", 0))
        result = {
            "success": success_units > 0,
            "success_units": success_units,
            "released": [_monitor_target_desc(item) for item in released],
            "responses": submit_responses,
            "status_responses": status_responses,
            "success_targets": _successful_targets(submit_responses),
        }
        if result["success"]:
            self._notify_success(state, params, result)
        return result

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

    def _send_round(self, state: RuntimeState, params: dict, successful_slot_keys: set[str] | None = None) -> dict:
        requests = self.capture.build_submit_requests(params)
        if not requests:
            self.log(state, "没有可提交的请求，请至少选择日期、场地和时间段")
            return {"success": False, "responses": []}

        required_success_units = _required_success_units(params)
        self.log(state, f"本轮并发提交 {len(requests)} 个请求")
        for index, request_data in enumerate(requests, start=1):
            self.log(state, f"准备第 {index} 个请求：{_request_target_desc(request_data)}")

        responses = []
        success_units = 0
        if successful_slot_keys is None:
            successful_slot_keys = set()
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
                    success_units += _response_success_units(response, successful_slot_keys)

                    if success_units >= required_success_units:
                        cancelled = 0
                        for pending_future in pending:
                            if pending_future.cancel():
                                cancelled += 1
                        self.log(state, f"成功时间数已达到 {success_units}，停止等待剩余 {len(pending)} 个请求，已取消 {cancelled} 个未开始请求")
                        self.log(state, f"已达到目标 {required_success_units} 个成功场地小时，停止当前 wx-token 的执行")
                        executor.shutdown(wait=False, cancel_futures=True)
                        responses.sort(key=lambda item: item.get("index", 0))
                        result = {
                            "success": True,
                            "success_units": success_units,
                            "responses": responses,
                            "cancelled": cancelled,
                            "success_targets": _successful_targets(responses),
                            "stop_reason": f"已达到目标 {required_success_units} 个成功场地小时，停止当前 wx-token 的执行",
                        }
                        self._notify_success(state, params, result)
                        self.notify(state, _stop_notification_message(result), sync=not params.get("dry_run", True))
                        return result
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        responses.sort(key=lambda item: item.get("index", 0))
        self.log(state, f"本轮成功时间数：{success_units}")
        return {
            "success": success_units >= required_success_units,
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
        success = _payload_success(payload)
        return {"success": success, "message": payload.get("msg", payload.get("code")), "payload": payload}

    def _send_site_list_request(self, state: RuntimeState, request_data: dict, params: dict) -> dict:
        with state.lock:
            state.last_request = _safe_request_summary(request_data)

        if params.get("dry_run"):
            payload = self._mock_site_list_payload()
            return {"success": True, "message": "mock site list", "payload": payload, "mock": True}

        request = Request(
            request_data["url"],
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
        return {"success": _payload_success(payload), "message": payload.get("msg", payload.get("code")), "payload": payload}

    def _mock_site_list_payload(self) -> dict:
        entry = self.capture.latest_site_list_entry()
        if entry:
            return self.capture.decode_site_list_response(entry)
        snapshot = self.capture.venue_snapshot()
        return {
            "code": 0,
            "msg": "mock",
            "data": {
                "list": [
                    {
                        "site_id": court.site_id,
                        "site_name": court.site_name,
                        "site_data": [
                            {
                                **time_slot.__dict__,
                                "status": 1 if (court_index + time_index) % 4 == 0 else 2,
                                "disabled_desc": "已预约" if (court_index + time_index) % 4 == 0 else "",
                                "disabled_reason": "mock" if (court_index + time_index) % 4 == 0 else "",
                                "member_name": f"Mock用户{court_index + 1}" if (court_index + time_index) % 4 == 0 else "",
                                "mobile": f"138****{time_index + 1:04d}" if (court_index + time_index) % 4 == 0 else "",
                            }
                            for time_index, time_slot in enumerate(snapshot.times)
                        ],
                    }
                    for court_index, court in enumerate(snapshot.courts)
                ]
            },
        }

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
        if state.wx_token and not (merged.get("headers") or {}).get("wx-token"):
            merged.setdefault("headers", {})["wx-token"] = state.wx_token
        return merged


class BackendClient:
    def __init__(self, backend: dict) -> None:
        self.url = backend["url"].rstrip("/")
        self.password = backend["password"]
        self.name = backend.get("name", self.url)
        self.backend_id = backend.get("id", "")
        self._session_cookie: str = ""

    def login(self) -> bool:
        body = json.dumps({"password": self.password}).encode("utf-8")
        req = Request(
            f"{self.url}{ADMIN_PATH}/api/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urlopen(req, timeout=5)
            for header_line in resp.headers.get_all("set-cookie") or []:
                for part in header_line.split(";"):
                    part = part.strip()
                    if part.startswith(f"{ADMIN_COOKIE_NAME}="):
                        self._session_cookie = part.split("=", 1)[1]
                        return True
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("session"):
                self._session_cookie = data["session"]
                return True
        except (HTTPError, URLError, OSError, json.JSONDecodeError):
            pass
        return False

    def snapshot(self) -> dict | None:
        return self._get(f"{ADMIN_PATH}/api/snapshot")

    def start(self, client_id: str) -> dict | None:
        return self._post(f"{ADMIN_PATH}/api/start", {"client_id": client_id})

    def stop(self, client_id: str) -> dict | None:
        return self._post(f"{ADMIN_PATH}/api/stop", {"client_id": client_id})

    def import_tasks(self, payload: dict) -> dict | None:
        return self._post(f"{ADMIN_PATH}/api/import", payload)

    def export(self, client_id: str = "__all__") -> dict | None:
        return self._post(f"{ADMIN_PATH}/api/export", {"client_id": client_id})

    def _get(self, path: str) -> dict | None:
        if not self._session_cookie and not self.login():
            return None
        req = Request(
            f"{self.url}{path}",
            headers={"Cookie": f"{ADMIN_COOKIE_NAME}={self._session_cookie}"},
            method="GET",
        )
        try:
            resp = urlopen(req, timeout=5)
            return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (401, 403) and self.login():
                req.add_header("Cookie", f"{ADMIN_COOKIE_NAME}={self._session_cookie}")
                try:
                    resp = urlopen(req, timeout=5)
                    return json.loads(resp.read().decode("utf-8"))
                except (HTTPError, URLError, OSError):
                    pass
        except (URLError, OSError, json.JSONDecodeError):
            pass
        return None

    def _post(self, path: str, payload: dict) -> dict | None:
        if not self._session_cookie and not self.login():
            return None
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(
            f"{self.url}{path}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Cookie": f"{ADMIN_COOKIE_NAME}={self._session_cookie}",
            },
            method="POST",
        )
        try:
            resp = urlopen(req, timeout=5)
            return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (401, 403) and self.login():
                req.remove_header("Cookie")
                req.add_header("Cookie", f"{ADMIN_COOKIE_NAME}={self._session_cookie}")
                try:
                    resp = urlopen(req, timeout=5)
                    return json.loads(resp.read().decode("utf-8"))
                except (HTTPError, URLError, OSError):
                    pass
        except (URLError, OSError, json.JSONDecodeError):
            pass
        return None


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


def _with_wx_token(params: dict, wx_token: str) -> dict:
    full = json.loads(json.dumps(params or {}, ensure_ascii=False))
    full.setdefault("headers", {})
    if wx_token:
        full["headers"]["wx-token"] = wx_token
    return full


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
    lines = [
        "【羽毛球抢票】抢票成功",
        f"成功时间数：{response.get('success_units', 0)}",
        f"抢票信息：{_response_targets_desc(response)}",
    ]
    if response.get("stop_reason"):
        lines.append(str(response["stop_reason"]))
    return "\n".join(lines)


def _stop_notification_message(response: dict) -> str:
    return "\n".join(
        [
            "【羽毛球抢票】已停止当前 wx-token",
            str(response.get("stop_reason") or "已达到停止条件"),
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
    monitor_selections = params.get("monitor_selections") or []
    if params.get("monitor_enabled") and monitor_selections:
        targets = []
        for item in _monitor_targets(params):
            court = item.get("court") or {}
            time_slot = item.get("time_slot") or {}
            targets.append(
                f"{item.get('date') or ''} {court.get('site_name', '未知场地')} "
                f"{time_slot.get('start_time', '?')}-{time_slot.get('end_time', '?')}"
            )
        return "；".join(targets[:10]) + (" ..." if len(targets) > 10 else "")

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


def _monitor_date(params: dict) -> str:
    date = str(params.get("monitor_date") or "").strip()
    if date:
        return date
    dates = params.get("dates") or []
    if dates:
        return str(dates[0])
    return str(params.get("date") or "").strip()


def _monitor_dates(params: dict) -> list[str]:
    date = _monitor_date(params)
    if date:
        return [date]
    return []


def _monitor_targets(params: dict) -> list[dict]:
    targets = []
    for date in _monitor_dates(params):
        for item in params.get("monitor_selections") or []:
            court = item.get("court") or {}
            time_slot = item.get("time_slot") or {}
            if court and time_slot:
                targets.append({"date": date, "court": court, "time_slot": time_slot})
    return targets


def _available_monitor_targets(payload: dict, targets: list[dict], date: str) -> list[dict]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    available_keys = set()
    for court_data in data.get("list", []):
        court = {"site_id": court_data.get("site_id"), "site_name": court_data.get("site_name")}
        for slot in court_data.get("site_data", []):
            if _slot_is_available(slot):
                available_keys.add(_selection_key(date, court, slot))

    released = []
    for item in targets:
        if item.get("date") != date:
            continue
        key = _selection_key(date, item["court"], item["time_slot"])
        if key in available_keys:
            released.append(item)
    return released


def _slot_is_available(slot: dict) -> bool:
    return str(slot.get("status")) == "2" and str(slot.get("times", "0")) != "0"


def _selection_key(date: str, court: dict, time_slot: dict) -> str:
    return "|".join(
        [
            str(date),
            str(court.get("site_id", "")),
            str(time_slot.get("start_time", "")),
            str(time_slot.get("end_time", "")),
        ]
    )


def _selection_without_date(item: dict) -> dict:
    return {"court": item["court"], "time_slot": item["time_slot"]}


def _monitor_target_desc(item: dict) -> str:
    court = item.get("court") or {}
    time_slot = item.get("time_slot") or {}
    return (
        f"{item.get('date', '')} {court.get('site_name', '未知场地')} "
        f"{time_slot.get('start_time', '?')}-{time_slot.get('end_time', '?')}"
    )


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


def _response_success_units(response: dict, successful_slot_keys: set[str] | None = None) -> int:
    if not response.get("success"):
        return 0
    keys = _request_slot_keys(response.get("request", {}))
    if not keys and response.get("dry_run"):
        keys = _request_slot_keys(response.get("body", {}))
    if not keys:
        keys = [str(response.get("target") or response.get("index") or "unknown")]
    if successful_slot_keys is None:
        return len(set(keys))

    added = 0
    for key in keys:
        if key in successful_slot_keys:
            continue
        successful_slot_keys.add(key)
        added += 1
    return added


def _required_success_units(params: dict) -> int:
    dates = _unique_dates(params.get("dates") or ([params.get("date")] if params.get("date") else []))
    date_count = max(1, len(dates))
    selections = params.get("monitor_selections") if params.get("monitor_enabled") else params.get("selections")
    keys: set[str] = set()
    for item in selections or []:
        if not isinstance(item, dict):
            continue
        court = item.get("court") if isinstance(item.get("court"), dict) else {}
        time_slot = item.get("time_slot") if isinstance(item.get("time_slot"), dict) else {}
        key = _selection_key("", court, time_slot)
        if key.strip("|"):
            keys.add(key)
    if keys:
        return max(1, len(keys) * date_count)

    time_slots = params.get("time_slots") or params.get("selected_times") or []
    if time_slots:
        return max(1, len(time_slots) * date_count)
    return 2


def _payload_success(payload: dict) -> bool:
    code = payload.get("code")
    if code == 0 or str(code) == "0":
        return True
    if payload.get("success") is True:
        return True
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for key in ["order_id", "order_no", "reserve_id", "reserve_no", "pay_order_id", "trade_no"]:
        if data.get(key) or payload.get(key):
            return True
    text = " ".join(str(value or "") for value in [payload.get("msg"), payload.get("message"), data.get("msg")])
    if any(word in text for word in ["失败", "不可", "已满", "无效", "过期", "取消", "错误", "不足"]):
        return False
    return any(word in text for word in ["成功", "待支付", "预约成功", "下单成功"])


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
        return f"{hours}小时{minutes}分钟{secs}秒"
    if minutes:
        return f"{minutes}分钟{secs}秒"
    return f"{secs}秒"


def _format_timestamp(timestamp: float) -> str:
    if not timestamp:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _form_values(form: dict[str, list[str]], key: str) -> list[str]:
    values = form.get(key) or []
    return [str(item) for item in values]


def _form_value(form: dict[str, list[str]], key: str, default: str = "") -> str:
    values = _form_values(form, key)
    return values[-1] if values else default


def _form_bool(form: dict[str, list[str]], key: str) -> bool:
    return key in form


def _selection_count(params: dict) -> int:
    if params.get("monitor_enabled"):
        return len(params.get("monitor_selections") or [])
    return len(params.get("selections") or [])


def _admin_task_name(params: dict) -> str:
    dates = params.get("dates") or ([params.get("date")] if params.get("date") else [])
    date = str(dates[0]) if dates else "未选日期"
    return f"{date}-{_admin_court_count(params)}个场地-{_admin_main_time(params)}"


def _admin_court_count(params: dict) -> int:
    selections = params.get("monitor_selections") if params.get("monitor_enabled") else params.get("selections")
    court_ids = {
        str((item.get("court") or {}).get("site_id") or (item.get("court") or {}).get("site_name") or "")
        for item in selections or []
        if isinstance(item, dict) and isinstance(item.get("court"), dict)
    }
    court_ids = {item for item in court_ids if item}
    if court_ids:
        return len(court_ids)
    fallback_courts = params.get("courts") or []
    fallback_ids = {
        str((court or {}).get("site_id") or (court or {}).get("site_name") or "")
        for court in fallback_courts
        if isinstance(court, dict)
    }
    return len({item for item in fallback_ids if item})


def _admin_main_time(params: dict) -> str:
    selections = params.get("monitor_selections") if params.get("monitor_enabled") else params.get("selections")
    slots = [
        item.get("time_slot") or {}
        for item in selections or []
        if isinstance(item, dict) and isinstance(item.get("time_slot"), dict)
    ]
    if not slots:
        slots = [slot for slot in params.get("time_slots") or [] if isinstance(slot, dict)]
    if not slots:
        return "未选时间"

    def sort_key(slot: dict) -> tuple[str, str]:
        return (str(slot.get("start_time") or ""), str(slot.get("end_time") or ""))

    first = sorted(slots, key=sort_key)[0]
    start = str(first.get("start_time") or "?")
    end = str(first.get("end_time") or "?")
    return f"{start}-{end}"


def _admin_form_to_params(snapshot, current: dict, form: dict[str, list[str]], wx_token: str) -> dict:
    dates = _unique_dates([item.strip().replace("-", "/") for item in _form_value(form, "dates", "").split(",")])
    if not dates:
        date = _form_value(form, "date", str(current.get("date") or snapshot.date)).strip().replace("-", "/")
        dates = [date] if date else []

    courts_by_id = {str(court.site_id): court.__dict__ for court in snapshot.courts}
    times_by_key = {f"{time.start_time}-{time.end_time}": time.__dict__ for time in snapshot.times}
    selections = []
    for value in _form_values(form, "selection"):
        if "|" not in value:
            continue
        court_id, time_key = value.split("|", 1)
        court = courts_by_id.get(court_id)
        time_slot = times_by_key.get(time_key)
        if court and time_slot:
            selections.append({"court": court, "time_slot": time_slot})

    monitor_enabled = _form_bool(form, "monitor_enabled")
    params = json.loads(json.dumps(current or {}, ensure_ascii=False))
    params.update(
        {
            "date": dates[0] if dates else "",
            "dates": dates,
            "monitor_enabled": monitor_enabled,
            "monitor_date": dates[0] if dates else "",
            "monitor_interval_seconds": _to_number(
                _form_value(form, "monitor_interval_seconds", str(current.get("monitor_interval_seconds") or 20)),
                20,
            ),
            "request_mode": _form_value(form, "request_mode", str(current.get("request_mode") or "single")),
            "dry_run": _form_bool(form, "dry_run"),
            "verify_ssl": False,
            "schedule_enabled": _form_bool(form, "schedule_enabled"),
            "scheduled_start_at": _form_value(form, "scheduled_start_at", ""),
            "interval_seconds": _to_number(_form_value(form, "interval_seconds", str(current.get("interval_seconds") or 0.1)), 0.1),
            "max_attempts": int(_to_number(_form_value(form, "max_attempts", str(current.get("max_attempts") or 100000)), 100000)),
            "headers": {
                **(current.get("headers") or {}),
                "wx-token": wx_token,
                "shop-id": _form_value(form, "shop_id", str((current.get("headers") or {}).get("shop-id") or "")),
                "brand-code": _form_value(form, "brand_code", str((current.get("headers") or {}).get("brand-code") or "")),
            },
        }
    )
    if monitor_enabled:
        params["selections"] = []
        params["monitor_selections"] = selections
    else:
        params["selections"] = selections
        params["monitor_selections"] = []
    return params


def _to_number(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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

    full_snapshot = app.admin_full_snapshot()
    venue_snapshot = app.capture.venue_snapshot()
    all_backends = app.backends

    local_tasks = full_snapshot["local"].get("tasks") or []
    local_cards = [
        _admin_task_card(task, venue_snapshot, all_backends, "local")
        for task in local_tasks
    ]
    local_body = "\n".join(local_cards) or '<p class="empty-hint">暂无本地任务</p>'

    remote_sections = ""
    for remote in full_snapshot.get("remotes") or []:
        bid = remote.get("backend_id", "")
        bname = escape(remote.get("backend_name", bid))
        if remote.get("error"):
            remote_sections += f'<h3 class="backend-group-title">{bname} <span class="backend-error">({escape(remote["error"])})</span></h3>'
            continue
        remote_task_cards = [
            _admin_task_card(task, venue_snapshot, all_backends, bid)
            for task in remote.get("tasks") or []
        ]
        remote_body = "\n".join(remote_task_cards) or f'<p class="empty-hint">{bname} 暂无任务</p>'
        remote_sections += f'<h3 class="backend-group-title">{bname}</h3>\n{remote_body}'

    backends_list_html = ""
    for b in all_backends:
        backends_list_html += f"""
        <div class="backend-item">
          <span class="backend-name">{escape(b.get('name', ''))}</span>
          <span class="backend-url">{escape(b.get('url', ''))}</span>
          <form method="post" action="{ADMIN_PATH}/backend/test" class="inline-form">
            <input type="hidden" name="backend_id" value="{escape(b.get('id', ''))}" />
            <button type="submit" class="link-button">测试</button>
          </form>
          <form method="post" action="{ADMIN_PATH}/backend/remove" class="inline-form">
            <input type="hidden" name="backend_id" value="{escape(b.get('id', ''))}" />
            <button type="submit" class="danger">删除</button>
          </form>
        </div>"""

    notice_html = f'<div class="notice">{escape(message)}</div>' if message else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>后端管理</title>
  <style>{_admin_css()}</style>
</head>
<body>
  <main class="admin">
    <header>
      <div>
        <h1>后端管理</h1>
        <p>管理多个后端节点，导入导出任务，查看和修改运行参数。</p>
      </div>
      <nav>
        <a href="{ADMIN_PATH}">刷新</a>
        <form method="post" action="{ADMIN_PATH}/export" target="_blank">
          <input type="hidden" name="client_id" value="__all__" />
          <button type="submit" class="link-button">导出全部</button>
        </form>
      </nav>
    </header>
    {notice_html}
    <section class="backends-panel">
      <div class="backends-header">
        <h2>远程节点</h2>
        <p>添加其他后端实例，统一管理所有节点的任务。</p>
      </div>
      <div class="backends-body">
        <div class="backend-list">{backends_list_html or '<p class="empty-hint">暂未添加远程节点</p>'}</div>
        <form method="post" action="{ADMIN_PATH}/backend/add" class="backend-add-form">
          <input name="name" placeholder="节点名称" />
          <input name="url" placeholder="https://host:port" required />
          <input name="password" type="password" placeholder="管理密码" required />
          <button type="submit">添加节点</button>
        </form>
      </div>
    </section>
    <section class="import-panel">
      <div>
        <h2>导入任务</h2>
        <p>粘贴单个任务参数、单任务导出 JSON，或"导出全部"的 JSON。可填写客户端 ID 覆盖导入目标。</p>
      </div>
      <form method="post" action="{ADMIN_PATH}/import">
        <label>目标客户端 ID（可选）<input name="client_id" placeholder="例如 default 或 mobile-a" /></label>
        <textarea name="payload" spellcheck="false" placeholder="粘贴任务 JSON，支持单任务导出或导出全部的 JSON"></textarea>
        <div class="import-actions" role="group" aria-label="导入后操作">
          <label><input type="radio" name="import_action" value="save" checked /> 仅导入</label>
          <label><input type="radio" name="import_action" value="start" /> 导入后开启任务</label>
          <label><input type="radio" name="import_action" value="stop" /> 导入后停止任务</label>
        </div>
        <button type="submit">导入任务</button>
      </form>
    </section>
    <section class="task-list">
      <h3 class="backend-group-title">本地</h3>
      {local_body}
      {remote_sections}
    </section>
  </main>
</body>
</html>"""


def _admin_task_card(task: dict, snapshot, backends: list[dict] | None = None, backend_id: str = "local") -> str:
    params = task.get("params") or {}
    task_name = _admin_task_name(params)
    is_active = task["running"] or task["waiting_for_schedule"]
    status = "等待定时" if task["waiting_for_schedule"] else "运行中" if task["running"] else "已停止"
    status_class = "running" if task["running"] else "waiting" if task["waiting_for_schedule"] else "stopped"
    dates = ", ".join(str(item) for item in params.get("dates") or ([params.get("date")] if params.get("date") else []))
    headers = params.get("headers") or {}
    selections = params.get("monitor_selections") if params.get("monitor_enabled") else params.get("selections")
    selected_keys = {
        f"{item.get('court', {}).get('site_id')}|{item.get('time_slot', {}).get('start_time')}-{item.get('time_slot', {}).get('end_time')}"
        for item in selections or []
    }
    export_payload = {
        "client_id": task["client_id"],
        "params": _with_wx_token(params, task.get("wx_token") or ""),
    }
    export_json = escape(json.dumps(export_payload, ensure_ascii=False, indent=2))
    grid_html = _admin_selection_grid(snapshot, selected_keys)
    mode = str(params.get("request_mode") or "single")
    open_attr = "open" if is_active else ""
    stopped_class = " task-stopped" if not is_active else ""
    is_remote = backend_id != "local"
    action_prefix = f"{ADMIN_PATH}/remote" if is_remote else ADMIN_PATH
    backend_field = f'<input type="hidden" name="backend_id" value="{escape(backend_id)}" />' if is_remote else ""
    sync_options = ""
    all_backends = backends or []
    for b in all_backends:
        if b.get("id") != backend_id:
            sync_options += f'<option value="{escape(b["id"])}">{escape(b.get("name", b["url"]))}</option>'
    if backend_id != "local":
        sync_options = f'<option value="local">本地</option>' + sync_options
    sync_html = ""
    if sync_options:
        sync_html = f"""
        <select name="sync_target" class="sync-select">
          <option value="">同步到...</option>
          {sync_options}
        </select>
        <button type="submit" formaction="{ADMIN_PATH}/remote/sync" class="secondary">同步</button>"""
    return f"""
<details class="task-card{stopped_class}" {open_attr}>
  <summary class="task-head">
    <div>
      <h2>{escape(task_name)}</h2>
      <p>客户端：<code>{escape(task['client_id'])}</code> · 更新时间：{escape(str(task.get('updated_at') or '-'))} · 选择 {escape(str(task.get('selection_count') or 0))} 项 · 日期：{escape(dates or '-')}</p>
    </div>
    <span class="task-head-actions">
      <span class="pill {status_class}">{status}</span>
      <span class="collapse-toggle" aria-hidden="true"><span class="show-open">展开</span><span class="show-close">折叠</span></span>
    </span>
  </summary>
  <div class="task-body">
    <details class="export-box">
      <summary>查看 / 复制导出 JSON</summary>
      <pre>{export_json}</pre>
    </details>
    <form method="post" action="{action_prefix}/update" class="task-form">
      <input type="hidden" name="client_id" value="{escape(task['client_id'])}" />
      {backend_field}
      <div class="form-grid">
        <label>wx-token
          <input name="wx_token" value="{escape(task.get('wx_token') or '')}" autocomplete="off" />
        </label>
        <label>日期（逗号分隔）
          <input name="dates" value="{escape(dates)}" placeholder="2026/05/28,2026/05/29" />
        </label>
        <label>轮询间隔秒
          <input name="interval_seconds" type="number" min="0.1" step="0.1" value="{escape(str(params.get('interval_seconds') or 0.1))}" />
        </label>
        <label>最大尝试次数
          <input name="max_attempts" type="number" min="0" value="{escape(str(params.get('max_attempts') or 100000))}" />
        </label>
        <label>shop-id
          <input name="shop_id" value="{escape(str(headers.get('shop-id') or ''))}" />
        </label>
        <label>brand-code
          <input name="brand_code" value="{escape(str(headers.get('brand-code') or ''))}" />
        </label>
        <label>定时启动时间
          <input name="scheduled_start_at" value="{escape(str(params.get('scheduled_start_at') or ''))}" placeholder="2026-05-28 09:59:59" />
        </label>
        <label>监听间隔秒
          <input name="monitor_interval_seconds" type="number" min="1" step="1" value="{escape(str(params.get('monitor_interval_seconds') or 20))}" />
        </label>
      </div>
      <div class="checks-row">
        <label><input type="radio" name="request_mode" value="single" {_checked(mode != 'pair')} /> 单个时间分开请求</label>
        <label><input type="radio" name="request_mode" value="pair" {_checked(mode == 'pair')} /> 同场相邻两小时一起请求</label>
        <label><input type="checkbox" name="monitor_enabled" {_checked(bool(params.get('monitor_enabled')))} /> 监听下单</label>
        <label style="display:none"><input type="checkbox" name="dry_run" /> dry-run</label>
        <label><input type="checkbox" name="schedule_enabled" {_checked(bool(params.get('schedule_enabled')))} /> 定时启动</label>
      </div>
      <div class="selection-block">
        <div class="selection-title">场地时间</div>
        <div class="selection-scroll">{grid_html}</div>
      </div>
      <div class="task-actions">
        <button type="submit">保存修改</button>
        <button type="submit" formaction="{action_prefix}/start" class="primary" {'disabled' if task['running'] else ''}>开启任务</button>
        <button type="submit" formaction="{action_prefix}/stop" class="danger" {'disabled' if not task['running'] else ''}>停止任务</button>
        <button type="submit" formaction="{ADMIN_PATH}/export" formtarget="_blank" class="secondary">导出此任务</button>{sync_html}
      </div>
    </form>
    <p class="last-log">最后日志：{escape(str(task.get('last_log') or '-'))}</p>
  </div>
</details>"""


def _admin_selection_grid(snapshot, selected_keys: set[str]) -> str:
    cells = ['<div class="grid-head corner">时间 / 场地</div>']
    for court in snapshot.courts:
        cells.append(f'<div class="grid-head">{escape(court.site_name)}</div>')
    for time_option in snapshot.times:
        time_key = f"{time_option.start_time}-{time_option.end_time}"
        cells.append(f'<div class="grid-time">{escape(time_key)}</div>')
        for court in snapshot.courts:
            value = f"{court.site_id}|{time_key}"
            checked = _checked(value in selected_keys)
            cells.append(
                '<label class="grid-choice">'
                f'<input type="checkbox" name="selection" value="{escape(value)}" {checked} />'
                f'<span>{escape(str(time_option.price))}元</span>'
                '</label>'
            )
    columns = f"112px repeat({len(snapshot.courts)}, minmax(78px, 1fr))"
    return f'<div class="selection-grid" style="grid-template-columns:{columns}">{"".join(cells)}</div>'


def _checked(value: bool) -> str:
    return "checked" if value else ""


def _admin_css() -> str:
    return """
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f8f9; color: #17211f; }
.auth { width: min(420px, calc(100vw - 32px)); margin: 12vh auto; padding: 24px; background: #fff; border: 1px solid #dce5e2; border-radius: 8px; }
h1, h2, p { margin: 0; }
h1 { font-size: 22px; }
h2 { font-size: 16px; }
p { color: #5f6f6a; }
code { padding: 1px 5px; border-radius: 4px; background: #eef4f3; color: #42514d; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
input, textarea, button { border-radius: 6px; border: 1px solid #ccd8d5; font: inherit; }
input { min-height: 36px; padding: 7px 10px; }
textarea { width: 100%; min-height: 170px; padding: 10px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
button { min-height: 34px; padding: 0 12px; background: #0f766e; color: #fff; border: 0; cursor: pointer; font-weight: 650; }
button:disabled { background: #a7b7b3; cursor: not-allowed; }
.error, .notice { padding: 10px 12px; margin-bottom: 14px; border-radius: 6px; background: #fff1f2; color: #be123c; }
.notice { background: #ecfdf5; color: #047857; }
.admin { width: min(1380px, calc(100vw - 32px)); margin: 24px auto; }
header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
header a { color: #0f766e; text-decoration: none; }
header nav { display: flex; align-items: center; gap: 10px; }
header form { margin: 0; }
.link-button, .secondary { background: #fff; color: #0f766e; border: 1px solid #b9d7d1; }
.danger { background: #fff1f2; color: #b42318; border: 1px solid #fecaca; }
.import-panel, .task-card, .empty-card { margin-bottom: 16px; padding: 16px; background: #fff; border: 1px solid #dce5e2; border-radius: 8px; }
.import-panel { display: grid; grid-template-columns: minmax(220px, 0.35fr) minmax(420px, 0.65fr); gap: 16px; align-items: start; }
.import-panel form { display: grid; gap: 10px; }
.import-actions { display: flex; flex-wrap: wrap; gap: 8px 14px; padding: 10px; border: 1px solid #edf2f0; border-radius: 6px; background: #f8fafc; }
.import-actions label { display: flex; grid-template-columns: none; align-items: center; gap: 6px; color: #17211f; }
.import-actions input { width: auto; min-height: 0; }
.task-list { display: grid; gap: 16px; }
.task-card { padding: 0; overflow: hidden; }
.task-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 14px 16px; cursor: pointer; list-style: none; }
.task-head::-webkit-details-marker { display: none; }
.task-head p { margin-top: 4px; font-size: 13px; }
.task-head-actions { display: flex; align-items: center; gap: 8px; }
.pill { display: inline-block; white-space: nowrap; padding: 4px 10px; border-radius: 999px; background: #eef4f3; color: #42514d; font-size: 12px; font-weight: 750; }
.pill.running { background: #dcfce7; color: #047857; }
.pill.waiting { background: #fef9c3; color: #854d0e; }
.pill.stopped { background: #eef4f3; color: #8a9a96; }
.task-card.task-stopped { opacity: 0.55; transition: opacity 0.2s; }
.task-card.task-stopped:hover { opacity: 0.85; }
.task-card.task-stopped .task-head { background: #f6f8f9; }
.collapse-toggle { display: inline-flex; align-items: center; justify-content: center; min-width: 54px; min-height: 28px; border: 1px solid #b9d7d1; border-radius: 6px; background: #fff; color: #0f766e; font-size: 12px; font-weight: 800; }
.show-close { display: none; }
.task-card[open] .show-open { display: none; }
.task-card[open] .show-close { display: inline; }
.task-body { display: grid; gap: 12px; padding: 0 16px 16px; border-top: 1px solid #edf2f0; }
.export-box { margin-bottom: 12px; border: 1px solid #edf2f0; border-radius: 6px; background: #f8fafc; }
.export-box summary { padding: 9px 10px; cursor: pointer; font-size: 13px; font-weight: 700; color: #42514d; }
.export-box pre { max-height: 220px; overflow: auto; margin: 0; padding: 10px; border-top: 1px solid #edf2f0; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.task-form { display: grid; gap: 12px; }
.form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
label { display: grid; gap: 5px; color: #5f6f6a; font-size: 12px; font-weight: 650; }
label input { width: 100%; }
.checks-row { display: flex; flex-wrap: wrap; gap: 10px 16px; padding: 10px; border: 1px solid #edf2f0; border-radius: 6px; background: #f8fafc; }
.checks-row label { display: flex; grid-template-columns: none; align-items: center; gap: 6px; color: #17211f; }
.checks-row input, .grid-choice input { width: auto; min-height: 0; }
.selection-block { display: grid; gap: 8px; }
.selection-title { color: #42514d; font-size: 13px; font-weight: 800; }
.selection-scroll { overflow: auto; border: 1px solid #dce5e2; border-radius: 8px; background: #fff; }
.selection-grid { display: grid; min-width: 980px; gap: 4px; padding: 8px; }
.grid-head, .grid-time, .grid-choice { min-height: 32px; border: 1px solid #dce5e2; border-radius: 6px; padding: 6px 7px; font-size: 12px; }
.grid-head { position: sticky; top: 8px; z-index: 2; background: #eef4f3; color: #0f766e; text-align: center; font-weight: 800; }
.corner { left: 8px; z-index: 3; }
.grid-time { position: sticky; left: 8px; z-index: 1; background: #fff; color: #5f6f6a; font-weight: 750; }
.grid-choice { display: flex; align-items: center; justify-content: center; gap: 5px; color: #17211f; }
.grid-choice:has(input:checked) { border-color: #0f766e; background: #eef4f3; color: #0f766e; font-weight: 800; }
.task-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.sync-select { min-height: 34px; padding: 0 8px; border-radius: 6px; border: 1px solid #ccd8d5; font-size: 13px; background: #fff; }
.last-log { margin-top: 2px; padding: 9px 10px; border-radius: 6px; background: #f8fafc; font-size: 12px; overflow-wrap: anywhere; }
.empty-card, .empty-hint { color: #5f6f6a; text-align: center; padding: 12px; }
.backends-panel { margin-bottom: 16px; padding: 16px; background: #fff; border: 1px solid #dce5e2; border-radius: 8px; }
.backends-header { margin-bottom: 12px; }
.backends-body { display: grid; gap: 10px; }
.backend-list { display: grid; gap: 8px; }
.backend-item { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border: 1px solid #edf2f0; border-radius: 6px; background: #f8fafc; }
.backend-name { font-weight: 700; color: #17211f; }
.backend-url { color: #5f6f6a; font-size: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; flex: 1; }
.backend-error { color: #b42318; font-size: 12px; font-weight: 400; }
.inline-form { margin: 0; }
.backend-add-form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.backend-add-form input { flex: 1; min-width: 120px; }
.backend-group-title { margin: 16px 0 8px; padding: 6px 0; font-size: 14px; font-weight: 800; color: #0f766e; border-bottom: 1px solid #edf2f0; }
.backend-group-title:first-child { margin-top: 0; }
@media (max-width: 920px) {
  .import-panel, .form-grid { grid-template-columns: 1fr; }
  header { align-items: flex-start; flex-direction: column; }
  .backend-add-form { flex-direction: column; }
}
"""


def create_handler(app: BookingWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            client_id = self._client_id()
            if path == f"{ADMIN_PATH}/api/snapshot":
                if not self._is_admin():
                    self._json_error(401, "unauthorized")
                    return
                self._json(app.admin_snapshot())
                return
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
            elif path == "/api/list-tasks":
                ids_raw = self.headers.get("x-client-ids") or client_id
                ids = [cid.strip() for cid in ids_raw.split(",") if cid.strip()]
                self._json(app.list_tasks(ids))
            elif path == "/api/export":
                self._json(app.status(client_id)["params"])
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            client_id = self._client_id()
            if path == f"{ADMIN_PATH}/api/login":
                payload = self._read_json()
                password = str(payload.get("password", ""))
                if app.admin.verify(password):
                    token = app.admin.new_session()
                    self.send_response(200)
                    self.send_header("content-type", "application/json; charset=utf-8")
                    self.send_header("set-cookie", f"{ADMIN_COOKIE_NAME}={token}; Path={ADMIN_PATH}; HttpOnly; SameSite=Lax")
                    body = json.dumps({"session": token}).encode("utf-8")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._json_error(401, "密码不正确")
                return
            if path == f"{ADMIN_PATH}/api/start":
                if not self._is_admin():
                    self._json_error(401, "unauthorized")
                    return
                payload = self._read_json()
                self._json(app.admin_start(payload.get("client_id", "")))
                return
            if path == f"{ADMIN_PATH}/api/stop":
                if not self._is_admin():
                    self._json_error(401, "unauthorized")
                    return
                payload = self._read_json()
                self._json(app.admin_stop(payload.get("client_id", "")))
                return
            if path == f"{ADMIN_PATH}/api/import":
                if not self._is_admin():
                    self._json_error(401, "unauthorized")
                    return
                payload = self._read_json()
                payload_text = json.dumps(payload, ensure_ascii=False)
                self._json(app.admin_import(payload_text))
                return
            if path == f"{ADMIN_PATH}/api/export":
                if not self._is_admin():
                    self._json_error(401, "unauthorized")
                    return
                payload = self._read_json()
                cid = payload.get("client_id", "__all__")
                result = app.admin_all_export() if cid == "__all__" else app.admin_task_export(cid)
                self._json(result)
                return
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
                form = self._read_form_multi()
                client = _form_value(form, "client_id", "")
                if client:
                    app.admin_stop(client)
                self._html(_admin_page(app, True, f"已请求停止任务：{client}"))
                return
            if path == f"{ADMIN_PATH}/start":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                client = _form_value(form, "client_id", "default")
                if client:
                    app.admin_update(form)
                    result = app.admin_start(client)
                    if result.get("error"):
                        self._html(_admin_page(app, True, str(result["error"])))
                    else:
                        self._html(_admin_page(app, True, f"已开启任务：{client}"))
                else:
                    self._html(_admin_page(app, True, "缺少客户端 ID，无法开启任务"))
                return
            if path == f"{ADMIN_PATH}/update":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                client = _form_value(form, "client_id", "default")
                app.admin_update(form)
                self._html(_admin_page(app, True, f"已保存任务：{client}"))
                return
            if path == f"{ADMIN_PATH}/import":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                result = app.admin_import(_form_value(form, "payload", ""), _form_value(form, "client_id", ""))
                if result.get("error"):
                    self._html(_admin_page(app, True, str(result["error"])))
                else:
                    imported = result["imported"]
                    action = _form_value(form, "import_action", "save")
                    self._html(_admin_page(app, True, app.admin_import_action_message(imported, action)))
                return
            if path == f"{ADMIN_PATH}/export":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                client = _form_value(form, "client_id", "")
                payload = app.admin_all_export() if client == "__all__" else app.admin_task_export(client)
                self._json(payload)
                return
            if path == f"{ADMIN_PATH}/backend/add":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                name = _form_value(form, "name", "")
                url = _form_value(form, "url", "")
                password = _form_value(form, "password", "")
                result = app.admin_add_backend(name, url, password)
                msg = result.get("error") or f"已添加节点：{name or url}"
                self._html(_admin_page(app, True, msg))
                return
            if path == f"{ADMIN_PATH}/backend/remove":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                backend_id = _form_value(form, "backend_id", "")
                app.admin_remove_backend(backend_id)
                self._html(_admin_page(app, True, "已删除节点"))
                return
            if path == f"{ADMIN_PATH}/backend/test":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                backend_id = _form_value(form, "backend_id", "")
                result = app.admin_test_backend(backend_id)
                msg = result.get("error") or result.get("message") or "测试完成"
                self._html(_admin_page(app, True, msg))
                return
            if path == f"{ADMIN_PATH}/remote/start":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                backend_id = _form_value(form, "backend_id", "")
                client = _form_value(form, "client_id", "")
                client_obj = app._get_backend_client(backend_id)
                if client_obj:
                    client_obj.start(client)
                    self._html(_admin_page(app, True, f"已请求远程开启任务：{client}"))
                else:
                    self._html(_admin_page(app, True, "远程节点不存在"))
                return
            if path == f"{ADMIN_PATH}/remote/stop":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                backend_id = _form_value(form, "backend_id", "")
                client = _form_value(form, "client_id", "")
                client_obj = app._get_backend_client(backend_id)
                if client_obj:
                    client_obj.stop(client)
                    self._html(_admin_page(app, True, f"已请求远程停止任务：{client}"))
                else:
                    self._html(_admin_page(app, True, "远程节点不存在"))
                return
            if path == f"{ADMIN_PATH}/remote/update":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                backend_id = _form_value(form, "backend_id", "")
                client = _form_value(form, "client_id", "default")
                client_obj = app._get_backend_client(backend_id)
                if client_obj:
                    params = _admin_form_to_params(app.capture.venue_snapshot(), {}, form, _form_value(form, "wx_token", ""))
                    export_data = {"client_id": client, "params": params}
                    client_obj.import_tasks(export_data)
                    self._html(_admin_page(app, True, f"已更新远程任务：{client}"))
                else:
                    self._html(_admin_page(app, True, "远程节点不存在"))
                return
            if path == f"{ADMIN_PATH}/remote/sync":
                if not self._is_admin():
                    self._redirect(ADMIN_PATH)
                    return
                form = self._read_form_multi()
                source_backend = _form_value(form, "backend_id", "local")
                source_client_id = _form_value(form, "client_id", "")
                target_backend = _form_value(form, "sync_target", "")
                if not target_backend:
                    self._html(_admin_page(app, True, "请选择同步目标"))
                    return
                result = app.admin_sync_task(source_backend, source_client_id, target_backend)
                if result.get("error"):
                    self._html(_admin_page(app, True, f"同步失败：{result['error']}"))
                else:
                    self._html(_admin_page(app, True, f"已同步任务 {source_client_id} 到目标节点"))
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
            elif path == "/api/check-token":
                self._json(app.check_token(client_id, payload))
            elif path == "/api/site-status":
                self._json(app.site_status(client_id, payload))
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
            return {key: values[-1] for key, values in parse_qs(data, keep_blank_values=True).items()}

        def _read_form_multi(self) -> dict[str, list[str]]:
            length = int(self.headers.get("content-length", "0"))
            if not length:
                return {}
            data = self.rfile.read(length).decode("utf-8")
            return {key: values for key, values in parse_qs(data, keep_blank_values=True).items()}

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

        def _json_error(self, status: int, message: str) -> None:
            data = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
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
