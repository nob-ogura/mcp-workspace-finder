import builtins
import logging
import textwrap
from pathlib import Path

import pytest

import app.__main__ as main_module


def _iter_inputs(values):
    it = iter(values)
    return lambda _prompt="": next(it)


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "servers.yaml"
    path.write_text(
        textwrap.dedent(
            """
            services:
              slack:
                mode: mock
                kind: binary
                exec: /bin/echo
                args: []
                workdir: .
                env: {}
                mock:
                  exec: /bin/echo
                  args: ["mock-slack"]
                  workdir: .
            """
        )
    )
    return path


def _suppress_startup(monkeypatch):
    monkeypatch.setattr(main_module, "show_startup_status", lambda summary: None)


def test_repl_help_lists_internal_commands(monkeypatch, tmp_path, capsys):
    config_path = _write_config(tmp_path)
    _suppress_startup(monkeypatch)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["help", "quit"]))

    main_module.repl_loop(force_mock=True, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    for keyword in ["quit", "reload", "help", "debug on", "debug off", "logpath"]:
        assert keyword in out


def test_repl_debug_toggle_changes_log_level(monkeypatch, tmp_path, capsys):
    config_path = _write_config(tmp_path)
    _suppress_startup(monkeypatch)
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["debug on", "debug off", "quit"]))

    main_module.repl_loop(force_mock=True, config_path=config_path, start_services=False)

    out = capsys.readouterr().out.lower()
    assert "debug logging enabled" in out
    assert "debug logging disabled" in out
    assert logging.getLogger().level == logging.WARNING


def test_repl_reload_then_quit(monkeypatch, tmp_path, capsys):
    config_path = _write_config(tmp_path)
    _suppress_startup(monkeypatch)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["reload", "quit"]))

    main_module.repl_loop(force_mock=True, config_path=config_path, start_services=False)

    out = capsys.readouterr().out.lower()
    assert "reloading services" in out
    assert "reload complete" in out
    assert "goodbye" in out


def test_repl_logpath_prints_current_log_file(monkeypatch, tmp_path, capsys):
    config_path = _write_config(tmp_path)
    _suppress_startup(monkeypatch)
    monkeypatch.setenv("MCP_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(builtins, "input", _iter_inputs(["logpath", "quit"]))

    main_module.repl_loop(force_mock=True, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    expected = (Path(tmp_path) / "workspace-finder.log").resolve()
    assert str(expected) in out
