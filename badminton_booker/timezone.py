from __future__ import annotations

import os
import time


DEFAULT_TIMEZONE = "Asia/Shanghai"


def apply_timezone(timezone: str = DEFAULT_TIMEZONE) -> None:
    os.environ["TZ"] = timezone
    if hasattr(time, "tzset"):
        time.tzset()
