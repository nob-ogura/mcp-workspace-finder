from __future__ import annotations

import os
import sys
import time
from enum import Enum

import typer
from rich.console import Console

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

REQUIRED_KEYS = ["OPENAI_API_KEY"]  # Placeholder list; expanded in later phases.


class RunMode(str, Enum):
    MOCK = "mock"
    REAL = "real"


def collect_missing_keys() -> list[str]:
    """Return required credential keys that are not set in the environment."""
    return [key for key in REQUIRED_KEYS if not os.getenv(key)]


def decide_mode(force_mock: bool) -> tuple[RunMode, str | None]:
    """Determine runtime mode and an optional warning message.

    Priority: --mock > ALLOW_REAL=1 (with keys present) > fallback to mock.
    """

    if force_mock:
        return RunMode.MOCK, None

    allow_real = os.getenv("ALLOW_REAL") == "1"
    missing_keys = collect_missing_keys()

    if allow_real and not missing_keys:
        return RunMode.REAL, None

    warning = None
    if missing_keys:
        warning = (
            "鍵不足によりモックへフォールバックします: "
            f"{', '.join(missing_keys)}"
        )

    return RunMode.MOCK, warning


def show_startup_status(mode: RunMode) -> None:
    """Display a short status spinner to indicate startup progress."""
    with console.status(
        f"Preparing workspace finder (mode: {mode.value})...", spinner="dots"
    ):
        time.sleep(0.1)


def repl_loop(force_mock: bool) -> None:
    """Minimal REPL that echoes input and stays alive until the user exits."""
    mode, warning = decide_mode(force_mock)

    if warning:
        console.print(f"[yellow]Warning:[/] {warning}")

    show_startup_status(mode)
    console.print(
        "[bold cyan]workspace-finder[/] REPL ready. "
        f"Mode: [bold]{mode.value}[/]. Type 'exit' to quit."
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
