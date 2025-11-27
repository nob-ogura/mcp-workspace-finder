from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import typer
from rich.console import Console

from app.config import load_server_definitions, mode_summary, resolve_service_modes
from app.process import launch_services_async, monitor_services
from app.status_display import emit_new_warnings, render_status_table

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
    help=(
        "Workspace finder CLI (placeholder). Use this entry point to explore and "
        "search across MCP servers."
    ),
)

console = Console()
READINESS_TIMEOUT = 1.0
MONITOR_WINDOW = 0.8

# Emit warnings/errors once to stderr so startup issues are visible in CLI
logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")


def real_smoke_enabled(force_mock: bool, allow_real_env: str | bool | None = None) -> bool:
    """Return True when real smoke should run based on env and CLI flags."""
    if force_mock:
        return False
    flag = os.getenv("ALLOW_REAL") if allow_real_env is None else allow_real_env
    if isinstance(flag, bool):
        return flag
    return flag == "1"


def show_startup_status(mode_summary_text: str) -> None:
    """Display a short status spinner to indicate startup progress."""
    with console.status(
        f"Preparing workspace finder ({mode_summary_text})...", spinner="dots"
    ):
        time.sleep(0.1)


async def _run_startup_with_status_board(
    definitions,
    resolved,
    *,
    readiness_timeout: float,
    monitor_window: float,
):
    """Start services, show a status table, then watch for early restarts."""
    statuses = await launch_services_async(
        definitions,
        resolved,
        readiness_timeout=readiness_timeout,
    )

    seen_warnings: set[tuple[str, str]] = set()
    emit_new_warnings(console, statuses, seen_warnings)
    render_status_table(
        console,
        resolved,
        statuses,
        title="Startup status",
        seen_warnings=seen_warnings,
    )

    if monitor_window > 0:
        await monitor_services(
            definitions,
            resolved,
            statuses,
            readiness_timeout=readiness_timeout,
            stop_after=monitor_window,
        )
        emit_new_warnings(console, statuses, seen_warnings)
        render_status_table(
            console,
            resolved,
            statuses,
            title="Monitoring status",
            seen_warnings=seen_warnings,
        )

    return statuses


def repl_loop(
    force_mock: bool,
    config_path: Path | None = None,
    *,
    start_services: bool = True,
    readiness_timeout: float = READINESS_TIMEOUT,
    monitor_window: float = MONITOR_WINDOW,
) -> None:
    """Minimal REPL that echoes input and stays alive until the user exits."""
    definitions = load_server_definitions(config_path)
    smoke_enabled = real_smoke_enabled(force_mock)
    console.print(
        "[green]real smoke enabled[/]"
        if smoke_enabled
        else "[yellow]real smoke skipped (mock mode)[/]"
    )

    resolved = resolve_service_modes(
        definitions,
        force_mock=force_mock,
        allow_real=smoke_enabled,
    )

    for decision in resolved.values():
        if decision.warning:
            console.print(f"[yellow]Warning:[/] {decision.warning}")

    summary = mode_summary(resolved)
    console.print(f"Selected modes: {summary}")

    show_startup_status(summary)

    if start_services:
        asyncio.run(
            _run_startup_with_status_board(
                definitions,
                resolved,
                readiness_timeout=readiness_timeout,
                monitor_window=monitor_window,
            )
        )

    console.print(
        "[bold cyan]workspace-finder[/] REPL ready. "
        f"Modes: [bold]{summary}[/]. Type 'exit' to quit."
    )

    while True:
        try:
            line = input("mcp> ").strip()
        except EOFError:
            console.print("\nEOF received; exiting.")
            break
        except KeyboardInterrupt:
            console.print("\nInterrupted. Exiting REPL.")
            break

        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            console.print("Goodbye.")
            break

        console.print(f"[dim]echo:[/] {line}")


@app.callback()
def main(
    ctx: typer.Context,
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Force mock mode; skips real credential validation.",
    ),
):
    """
    CLI entry point. In TTY without subcommands, enter the REPL;
    otherwise fall back to Typer's help/command handling.
    """
    if ctx.invoked_subcommand:
        return

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if not is_tty:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)

    repl_loop(force_mock=mock)


@app.command()
def repl(
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Force mock mode; skips real credential validation.",
    )
):
    """Explicitly start the REPL even when invoked as a subcommand."""
    repl_loop(force_mock=mock)


if __name__ == "__main__":
    app()
