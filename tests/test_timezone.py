import os
import time
import unittest

from badminton_booker.timezone import DEFAULT_TIMEZONE, apply_timezone


class TimezoneTest(unittest.TestCase):
    def test_apply_timezone_sets_shanghai(self) -> None:
        original = os.environ.get("TZ")
        try:
            apply_timezone()

            self.assertEqual(os.environ["TZ"], DEFAULT_TIMEZONE)
            self.assertEqual(time.strftime("%z", time.localtime(0)), "+0800")
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            if hasattr(time, "tzset"):
                time.tzset()


if __name__ == "__main__":
    unittest.main()
