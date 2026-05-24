import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import badminton_booker.webapp as webapp
from badminton_booker.webapp import BookingWebApp, _admin_page


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
        message = webapp._success_notification_message(
            {
                "success_units": 2,
                "success_targets": [
                    "2026/05/28 4号场 07:00-08:00",
                    "2026/05/28 4号场 08:00-09:00",
                ],
            }
        )

        self.assertIn("【羽毛球抢票】抢票成功", message)
        self.assertIn("成功时间数：2", message)
        self.assertIn("2026/05/28 4号场 07:00-08:00", message)
        self.assertIn("2026/05/28 4号场 08:00-09:00", message)

    def test_success_round_sends_sync_notification_once(self) -> None:
        app = BookingWebApp("request.txt")
        state = app.state_for("client-a")
        sent = []

        def fake_send(message):
            sent.append(message)
            return "企业微信通知已发送"

        params = {
            "dry_run": False,
            "headers": {},
            "dates": ["2026/05/28"],
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
        self.assertEqual(len(sent), 1)
        self.assertIn("【羽毛球抢票】抢票成功", sent[0])
        self.assertIn("2026/05/28 4号场 07:00-08:00", sent[0])
        self.assertTrue(any("企业微信通知已发送" in line for line in app.status("client-a")["logs"]))


if __name__ == "__main__":
    unittest.main()
