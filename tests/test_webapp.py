import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import badminton_booker.webapp as webapp
from badminton_booker.webapp import BookingWebApp, _admin_page
from badminton_booker.venue_defaults import default_date


def _tomorrow():
    return default_date()


def _day_after(days=1):
    return (date.today() + timedelta(days=days + 1)).strftime("%Y/%m/%d")


class BookingWebAppTest(unittest.TestCase):
    def test_wx_token_is_hidden_from_public_backend_state(self) -> None:
        app = BookingWebApp("request.txt")

        status = app.save_params(
            "client-a",
            {
                "headers": {
                    "wx-token": "secret-token-a",
                    "shop-id": "shop-a",
                    "brand-code": "brand-a",
                }
            },
        )

        self.assertNotIn("wx-token", status["params"]["headers"])
        self.assertNotIn("wx-token", app.metadata("client-a")["params"]["headers"])
        self.assertNotIn("wx-token", app.status("client-a")["params"]["headers"])
        self.assertNotIn("wx-token", status["last_request"]["requests"][0]["headers"])
        self.assertEqual(app.admin_snapshot()["tasks"][0]["wx_token"], "secret-token-a")

    def test_clients_have_independent_backend_state(self) -> None:
        app = BookingWebApp("request.txt")

        app.save_params("client-a", {"headers": {"shop-id": "shop-a"}})
        app.save_params("client-b", {"headers": {"shop-id": "shop-b"}})

        self.assertEqual(app.status("client-a")["params"]["headers"]["shop-id"], "shop-a")
        self.assertEqual(app.status("client-b")["params"]["headers"]["shop-id"], "shop-b")

    def test_admin_password_flow_and_stop(self) -> None:
        with TemporaryDirectory() as temp_dir:
            original_path = webapp.ADMIN_CONFIG_PATH
            webapp.ADMIN_CONFIG_PATH = Path(temp_dir) / "admin.json"
            try:
                app = BookingWebApp("request.txt")
                self.assertFalse(app.admin.password_set())
                self.assertTrue(app.admin.set_password("secret"))
                self.assertTrue(app.admin.password_set())
                self.assertTrue(app.admin.verify("secret"))
                self.assertFalse(app.admin.verify("wrong"))

                session = app.admin.new_session()
                self.assertTrue(app.admin.is_session(session))

                app.save_params("client-a", {"headers": {"wx-token": "token-a"}})
                html = _admin_page(app, True)
                self.assertIn("token-a", html)

                app.admin_stop("client-a")
                self.assertIn("已请求停止", app.status("client-a")["logs"][-1])
            finally:
                webapp.ADMIN_CONFIG_PATH = original_path

    def test_admin_export_import_and_update_task(self) -> None:
        app = BookingWebApp("request.txt")
        app.save_params(
            "client-a",
            {
                "headers": {"wx-token": "token-a"},
                "dates": [_tomorrow()],
                "selections": [
                    {
                        "court": {"site_id": 3692729935134806, "site_name": "1号场"},
                        "time_slot": {
                            "start_time": "07:00",
                            "end_time": "08:00",
                            "start_timestamp": 1779922800,
                            "end_timestamp": 1779926400,
                            "price": "75",
                            "times": "1",
                        },
                    }
                ],
            },
        )

        exported = app.admin_task_export("client-a")
        self.assertEqual(exported["params"]["headers"]["wx-token"], "token-a")

        imported = app.admin_import(
            f'{{"client_id":"client-b","params":{{"dates":["{_day_after(1)}"],"headers":{{"wx-token":"token-b"}},"selections":[]}}}}'
        )
        self.assertEqual(imported["imported"], ["client-b"])
        self.assertEqual(app.admin_snapshot()["tasks"][1]["wx_token"], "token-b")

        app.admin_update(
            {
                "client_id": ["client-a"],
                "wx_token": ["token-c"],
                "dates": [_day_after(2)],
                "interval_seconds": ["0.2"],
                "max_attempts": ["50"],
                "request_mode": ["single"],
                "selection": ["3692729935134809|08:00-09:00"],
            }
        )
        snapshot = app.admin_snapshot()["tasks"][0]
        self.assertEqual(snapshot["wx_token"], "token-c")
        self.assertEqual(snapshot["params"]["dates"], [_day_after(2)])
        self.assertEqual(snapshot["params"]["interval_seconds"], 0.2)
        self.assertEqual(snapshot["params"]["max_attempts"], 50)
        self.assertEqual(snapshot["params"]["selections"][0]["court"]["site_name"], "4号场")
        self.assertEqual(snapshot["params"]["selections"][0]["time_slot"]["start_time"], "08:00")

    def test_admin_task_name_uses_date_court_count_and_main_time(self) -> None:
        params = {
            "dates": [_tomorrow()],
            "selections": [
                {
                    "court": {"site_id": 1, "site_name": "1号场"},
                    "time_slot": {"start_time": "08:00", "end_time": "09:00"},
                },
                {
                    "court": {"site_id": 1, "site_name": "1号场"},
                    "time_slot": {"start_time": "07:00", "end_time": "08:00"},
                },
                {
                    "court": {"site_id": 2, "site_name": "2号场"},
                    "time_slot": {"start_time": "09:00", "end_time": "10:00"},
                },
            ],
        }

        self.assertEqual(webapp._admin_task_name(params), f"{_tomorrow()}-2个场地-07:00-08:00")

    def test_admin_page_exposes_import_actions_and_task_controls(self) -> None:
        app = BookingWebApp("request.txt")
        app.save_params(
            "client-a",
            {
                "headers": {"wx-token": "token-a"},
                "dates": [_tomorrow()],
                "selections": [
                    {
                        "court": {"site_id": 3692729935134806, "site_name": "1号场"},
                        "time_slot": {
                            "start_time": "07:00",
                            "end_time": "08:00",
                            "start_timestamp": 1779922800,
                            "end_timestamp": 1779926400,
                            "price": "75",
                            "times": "1",
                        },
                    }
                ],
            },
        )

        html = _admin_page(app, True)

        self.assertIn(f"{_tomorrow()}-1个场地-07:00-08:00", html)
        self.assertIn("导入后开启任务", html)
        self.assertIn("导入后停止任务", html)
        self.assertIn('formaction="/sundx/start"', html)
        self.assertIn("开启任务", html)
        self.assertIn("停止任务", html)

    def test_admin_start_uses_saved_params_and_cached_wx_token(self) -> None:
        app = BookingWebApp("request.txt")
        app.save_params(
            "client-a",
            {
                "headers": {"wx-token": "token-a"},
                "dates": [_tomorrow()],
                "selections": [],
            },
        )

        with patch.object(app, "start", return_value={"running": True}) as start:
            result = app.admin_start("client-a")

        self.assertEqual(result, {"running": True})
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0], "client-a")
        self.assertEqual(start.call_args.args[1]["headers"]["wx-token"], "token-a")

    def test_admin_import_action_can_start_or_stop_imported_tasks(self) -> None:
        app = BookingWebApp("request.txt")
        imported = app.admin_import(
            f'{{"tasks":[{{"client_id":"client-a","params":{{"dates":["{_tomorrow()}"],"headers":{{"wx-token":"token-a"}},"selections":[]}}}},'
            f'{{"client_id":"client-b","params":{{"dates":["{_day_after(1)}"],"headers":{{"wx-token":"token-b"}},"selections":[]}}}}]}}'
        )

        with patch.object(app, "admin_start") as start:
            message = app.admin_import_action_message(imported["imported"], "start")

        self.assertEqual(message, "已导入并开启任务：client-a, client-b")
        self.assertEqual([call.args[0] for call in start.call_args_list], ["client-a", "client-b"])

        with patch.object(app, "admin_stop") as stop:
            message = app.admin_import_action_message(imported["imported"], "stop")

        self.assertEqual(message, "已导入并停止任务：client-a, client-b")
        self.assertEqual([call.args[0] for call in stop.call_args_list], ["client-a", "client-b"])

    def test_success_detection_accepts_string_code_and_pending_payment(self) -> None:
        self.assertTrue(webapp._payload_success({"code": "0", "msg": "success"}))
        self.assertTrue(webapp._payload_success({"code": 1001, "msg": "下单成功，请待支付"}))
        self.assertTrue(webapp._payload_success({"code": 1001, "data": {"order_no": "order-1"}}))
        self.assertFalse(webapp._payload_success({"code": 1001, "msg": "预约失败，库存不足"}))

    def test_notify_uses_wechat_bot_webhook(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["data"] = request.data.decode("utf-8")
            captured["timeout"] = timeout

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return False

                def read(self):
                    return b'{"errcode":0,"errmsg":"ok"}'

            return Response()

        class ImmediateThread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon

            def start(self):
                self.target()

        with patch.object(webapp, "urlopen", fake_urlopen), patch.object(webapp.threading, "Thread", ImmediateThread):
            results = []
            webapp.notify("【羽毛球抢票】test", results.append)

        self.assertEqual(captured["url"], webapp.WECHAT_BOT_WEBHOOK)
        self.assertEqual(captured["timeout"], 5)
        self.assertEqual(
            captured["data"],
            '{"msgtype": "text", "text": {"content": "【羽毛球抢票】test"}}',
        )
        self.assertEqual(results, ["企业微信通知已发送"])

    def test_success_notification_includes_booking_context(self) -> None:
        d = _tomorrow()
        message = webapp._success_notification_message(
            {
                "success_units": 2,
                "success_targets": [
                    f"{d} 4号场 07:00-08:00",
                    f"{d} 4号场 08:00-09:00",
                ],
            }
        )

        self.assertIn("【羽毛球抢票】抢票成功", message)
        self.assertIn("成功时间数：2", message)
        self.assertIn(f"{d} 4号场 07:00-08:00", message)
        self.assertIn(f"{d} 4号场 08:00-09:00", message)

    def test_success_request_sends_sync_notification_at_success_log(self) -> None:
        app = BookingWebApp("request.txt")
        state = app.state_for("client-a")
        sent = []

        def fake_send(message):
            sent.append(message)
            return "企业微信通知已发送"

        d = _tomorrow()
        params = {
            "dry_run": False,
            "headers": {},
            "dates": [d],
            "selections": [
                {
                    "court": {"site_id": 3692729935134809, "site_name": "4号场"},
                    "time_slot": {
                        "start_time": "07:00",
                        "end_time": "08:00",
                        "start_timestamp": 1779922800,
                        "end_timestamp": 1779926400,
                        "price": "75",
                        "times": "1",
                    },
                },
                {
                    "court": {"site_id": 3692729935134809, "site_name": "4号场"},
                    "time_slot": {
                        "start_time": "08:00",
                        "end_time": "09:00",
                        "start_timestamp": 1779926400,
                        "end_timestamp": 1779930000,
                        "price": "75",
                        "times": "1",
                    },
                },
            ],
        }

        with patch.object(webapp, "_send_wechat_notification", fake_send):
            with patch.object(app, "_send_request", side_effect=lambda *args: {"success": True, "payload": {"code": 0}}):
                response = app._send_round(state, params)

        self.assertTrue(response["success"])
        self.assertTrue(response["notification_sent"])
        self.assertGreaterEqual(len(sent), 2)
        self.assertIn("【羽毛球抢票】单个请求抢票成功", sent[0])
        self.assertIn(f"{d} 4号场 07:00-08:00", sent[0])
        self.assertTrue(any("【羽毛球抢票】已停止当前 wx-token" in item for item in sent))
        self.assertTrue(any("企业微信通知已发送" in line for line in app.status("client-a")["logs"]))

    def test_success_units_count_unique_date_court_time(self) -> None:
        d = _tomorrow()
        successful_slot_keys = set()
        first = {
            "success": True,
            "request": {
                "body": {
                    "venues_date": d,
                    "venues_site_time": [
                        {"site_id": 1, "start_time": "08:00", "end_time": "09:00"},
                        {"site_id": 2, "start_time": "08:00", "end_time": "09:00"},
                    ],
                }
            },
        }
        duplicate = {
            "success": True,
            "request": {
                "body": {
                    "venues_date": d,
                    "venues_site_time": [
                        {"site_id": 1, "start_time": "08:00", "end_time": "09:00"},
                    ],
                }
            },
        }
        next_time = {
            "success": True,
            "request": {
                "body": {
                    "venues_date": d,
                    "venues_site_time": [
                        {"site_id": 1, "start_time": "09:00", "end_time": "10:00"},
                    ],
                }
            },
        }

        self.assertEqual(webapp._response_success_units(first, successful_slot_keys), 2)
        self.assertEqual(webapp._response_success_units(duplicate, successful_slot_keys), 0)
        self.assertEqual(webapp._response_success_units(next_time, successful_slot_keys), 1)

    def test_required_success_units_match_selected_slots(self) -> None:
        params = {
            "dates": [_day_after(3)],
            "selections": [
                {
                    "court": {"site_id": 1},
                    "time_slot": {"start_time": "09:00", "end_time": "10:00"},
                }
            ],
        }
        self.assertEqual(webapp._required_success_units(params), 1)

        params["selections"].append(
            {
                "court": {"site_id": 1},
                "time_slot": {"start_time": "10:00", "end_time": "11:00"},
            }
        )
        self.assertEqual(webapp._required_success_units(params), 2)

    def test_send_round_stops_after_single_selected_slot_success(self) -> None:
        app = BookingWebApp("request.txt")
        state = app.state_for("client-a")
        params = {
            "dry_run": False,
            "headers": {},
            "dates": [_day_after(3)],
            "selections": [
                {
                    "court": {"site_id": 3692729935134806, "site_name": "1号场"},
                    "time_slot": {
                        "start_time": "09:00",
                        "end_time": "10:00",
                        "start_timestamp": 1780275600,
                        "end_timestamp": 1780279200,
                        "price": "75",
                        "times": "1",
                    },
                }
            ],
        }

        with patch.object(app, "notify"):
            with patch.object(app, "_send_request", return_value={"success": True, "payload": {"code": 0}}):
                response = app._send_round(state, params)

        self.assertTrue(response["success"])
        self.assertEqual(response["success_units"], 1)
        self.assertEqual(response["stop_reason"], "已达到目标 1 个成功场地小时，停止当前 wx-token 的执行")

    def test_fixed_courts_include_v1_v2(self) -> None:
        app = BookingWebApp("request.txt")
        court_names = [court["site_name"] for court in app.metadata("client-a")["snapshot"]["courts"]]

        self.assertIn("6楼V1号场", court_names)
        self.assertIn("6楼V2号场", court_names)

    def test_monitor_round_submits_released_slot(self) -> None:
        app = BookingWebApp("request.txt")
        state = app.state_for("client-a")
        sent = []
        d = _tomorrow()
        params = {
            "dry_run": True,
            "headers": {},
            "dates": [d],
            "monitor_selections": [
                {
                    "court": {"site_id": 3692729935134806, "site_name": "1号场"},
                    "time_slot": {
                        "start_time": "09:00",
                        "end_time": "10:00",
                        "start_timestamp": 1779930000,
                        "end_timestamp": 1779933600,
                        "price": "75",
                        "times": "1",
                    },
                }
            ],
        }

        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "site_id": 3692729935134806,
                        "site_name": "1号场",
                        "site_data": [
                            {
                                "status": 2,
                                "times": "1",
                                "start_time": "09:00",
                                "end_time": "10:00",
                            }
                        ],
                    }
                ]
            },
        }

        with patch.object(app, "_send_site_list_request", return_value={"success": True, "payload": payload}):
            with patch.object(app, "_send_request", side_effect=lambda *args: {"success": True, "payload": {"code": 0}}):
                with patch.object(app, "notify", side_effect=lambda _state, message, sync=False: sent.append(message)):
                    response = app._send_monitor_round(state, params, webapp._monitor_targets(params), set())

        self.assertTrue(response["success"])
        self.assertEqual(response["success_units"], 1)
        self.assertEqual(response["success_targets"], [f"{d} 1号场 09:00-10:00"])
        self.assertTrue(any("监听下单第 1 个请求（成功）" in line for line in state.logs))
        self.assertTrue(any("【羽毛球抢票】dry-run 单个请求成功" in item for item in sent))

    def test_monitor_preview_shows_site_list_and_submit_requests(self) -> None:
        app = BookingWebApp("request.txt")
        d = _tomorrow()
        preview = app.preview(
            "client-a",
            {
                "monitor_enabled": True,
                "dates": [d],
                "monitor_date": d,
                "monitor_selections": [
                    {
                        "court": {"site_id": 3692729935134806, "site_name": "1号场"},
                        "time_slot": {
                            "start_time": "09:00",
                            "end_time": "10:00",
                            "start_timestamp": 1779930000,
                            "end_timestamp": 1779933600,
                            "price": "75",
                            "times": "1",
                        },
                    }
                ],
            },
        )

        self.assertEqual(preview["mode"], "monitor")
        self.assertIn("venues_site_list", preview["requests"][0]["url"])
        self.assertEqual(preview["monitor_targets"], [f"{d} 1号场 09:00-10:00"])
        self.assertEqual(len(preview["submit_requests_when_released"]), 1)

    def test_site_status_returns_live_snapshot(self) -> None:
        app = BookingWebApp("request.txt")
        d = _tomorrow()
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "site_id": 3692729935134806,
                        "site_name": "1号场",
                        "site_data": [
                            {
                                "status": 2,
                                "times": "1",
                                "start_time": "07:00",
                                "end_time": "08:00",
                                "price": "75",
                            },
                            {
                                "status": 1,
                                "times": "0",
                                "start_time": "08:00",
                                "end_time": "09:00",
                                "price": "75",
                                "disabled_desc": "已预约",
                                "member_name": "张三",
                                "mobile": "13800000000",
                            },
                        ],
                    }
                ]
            },
        }

        with patch.object(app, "_send_site_list_request", return_value={"success": True, "payload": payload}):
            response = app.site_status(
                "client-a",
                {
                    "monitor_date": d,
                    "headers": {"wx-token": "token-a"},
                },
            )

        self.assertTrue(response["success"])
        self.assertEqual(response["available_count"], 1)
        self.assertEqual(response["occupied_count"], 1)
        self.assertEqual(response["snapshot"]["date"], d)
        self.assertTrue(response["snapshot"]["items"][0]["available"])
        self.assertFalse(response["snapshot"]["items"][1]["available"])
        self.assertEqual(response["snapshot"]["items"][1]["member_name"], "张三")
        self.assertEqual(response["snapshot"]["items"][1]["mobile"], "13800000000")
        self.assertNotIn("wx-token", response["request"]["headers"])
        self.assertEqual(app.admin_snapshot()["tasks"][0]["wx_token"], "token-a")

    def test_monitor_loop_uses_separate_interval(self) -> None:
        app = BookingWebApp("request.txt")
        state = app.state_for("client-a")
        calls = []
        d = _tomorrow()
        params = {
            "dry_run": True,
            "monitor_interval_seconds": 20,
            "max_attempts": 2,
            "monitor_date": d,
            "monitor_selections": [
                {
                    "court": {"site_id": 3692729935134806, "site_name": "1号场"},
                    "time_slot": {
                        "start_time": "09:00",
                        "end_time": "10:00",
                        "start_timestamp": 1779930000,
                        "end_timestamp": 1779933600,
                        "price": "75",
                        "times": "1",
                    },
                }
            ],
        }

        with patch.object(app, "_send_monitor_round", side_effect=lambda *args: {"success": False}):
            with patch.object(state.stop_event, "wait", side_effect=lambda seconds: calls.append(seconds)):
                app._run_monitor_loop(state, params)

        self.assertEqual(calls, [20])
        self.assertTrue(any("监听间隔 20 秒" in line for line in state.logs))


if __name__ == "__main__":
    unittest.main()
