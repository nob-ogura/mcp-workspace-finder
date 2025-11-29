from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Mapping
from typing import Any, Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from dotenv import load_dotenv, dotenv_values

from app.config import (
    RunMode,
    ServerDefinition,
    load_server_definitions,
    cli_override_warning,
    mode_summary,
    resolve_service_modes,
)
from app.process import launch_services_async, monitor_services
from app.status_display import emit_new_warnings, render_status_table
from app.smoke import format_result_line, run_smoke_checks, write_report
from app.logging_utils import (
    configure_file_logging,
    install_log_masking,
    set_debug_logging,
)
from app.progress_display import ProgressDisplay
from app.llm_search import generate_search_parameters, SearchGenerationResult
from app.summary_display import render_summary_with_links
from app.llm_client import create_llm_client
from app.mcp_runners import run_oneshot_with_mcp

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
ONESHOT_PROGRESS_STEPS: tuple[str, ...] = (
    "Preparing query...",
    "Searching sources...",
    "Summarizing...",
)

# Emit warnings/errors once to stderr so startup issues are visible in CLI
logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s")
install_log_masking()


class InputMode(Enum):
    REPL = "repl"
    ONESHOT = "oneshot"
    HELP = "help"


@dataclass
class ReplContext:
    log_path: Path
    debug_enabled: bool = False


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


def _render_repl_help() -> None:
    """Print available internal REPL commands."""
    commands = [
        ("help", "Show this help message"),
        ("quit", "Exit the REPL"),
        ("reload", "Reload server connections"),
        ("debug on", "Enable debug logging"),
        ("debug off", "Disable debug logging"),
        ("logpath", "Show current log file location"),
    ]
    console.print("[cyan]Available commands:[/]")
    for name, description in commands:
        console.print(f"  {name:<10} {description}")


def _handle_repl_command(line: str, context: ReplContext) -> tuple[bool, bool]:
    """
    Handle internal REPL commands.

    Returns (handled, should_exit).
    """
    raw = line.strip()
    lowered = raw.lower()

    if lowered in {"quit", "exit"}:
        console.print("Goodbye.")
        return True, True

    if lowered == "help":
        _render_repl_help()
        return True, False

    if lowered == "reload":
        console.print("[cyan]Reloading services...[/]")
        console.print("[green]Reload complete.[/]")
        return True, False

    if lowered.startswith("debug"):
        parts = lowered.split()
        if len(parts) == 2 and parts[1] in {"on", "off"}:
            enabled = parts[1] == "on"
            set_debug_logging(enabled)
            context.debug_enabled = enabled
            message = "Debug logging enabled" if enabled else "Debug logging disabled"
            color = "green" if enabled else "yellow"
            console.print(f"[{color}]{message}[/]")
            return True, False

    if lowered == "logpath":
        print(str(context.log_path))
        return True, False

    return False, False


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
    llm_client: Any | None = None,
    search_runner: Callable[[list[dict[str, Any]]], Any] | None = None,
    summarizer: Callable[[str, list[Any], Any | None], Any] | None = None,
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

    override = cli_override_warning(
        definitions,
        force_mock=force_mock,
        allow_real=smoke_enabled,
    )
    if override:
        console.print(f"[yellow]Warning:[/] {override}")

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

    log_path = configure_file_logging()
    context = ReplContext(
        log_path=log_path,
        debug_enabled=logging.getLogger().getEffectiveLevel() <= logging.DEBUG,
    )

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

        handled, should_exit = _handle_repl_command(line, context)
        if should_exit:
            break
        if handled:
            continue

        # Reuse one-shot pipeline for actual queries
        run_oneshot(
            line,
            force_mock=force_mock,
            config_path=config_path,
            llm_client=llm_client,
            search_runner=search_runner,
            summarizer=summarizer,
        )


def _looks_like_fetch_results(items: list[Any]) -> bool:
    """Heuristic: detect Phase6 fetch results (have service/title/uri/content)."""
    if not items:
        return False
    sample = items[0]
    return all(hasattr(sample, field) for field in ("service", "title", "uri", "content"))


def _render_summary_output(payload: Any, alternatives: list[str]) -> bool:
    """Render summary + links when a summary-style payload is provided."""
    if payload is None:
        return False

    # Support both SearchFetchSummaryResult and SummaryPipelineResult
    summary_md = getattr(payload, "summary_markdown", None)
    links = getattr(payload, "links", None)

    if summary_md is None or links is None:
        return False

    render_summary_with_links(console, summary_md, links, alternatives=alternatives)
    return True


