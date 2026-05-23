import unittest

from badminton_booker.config import RequestFileConfig, TargetConfig
from badminton_booker.providers.request_file import RequestFileProvider


class RequestFileProviderTest(unittest.TestCase):
    def test_reads_submit_request_slots(self) -> None:
        provider = RequestFileProvider(
            RequestFileConfig(path="request.txt", submit_path="/v2/reserve/submit?", dry_run=True)
        )

        slots = provider.list_slots(TargetConfig(dates=[], time_ranges=[], court_names=[]))

        self.assertGreaterEqual(len(slots), 1)
        self.assertRegex(slots[0].date, r"^20\d{2}-\d{2}-\d{2}$")
        self.assertEqual(slots[0].court_name, "4号场")


if __name__ == "__main__":
    unittest.main()
