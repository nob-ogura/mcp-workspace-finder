#!/usr/bin/env python3
"""Minimal mock GitHub MCP server placeholder."""

import signal
import sys
import time


def _handle_term(signum, frame):  # noqa: D401, D417
    """Exit cleanly on SIGTERM."""
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_term)
    print("mock github server ready", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
