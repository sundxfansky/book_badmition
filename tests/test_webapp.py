import unittest

from badminton_booker.webapp import BookingWebApp


class BookingWebAppTest(unittest.TestCase):
    def test_wx_token_is_not_saved_to_backend_state(self) -> None:
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

    def test_clients_have_independent_backend_state(self) -> None:
        app = BookingWebApp("request.txt")

        app.save_params("client-a", {"headers": {"shop-id": "shop-a"}})
        app.save_params("client-b", {"headers": {"shop-id": "shop-b"}})

        self.assertEqual(app.status("client-a")["params"]["headers"]["shop-id"], "shop-a")
        self.assertEqual(app.status("client-b")["params"]["headers"]["shop-id"], "shop-b")


if __name__ == "__main__":
    unittest.main()
