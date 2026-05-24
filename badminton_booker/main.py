from __future__ import annotations

import argparse
import sys

from .config import load_config
from .notifier import Notifier
from .providers import create_provider
from .scheduler import BookingRunner
from .timezone import apply_timezone
from .webapp import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Badminton court booking helper")
    parser.add_argument("--config", default="config.json", help="Path to JSON config file")
    parser.add_argument("--host", default="127.0.0.1", help="Web server host")
    parser.add_argument("--port", default=8765, type=int, help="Web server port")
    parser.add_argument("--request-file", default="request.txt", help="Captured request file for web mode")
    parser.add_argument("command", choices=["once", "watch", "web"], help="Run once, keep polling, or open web console")
    return parser


def main(argv: list[str] | None = None) -> int:
    apply_timezone()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "web":
        run_server(args.host, args.port, args.request_file)
        return 0

    config = load_config(args.config)
    provider = create_provider(config.provider, config)
    notifier = Notifier(config.notification)
    runner = BookingRunner(config, provider, notifier)

    result = runner.run_once() if args.command == "once" else runner.watch()
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
