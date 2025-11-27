from __future__ import annotations

from typing import Mapping

from rich.console import Console
from rich.table import Table

from app.config import ResolvedService
from app.process import RuntimeStatus


def _status_label(status: RuntimeStatus | None) -> str:
    if status is None:
        return "[dim]skipped[/]"
    if status.ready:
        return "[green]ready[/]"
    if status.error:
        return "[red]failed[/]"
    if status.warning:
        return "[yellow]warning[/]"
    if status.process:
        return "[cyan]starting[/]"
    return "[dim]stopped[/]"


def render_status_table(
    console: Console,
    resolved: Mapping[str, ResolvedService],
    statuses: Mapping[str, RuntimeStatus],
    *,
    title: str = "Service status",
    seen_warnings: set[tuple[str, str]] | None = None,
) -> None:
    table = Table(title=title)
    table.add_column("Service", style="bold")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("Restarts", justify="right")
    table.add_column("Warning", overflow="fold")

    for name in resolved.keys():
        decision = resolved[name]
        current = statuses.get(name)
        warning = ""
        if current:
            warning = current.warning or current.error or ""
            if seen_warnings is not None and current.warning and (name, current.warning) in seen_warnings:
                warning = ""
        table.add_row(
            name,
            decision.selected_mode.value,
            _status_label(current),
            str(current.restart_count if current else 0),
            warning,
        )

    console.print(table)


def emit_new_warnings(
    console: Console,
    statuses: Mapping[str, RuntimeStatus],
    seen: set[tuple[str, str]],
) -> None:
    for name, status in statuses.items():
        if not status.warning:
            continue
        key = (name, status.warning)
        if key in seen:
            continue
        console.print(f"[yellow]Warning:[/] {name}: {status.warning}")
        seen.add(key)
