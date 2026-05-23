import unittest

from badminton_booker.config import (
    AppConfig,
    BookingConfig,
    NotificationConfig,
    RequestFileConfig,
    TargetConfig,
)
from badminton_booker.notifier import Notifier
from badminton_booker.providers.demo import DemoProvider
from badminton_booker.scheduler import BookingRunner


class BookingRunnerTest(unittest.TestCase):
    def test_run_once_reserves_matching_slot(self) -> None:
        config = AppConfig(
            provider="demo",
            poll_interval_seconds=1,
            max_attempts=1,
            target=TargetConfig(
                dates=["2026-05-24"],
                time_ranges=["19:00-20:00"],
                court_names=["Court 1"],
            ),
            booking=BookingConfig(
                user_name="Tester",
                phone="13800000000",
                participants=4,
            ),
            notification=NotificationConfig(),
            request_file=RequestFileConfig(),
        )

        runner = BookingRunner(config, DemoProvider(), Notifier(config.notification))
        result = runner.run_once()

        self.assertTrue(result.success)
        self.assertIsNotNone(result.order_id)


if __name__ == "__main__":
    unittest.main()
