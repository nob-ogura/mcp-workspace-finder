import builtins
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Callable

import pytest
from typer.testing import CliRunner

import app.__main__ as main_module
from app.config import RunMode, load_server_definitions, resolve_service_modes
from app.__main__ import repl_loop, show_startup_status


def _iter_inputs(values: list[str]) -> Callable[[str], str]:
    it: Iterator[str] = iter(values)
    return lambda _prompt="": next(it)


def _write_config(tmp_path: Path, yaml_text: str) -> Path:
    path = tmp_path / "servers.yaml"
    path.write_text(textwrap.dedent(yaml_text))
    return path


def test_auto_mode_enters_repl_when_tty(monkeypatch):
    runner = CliRunner()
    called = {}

    monkeypatch.setattr(main_module, "repl_loop", lambda **kwargs: called.setdefault("repl", kwargs))
    monkeypatch.setattr(main_module, "_stream_isatty", lambda stream: True)

    result = runner.invoke(main_module.app, [])

    assert result.exit_code == 0
    assert "repl" in called
    assert "input mode: REPL" in result.output


def test_auto_mode_runs_oneshot_when_query_option(monkeypatch):
    runner = CliRunner()
    called = {}

    monkeypatch.setattr(main_module, "run_oneshot", lambda query, **kwargs: called.setdefault("oneshot", query))
    monkeypatch.setattr(main_module, "_stream_isatty", lambda stream: True)

    result = runner.invoke(main_module.app, ["--query", "社内ナレッジを検索"])

    assert result.exit_code == 0
    assert called["oneshot"] == "社内ナレッジを検索"
    assert "input mode: ONESHOT (--query)" in result.output
    assert "mcp>" not in result.output


def test_auto_mode_runs_oneshot_when_stdin_piped(monkeypatch):
    runner = CliRunner()
    called = {}

    def fake_isatty(stream):
        if stream is main_module.sys.stdin:
            return False
        return True

    monkeypatch.setattr(main_module, "_stream_isatty", fake_isatty)
    monkeypatch.setattr(main_module, "run_oneshot", lambda query, **kwargs: called.setdefault("oneshot", query))

    result = runner.invoke(main_module.app, [], input="Slack のDMを検索\n")

    assert result.exit_code == 0
    assert called["oneshot"] == "Slack のDMを検索"
    assert "input mode: ONESHOT (stdin)" in result.output
    assert "mcp>" not in result.output


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

    repl_loop(force_mock=True, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    assert out.count("CLI override") == 1
    assert "slack=mock" in out


def test_cli_override_when_allow_real_disabled(monkeypatch, tmp_path, capsys):
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
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
            mock:
              exec: /bin/echo
              args: ["mock-github"]
              workdir: .
        """,
    )
    monkeypatch.delenv("ALLOW_REAL", raising=False)
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    assert out.count("CLI override") == 1
    assert "slack=mock" in out
    assert "github=mock" in out
    assert "鍵不足" not in out


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

    repl_loop(force_mock=False, config_path=config_path, start_services=False)

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

    repl_loop(force_mock=False, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    assert "鍵不足によりモックへフォールバック" in out
    assert "slack=mock" in out
    assert "Warning" in out


def test_real_smoke_enabled_when_env_and_no_mock(monkeypatch, tmp_path, capsys):
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
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
            mock:
              exec: /bin/echo
              args: ["mock-github"]
              workdir: .
          drive:
            mode: real
            kind: node
            exec: /bin/echo
            args: []
            workdir: .
            env:
              DRIVE_TOKEN_PATH: ${DRIVE_TOKEN_PATH}
              GOOGLE_CREDENTIALS_PATH: ${GOOGLE_CREDENTIALS_PATH}
            mock:
              exec: /bin/echo
              args: ["mock-drive"]
              workdir: .
        """,
    )
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv("SLACK_USER_TOKEN", "token-slack")
    monkeypatch.setenv("GITHUB_TOKEN", "token-github")
    monkeypatch.setenv("DRIVE_TOKEN_PATH", "/tmp/token.json")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "/tmp/creds.json")
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    assert out.count("real smoke enabled") == 1
    assert "real smoke skipped" not in out
    assert "slack=real" in out
    assert "github=real" in out
    assert "drive=real" in out


def test_real_smoke_skipped_without_allow_real(monkeypatch, tmp_path, capsys):
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
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
            mock:
              exec: /bin/echo
              args: ["mock-github"]
              workdir: .
          drive:
            mode: real
            kind: node
            exec: /bin/echo
            args: []
            workdir: .
            env:
              DRIVE_TOKEN_PATH: ${DRIVE_TOKEN_PATH}
              GOOGLE_CREDENTIALS_PATH: ${GOOGLE_CREDENTIALS_PATH}
            mock:
              exec: /bin/echo
              args: ["mock-drive"]
              workdir: .
        """,
    )
    monkeypatch.delenv("ALLOW_REAL", raising=False)
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    assert out.count("real smoke skipped") == 1
    assert "real smoke enabled" not in out
    assert "slack=mock" in out
    assert "github=mock" in out
    assert "drive=mock" in out


def test_real_smoke_skipped_when_mock_option(monkeypatch, tmp_path, capsys):
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
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
            mock:
              exec: /bin/echo
              args: ["mock-github"]
              workdir: .
          drive:
            mode: real
            kind: node
            exec: /bin/echo
            args: []
            workdir: .
            env:
              DRIVE_TOKEN_PATH: ${DRIVE_TOKEN_PATH}
              GOOGLE_CREDENTIALS_PATH: ${GOOGLE_CREDENTIALS_PATH}
            mock:
              exec: /bin/echo
              args: ["mock-drive"]
              workdir: .
        """,
    )
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv("SLACK_USER_TOKEN", "token-slack")
    monkeypatch.setenv("GITHUB_TOKEN", "token-github")
    monkeypatch.setenv("DRIVE_TOKEN_PATH", "/tmp/token.json")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", "/tmp/creds.json")
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=True, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    assert out.count("real smoke skipped") == 1
    assert "real smoke enabled" not in out
    assert "slack=mock" in out
    assert "github=mock" in out
    assert "drive=mock" in out


