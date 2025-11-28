from __future__ import annotations

import time
from collections.abc import Sequence

from rich.console import Console


class ProgressDisplay:
    """Render a simple sequential progress indicator with a single spinner."""

    def __init__(self, console: Console):
        self.console = console

    def run(self, steps: Sequence[str], *, delay: float = 0.0, spinner: str = "dots") -> None:
        steps = tuple(steps)
        if not steps:
            return

        with self.console.status(steps[0], spinner=spinner) as status:
            for label in steps:
                status.update(label)
                # also emit the step so it remains visible after the spinner clears
                self.console.print(f"[cyan]{label}[/]")
                if delay > 0:
                    time.sleep(delay)
