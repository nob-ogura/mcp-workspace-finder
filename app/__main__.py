from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Mapping
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from dotenv import load_dotenv, dotenv_values

from app.config import (
    RunMode,
    ServerDefinition,
    load_server_definitions,
    mode_summary,
    resolve_service_modes,
)
from app.process import launch_services_async, monitor_services
from app.status_display import emit_new_warnings, render_status_table
from app.smoke import format_result_line, run_smoke_checks, write_report
from app.logging_utils import install_log_masking

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
READINESS_TIMEOUT = 15.0
MONITOR_WINDOW = 0.8

# Emit warnings/errors once to stderr so startup issues are visible in CLI
logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
install_log_masking()


class InputMode(Enum):
    REPL = "repl"
    ONESHOT = "oneshot"
    HELP = "help"


def _stream_isatty(stream) -> bool:
    """Safe isatty check that tolerates StringIO in tests."""
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def determine_input_mode(query_option: str | None) -> tuple[InputMode, str | None, str]:
    """
    Decide whether to launch the REPL or run a one-shot query based on input sources.

    Returns (mode, query_text, source_label).
    """
    stdin_tty = _stream_isatty(sys.stdin)
    stdout_tty = _stream_isatty(sys.stdout)

    if query_option is not None:
        return InputMode.ONESHOT, query_option, "--query"

    if not stdin_tty:
        piped_text = sys.stdin.read()
        return InputMode.ONESHOT, piped_text.strip(), "stdin"

    if stdin_tty and stdout_tty:
        return InputMode.REPL, None, "tty"

    return InputMode.HELP, None, "help"


def real_smoke_enabled(force_mock: bool, allow_real_env: str | bool | None = None) -> bool:
    """Return True when real smoke should run based on env and CLI flags."""
    if force_mock:
        return False
    flag = os.getenv("ALLOW_REAL") if allow_real_env is None else allow_real_env
    if isinstance(flag, bool):
        return flag
    return flag == "1"


def _determine_env_path(config_path: Path | None, env_path: Path | None = None) -> Path:
    if env_path:
        return env_path.resolve()
    if config_path:
        return Path(config_path).resolve().parent / ".env"
    return (Path.cwd() / ".env").resolve()


def should_load_dotenv(
    force_mock: bool,
    definitions: Mapping[str, "ServerDefinition"],
    *,
    config_path: Path | None = None,
    env_path: Path | None = None,
    allow_real_env: str | None = None,
) -> bool:
    """Return True when .env should be loaded for real mode."""

    path = _determine_env_path(config_path, env_path)
    if not path.exists():
        return False
    if force_mock:
        return False

    allow_real = os.getenv("ALLOW_REAL") if allow_real_env is None else allow_real_env
    if allow_real != "1":
        allow_from_file = dotenv_values(path).get("ALLOW_REAL")
        if allow_from_file != "1":
            return False

    has_real_mode = any(definition.declared_mode is RunMode.REAL for definition in definitions.values())
    return has_real_mode


def maybe_load_dotenv(
    force_mock: bool,
    definitions: Mapping[str, "ServerDefinition"],
    *,
    config_path: Path | None = None,
    env_path: Path | None = None,
) -> bool:
    """Load .env when running real mode with explicit opt-in.

    Conditions:
    - .env exists
    - --mock is not provided
    - ALLOW_REAL=1 is set in the environment
    - servers.yaml contains at least one service with mode=real
    """

    path = _determine_env_path(config_path, env_path)
    if not should_load_dotenv(force_mock, definitions, config_path=config_path, env_path=path):
        return False

    load_dotenv(dotenv_path=path)
    console.print(f"[green].env loaded[/] ({path})")
    return True


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
    _ = maybe_load_dotenv(force_mock, definitions, config_path=config_path)
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

    if smoke_enabled:
        all_real = all(decision.selected_mode is RunMode.REAL for decision in resolved.values())
        status_label = "実施可" if all_real else "未実施（欠損あり）"
        color = "green" if all_real else "yellow"
        console.print(f"[{color}]real smoke status:[/] {status_label}")

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


def run_oneshot(
    query: str,
    *,
    force_mock: bool,
    config_path: Path | None = None,
) -> None:
    """Placeholder one-shot execution path; will be extended in later tasks."""
    console.print(f"[bold cyan]one-shot mode[/] query: {query}")


@app.callback()
def main(
    ctx: typer.Context,
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Force mock mode; skips real credential validation.",
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        "-q",
        help="Run one query non-interactively and exit.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to servers.yaml. Defaults to repo root servers.yaml.",
    ),
):
    """
    CLI entry point. In TTY without subcommands, enter the REPL;
    otherwise fall back to Typer's help/command handling.
    """
    if ctx.invoked_subcommand:
        return

    mode, query_text, source = determine_input_mode(query)
    console.print(f"input mode: {mode.name} ({source})")

    if mode is InputMode.ONESHOT:
        run_oneshot(query_text or "", force_mock=mock, config_path=config)
        raise typer.Exit(code=0)

    if mode is InputMode.REPL:
        repl_loop(force_mock=mock, config_path=config)
        return

    typer.echo(ctx.get_help())
    raise typer.Exit(code=0)


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


@app.command()
def smoke(
    mock: bool = typer.Option(
        False,
        "--mock",
        help="Force mock mode; skips real credential validation.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to servers.yaml. Defaults to repo root servers.yaml.",
    ),
    report: Path | None = typer.Option(
        None,
        "--report",
        "-r",
        help="Optional JSON report output path.",
    ),
):
    """Run real-mode smoke searches for Slack/GitHub/Drive."""
    definitions = load_server_definitions(config)
    _ = maybe_load_dotenv(mock, definitions, config_path=config)

    if not real_smoke_enabled(mock):
        console.print("[yellow]real smoke skipped (mock mode)[/]")
        raise typer.Exit(code=0)

    resolved = resolve_service_modes(definitions, force_mock=mock, allow_real=True)
    not_real = [name for name, decision in resolved.items() if decision.selected_mode is not RunMode.REAL]
    if not_real:
        console.print(
            "[yellow]real smoke skipped (not all services real: "
            + ", ".join(not_real)
            + ")[/]"
        )
        raise typer.Exit(code=1)

    results = run_smoke_checks(resolved)
    for result in results.values():
        color = "green" if result.ok else "red"
        console.print(f"[{color}]{format_result_line(result)}[/]")

    if report:
        write_report(report, results)
        console.print(f"Report written to {report}")

    if all(result.ok for result in results.values()):
        console.print("[green]real smoke passed[/]")
        raise typer.Exit(code=0)

    console.print("[red]real smoke failed[/]")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
