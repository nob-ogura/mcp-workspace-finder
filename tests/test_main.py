import builtins
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Callable

import pytest

from app.config import RunMode, load_server_definitions, resolve_service_modes
from app.__main__ import repl_loop, show_startup_status


def _iter_inputs(values: list[str]) -> Callable[[str], str]:
    it: Iterator[str] = iter(values)
    return lambda _prompt="": next(it)


def _write_config(tmp_path: Path, yaml_text: str) -> Path:
    path = tmp_path / "servers.yaml"
    path.write_text(textwrap.dedent(yaml_text))
    return path


def test_load_server_definitions_extracts_required_keys(tmp_path):
    path = _write_config(
        tmp_path,
        """
        services:
          slack:
            mode: real
            kind: binary
            exec: /bin/echo
            args: []
            workdir: .
            env:
              SLACK_USER_TOKEN: ${SLACK_USER_TOKEN}
            mock:
              exec: /bin/echo
              args: ["mock"]
              workdir: .
        """,
    )

    configs = load_server_definitions(path)

    assert set(configs.keys()) == {"slack"}
    slack = configs["slack"]
    assert slack.declared_mode is RunMode.REAL
    assert "SLACK_USER_TOKEN" in slack.required_env_keys()


def test_cli_override_warns_and_forces_mock(monkeypatch, tmp_path, capsys):
    config_path = _write_config(
        tmp_path,
        """
        services:
          slack:
            mode: real
            kind: binary
            exec: /bin/echo
            args: []
            workdir: .
            env:
              SLACK_USER_TOKEN: ${SLACK_USER_TOKEN}
            mock:
              exec: /bin/echo
              args: ["mock-slack"]
              workdir: .
        """,
    )
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv("SLACK_USER_TOKEN", "token-present")
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=True, config_path=config_path)

    out = capsys.readouterr().out
    assert out.count("CLI override") == 1
    assert "slack=mock" in out


def test_allow_real_selects_real_when_keys_present(monkeypatch, tmp_path, capsys):
    config_path = _write_config(
        tmp_path,
        """
        services:
          github:
            mode: real
            kind: python
            exec: python
            args: ["-m", "github_mcp_server"]
            workdir: .
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
            mock:
              exec: python
              args: ["-m", "tests.mocks.github_server"]
              workdir: .
        """,
    )
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "token-present")
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False, config_path=config_path)

    out = capsys.readouterr().out
    assert "github=real" in out
    assert "Warning" not in out


def test_missing_keys_fallback_to_mock(monkeypatch, tmp_path, capsys):
    config_path = _write_config(
        tmp_path,
        """
        services:
          slack:
            mode: real
            kind: binary
            exec: /bin/echo
            args: []
            workdir: .
            env:
              SLACK_USER_TOKEN: ${SLACK_USER_TOKEN}
            mock:
              exec: /bin/echo
              args: ["mock-slack"]
              workdir: .
        """,
    )
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.delenv("SLACK_USER_TOKEN", raising=False)
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False, config_path=config_path)

    out = capsys.readouterr().out
    assert "鍵不足によりモックへフォールバック" in out
    assert "slack=mock" in out
    assert "Warning" in out


def test_show_startup_status_uses_spinner(mocker):
    sleep = mocker.patch("time.sleep")
    status = mocker.patch("app.__main__.console.status")

    show_startup_status("slack=mock")

    status.assert_called_once()
    args, _ = status.call_args
    assert "slack=mock" in args[0]
    sleep.assert_called_once_with(0.1)