def run_oneshot_with_mcp_sync(
    query: str,
    *,
    force_mock: bool,
    config_path: Path | None = None,
    llm_client: Any | None = None,
) -> None:
    """Execute oneshot query using MCP servers synchronously.

    This function starts MCP servers, executes search/fetch/summarize pipeline,
    and displays results with evidence links.
    """
    normalized_query = (query or "").strip()
    ProgressDisplay(console).run(ONESHOT_PROGRESS_STEPS, delay=0.02)

    try:
        result = asyncio.run(
            run_oneshot_with_mcp(
                normalized_query,
                force_mock=force_mock,
                config_path=config_path,
                llm_client=llm_client,
            )
        )

        if result is None:
            console.print("[yellow]MCP サーバーを起動できませんでした。--mock モードで再試行してください。[/]")
            return

        # Render summary with evidence links and alternatives
        alternatives = getattr(result, "alternatives", [])
        if _render_summary_output(result, alternatives):
            return

        # Fallback display
        console.print("[bold green]Result:[/]")
        if result.documents:
            for doc in result.documents:
                console.print(f"- [{doc.service}] {doc.title}: {doc.uri}")
        else:
            console.print("[yellow]検索結果がありません[/]")

    except Exception as exc:
        console.print(f"[red]エラー: {exc}[/]")
        logging.exception("oneshot MCP pipeline failed")


def run_oneshot(
    query: str,
    *,
    force_mock: bool,
    config_path: Path | None = None,
    llm_client: Any | None = None,
    search_runner: Callable[[list[dict[str, Any]]], Any] | None = None,
    summarizer: Callable[[str, list[Any], Any | None], Any] | None = None,
) -> None:
    """Execute a single query non-interactively then exit."""
    normalized_query = (query or "").strip()
    ProgressDisplay(console).run(ONESHOT_PROGRESS_STEPS, delay=0.02)

    # Generate search parameters using LLM
    generation = _generate_with_fallback(normalized_query, llm_client)

    # Debug: show generated searches if LLM was used
    if llm_client is not None and generation.searches:
        console.print(f"[dim]Generated {len(generation.searches)} search queries[/]")
        for s in generation.searches:
            console.print(f"[dim]  - {s.get('service')}: {s.get('query')}[/]")

    # Use provided runner or fall back to echo mode
    runner = search_runner or (lambda searches: [normalized_query] if normalized_query else ["result"])
    search_results = runner(generation.searches)

    # Runner may already return a summary result (full pipeline)
    if _render_summary_output(search_results, generation.alternatives):
        return

    if not search_results:
        _render_alternatives_only(generation.alternatives)
        return

    summary_payload = None
    if summarizer is not None:
        summary_payload = summarizer(normalized_query, search_results, llm_client)
    elif llm_client is not None and isinstance(search_results, list) and _looks_like_fetch_results(search_results):
        # When fetch results are already available and we have an LLM client, run summary pipeline by default.
        from app.summary_pipeline import run_summary_pipeline

        summary_payload = run_summary_pipeline(normalized_query, search_results, llm_client)

    if _render_summary_output(summary_payload, generation.alternatives):
        return

    # Fall back to simple output when no summary was generated
    console.print("[bold green]Result:[/]")
    if generation.searches and (not search_results or search_results == [normalized_query]):
        console.print("[yellow]Note: MCP search runners are not yet connected.[/]")
        console.print("[yellow]Search parameters were generated by LLM but actual MCP search was not executed.[/]")
        console.print("\n[bold]Generated search parameters:[/]")
        for s in generation.searches:
            console.print(f"  - {s.get('service')}: {s.get('query')}")
        if generation.alternatives:
            console.print("\n[bold]Alternative queries:[/]")
            for alt in generation.alternatives:
                console.print(f"  - {alt}")
    else:
        for item in search_results:
            console.print(f"- {item}")


def _generate_with_fallback(query: str, llm_client: Any | None) -> SearchGenerationResult:
    if llm_client is not None:
        return generate_search_parameters(query, llm_client)

    base = query or "workspace"
    alternatives = [base, f"{base} 資料"]
    return SearchGenerationResult(searches=[], alternatives=alternatives)


def _render_alternatives_only(alternatives: list[str] | None) -> None:
    cleaned = [alt.strip() for alt in alternatives or [] if isinstance(alt, str) and alt.strip()]

    if not cleaned:
        console.print("[yellow]代替クエリを生成できませんでした[/]")
        return

    render_summary_with_links(console, "", [], alternatives=cleaned)


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

    # Load .env and create LLM client for oneshot mode
    definitions = load_server_definitions(config)
    _ = maybe_load_dotenv(mock, definitions, config_path=config)

    # Create LLM client (works in both mock and real mode)
    llm_client = create_llm_client()
    if llm_client:
        console.print("[green]LLM client initialized[/]")
    else:
        console.print("[yellow]LLM client not available (OPENAI_API_KEY not set)[/]")

    if mode is InputMode.ONESHOT:
        # Use MCP servers for search if llm_client is available
        if llm_client is not None:
            run_oneshot_with_mcp_sync(
                query_text or "",
                force_mock=mock,
                config_path=config,
                llm_client=llm_client,
            )
        else:
            # Fallback to simple echo mode without MCP
            run_oneshot(
                query_text or "",
                force_mock=mock,
                config_path=config,
                llm_client=None,
            )
        raise typer.Exit(code=0)

    if mode is InputMode.REPL:
        repl_loop(force_mock=mock, config_path=config, llm_client=llm_client)
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
