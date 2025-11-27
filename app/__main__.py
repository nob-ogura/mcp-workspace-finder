from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Workspace finder CLI (placeholder). Use this entry point to explore and "
        "search across MCP servers."
    ),
)

console = Console()


@app.callback()
def main(ctx: typer.Context):
    """CLI entry point. Future commands will be added in Phase 1+."""
    # Typer handles help rendering; keep placeholder for future expansion.
    pass


if __name__ == "__main__":
    app()
