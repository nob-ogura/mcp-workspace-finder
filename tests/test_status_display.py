import builtins
from collections.abc import Iterator
from pathlib import Path

from rich.console import Console

from app.config import RunMode, ResolvedService
from app.process import RuntimeStatus
from app.status_display import emit_new_warnings, render_status_table
from app.__main__ import repl_loop


def _iter_inputs(values: list[str]) -> Iterator[str]:
    it: Iterator[str] = iter(values)
    for value in it:
        yield value


def _resolved(name: str, mode: RunMode) -> ResolvedService:
    return ResolvedService(
        name=name,
        declared_mode=mode,
        selected_mode=mode,
        missing_keys=[],
        warning=None,
    )


def _status(name: str, mode: RunMode, *, ready: bool, restart_count: int = 0, warning: str | None = None):
    return RuntimeStatus(
        name=name,
        mode=mode,
        command=[],
        process=None,
        ready=ready,
        restart_count=restart_count,
        warning=warning,
        error=warning,
    )


def _write_config(tmp_path: Path) -> Path:
    text = """
    services:
      slack:
        mode: mock
        kind: python
        exec: /bin/echo
        args: []
        workdir: .
        env: {}
        mock:
          exec: /bin/echo
          args: []
          workdir: .
      github:
        mode: mock
        kind: python
        exec: /bin/echo
        args: []
        workdir: .
        env: {}
        mock:
          exec: /bin/echo
          args: []
          workdir: .
    """
    path = tmp_path / "servers.yaml"
    path.write_text(text)
    return path


def test_render_status_table_shows_modes_and_ready():
    console = Console(record=True)
    resolved = {
        "slack": _resolved("slack", RunMode.MOCK),
        "github": _resolved("github", RunMode.MOCK),
    }
    statuses = {
        "slack": _status("slack", RunMode.MOCK, ready=True),
        "github": _status("github", RunMode.MOCK, ready=True),
    }

    render_status_table(console, resolved, statuses, title="startup")

    output = console.export_text()
    assert "slack" in output
    assert "github" in output
    assert "mock" in output
    assert "ready" in output
    assert "Warning:" not in output


def test_warning_emitted_once_and_status_updates():
    console = Console(record=True)
    seen: set[tuple[str, str]] = set()
    resolved = {"github": _resolved("github", RunMode.MOCK)}
    statuses = {"github": _status("github", RunMode.MOCK, ready=False, warning="first failure")}

    emit_new_warnings(console, statuses, seen)
    render_status_table(console, resolved, statuses, title="after-start", seen_warnings=seen)

    statuses["github"].warning = None
    statuses["github"].error = None
    statuses["github"].ready = True
    statuses["github"].restart_count = 1

    emit_new_warnings(console, statuses, seen)
    render_status_table(console, resolved, statuses, title="after-restart", seen_warnings=seen)

    output = console.export_text()
    assert output.count("first failure") == 1
    assert "ready" in output
    assert "restart" in output.lower()


def test_repl_outputs_status_board(monkeypatch, tmp_path, capsys):
    config_path = _write_config(tmp_path)
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)

    async def fake_run(definitions, resolved, *, readiness_timeout: float, monitor_window: float):
        console = Console()
        statuses = {
            name: _status(name, service.selected_mode, ready=True)
            for name, service in resolved.items()
        }
        render_status_table(console, resolved, statuses, title="fake")
        return statuses

    monkeypatch.setattr("app.__main__._run_startup_with_status_board", fake_run)
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(_iter_inputs(["exit"])))

    repl_loop(
        force_mock=True,
        config_path=config_path,
        readiness_timeout=0.05,
        monitor_window=0.01,
    )

    out = capsys.readouterr().out
    assert "Service" in out or "service" in out.lower()
    assert "ready" in out
