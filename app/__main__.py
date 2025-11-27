from __future__ import annotations

import os
import sys
import time

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


def warn_missing_keys() -> None:
    """Emit a one-time warning when required credentials are absent."""
    missing = [key for key in REQUIRED_KEYS if not os.getenv(key)]
    if missing:
        console.print(
            "[yellow]Warning:[/] Missing credentials "
            f"{', '.join(missing)} — running in limited mode."
        )


def show_startup_status() -> None:
    """Display a short status spinner to indicate startup progress."""
    with console.status("Preparing workspace finder (stub)...", spinner="dots"):
        time.sleep(0.1)


def repl_loop() -> None:
    """Minimal REPL that echoes input and stays alive until the user exits."""
    warn_missing_keys()
    show_startup_status()
    console.print("[bold cyan]workspace-finder[/] REPL ready. Type 'exit' to quit.")

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
def main(ctx: typer.Context):
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

    repl_loop()


@app.command()
def repl():
    """Explicitly start the REPL even when invoked as a subcommand."""
    repl_loop()


if __name__ == "__main__":
    app()
