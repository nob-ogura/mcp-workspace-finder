from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from rich.console import Console

from app.config import load_server_definitions, mode_summary, resolve_service_modes

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



def show_startup_status(mode_summary_text: str) -> None:
    """Display a short status spinner to indicate startup progress."""
    with console.status(
        f"Preparing workspace finder ({mode_summary_text})...", spinner="dots"
    ):
        time.sleep(0.1)


def repl_loop(force_mock: bool, config_path: Path | None = None) -> None:
    """Minimal REPL that echoes input and stays alive until the user exits."""
    definitions = load_server_definitions(config_path)
    resolved = resolve_service_modes(definitions, force_mock=force_mock)

    for decision in resolved.values():
        if decision.warning:
            console.print(f"[yellow]Warning:[/] {decision.warning}")

    summary = mode_summary(resolved)
    console.print(f"Selected modes: {summary}")

    show_startup_status(summary)
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