def test_show_startup_status_uses_spinner(mocker):
    sleep = mocker.patch("time.sleep")
    status = mocker.patch("app.__main__.console.status")

    show_startup_status("slack=mock")

    status.assert_called_once()
    args, _ = status.call_args
    assert "slack=mock" in args[0]
    sleep.assert_called_once_with(0.1)


def test_dotenv_loaded_when_real_and_allow_real(monkeypatch, tmp_path, mocker, capsys):
    monkeypatch.chdir(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text("GITHUB_TOKEN=from_env\n")
    config_path = _write_config(
        tmp_path,
        """
        services:
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
        """,
    )

    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    loader = mocker.spy(main_module, "load_dotenv")
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False, config_path=config_path, start_services=False)

    loader.assert_called_once_with(dotenv_path=env_path)
    out = capsys.readouterr().out
    assert ".env loaded" in out


def test_dotenv_loads_when_allow_real_in_dotenv(monkeypatch, tmp_path, mocker, capsys):
    monkeypatch.chdir(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text("ALLOW_REAL=1\nGITHUB_TOKEN=from_env\n")
    config_path = _write_config(
        tmp_path,
        """
        services:
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
        """,
    )

    monkeypatch.delenv("ALLOW_REAL", raising=False)
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    loader = mocker.spy(main_module, "load_dotenv")
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False, config_path=config_path, start_services=False)

    loader.assert_called_once_with(dotenv_path=env_path)
    out = capsys.readouterr().out
    assert "real smoke enabled" in out


def test_dotenv_skipped_when_force_mock(monkeypatch, tmp_path, mocker, capsys):
    monkeypatch.chdir(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text("GITHUB_TOKEN=from_env\n")
    config_path = _write_config(
        tmp_path,
        """
        services:
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
        """,
    )

    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    loader = mocker.patch("app.__main__.load_dotenv")
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=True, config_path=config_path, start_services=False)

    loader.assert_not_called()
    out = capsys.readouterr().out
    assert ".env loaded" not in out


def test_auth_files_present_allow_real(monkeypatch, tmp_path, capsys):
    drive_token = tmp_path / "token.json"
    drive_token.write_text("{}")

    config_path = _write_config(
        tmp_path,
        f"""
        services:
          slack:
            mode: real
            kind: binary
            exec: /bin/echo
            args: []
            workdir: .
            env:
              SLACK_USER_TOKEN: ${{SLACK_USER_TOKEN}}
            mock:
              exec: /bin/echo
              args: ["mock-slack"]
              workdir: .
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${{GITHUB_TOKEN}}
            mock:
              exec: /bin/echo
              args: ["mock-github"]
              workdir: .
          drive:
            mode: real
            kind: node
            exec: /bin/echo
            args: []
            workdir: .
            env:
              DRIVE_TOKEN_PATH: ${{DRIVE_TOKEN_PATH}}
              GOOGLE_CREDENTIALS_PATH: ${{GOOGLE_CREDENTIALS_PATH}}
            auth_files:
              - path: ${{DRIVE_TOKEN_PATH}}
            mock:
              exec: /bin/echo
              args: ["mock-drive"]
              workdir: .
        """,
    )
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv("SLACK_USER_TOKEN", "token-slack")
    monkeypatch.setenv("GITHUB_TOKEN", "token-github")
    monkeypatch.setenv("DRIVE_TOKEN_PATH", str(drive_token))
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "creds.json"))
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    assert "drive=real" in out
    assert "認証ファイル不足" not in out
    assert "未実施（欠損あり）" not in out


def test_missing_auth_file_falls_back_and_marks_smoke_pending(monkeypatch, tmp_path, capsys):
    missing_token = tmp_path / "missing_token.json"

    config_path = _write_config(
        tmp_path,
        f"""
        services:
          slack:
            mode: real
            kind: binary
            exec: /bin/echo
            args: []
            workdir: .
            env:
              SLACK_USER_TOKEN: ${{SLACK_USER_TOKEN}}
            mock:
              exec: /bin/echo
              args: ["mock-slack"]
              workdir: .
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${{GITHUB_TOKEN}}
            mock:
              exec: /bin/echo
              args: ["mock-github"]
              workdir: .
          drive:
            mode: real
            kind: node
            exec: /bin/echo
            args: []
            workdir: .
            env:
              DRIVE_TOKEN_PATH: ${{DRIVE_TOKEN_PATH}}
              GOOGLE_CREDENTIALS_PATH: ${{GOOGLE_CREDENTIALS_PATH}}
            auth_files:
              - path: ${{DRIVE_TOKEN_PATH}}
            mock:
              exec: /bin/echo
              args: ["mock-drive"]
              workdir: .
        """,
    )
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv("SLACK_USER_TOKEN", "token-slack")
    monkeypatch.setenv("GITHUB_TOKEN", "token-github")
    monkeypatch.setenv("DRIVE_TOKEN_PATH", str(missing_token))
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "creds.json"))
    monkeypatch.setattr("app.__main__.show_startup_status", lambda summary: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False, config_path=config_path, start_services=False)

    out = capsys.readouterr().out
    assert "認証ファイル不足" in out
    assert "drive=mock" in out
    assert "slack=real" in out
    assert "github=real" in out
    assert out.count("未実施（欠損あり）") == 1
