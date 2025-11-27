import builtins
from collections.abc import Iterator
from typing import Callable

import pytest

from app.__main__ import (
    REQUIRED_KEYS,
    RunMode,
    collect_missing_keys,
    decide_mode,
    repl_loop,
    show_startup_status,
)


def test_collect_missing_keys_when_env_empty(monkeypatch):
    monkeypatch.delenv(REQUIRED_KEYS[0], raising=False)

    assert collect_missing_keys() == [REQUIRED_KEYS[0]]


def test_collect_missing_keys_when_env_present(monkeypatch):
    monkeypatch.setenv(REQUIRED_KEYS[0], "token")

    assert collect_missing_keys() == []


def test_decide_mode_force_mock_overrides_env(monkeypatch):
    # Even when real mode is allowed and keys exist, --mock should win.
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv(REQUIRED_KEYS[0], "token")

    mode, warning = decide_mode(force_mock=True)

    assert mode is RunMode.MOCK
    assert warning is None


def test_decide_mode_real_only_when_allowed_and_keys_present(monkeypatch):
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv(REQUIRED_KEYS[0], "token")

    mode, warning = decide_mode(force_mock=False)

    assert mode is RunMode.REAL
    assert warning is None


def test_decide_mode_warns_and_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.delenv(REQUIRED_KEYS[0], raising=False)

    mode, warning = decide_mode(force_mock=False)

    assert mode is RunMode.MOCK
    assert warning is not None
    assert REQUIRED_KEYS[0] in warning


def test_show_startup_status_uses_spinner(mocker):
    sleep = mocker.patch("time.sleep")
    status = mocker.patch("app.__main__.console.status")

    show_startup_status(RunMode.MOCK)

    status.assert_called_once()
    # Prevent flakiness: confirm spinner message references mode
    args, _ = status.call_args
    assert RunMode.MOCK.value in args[0]
    sleep.assert_called_once_with(0.1)


def _iter_inputs(values: list[str]) -> Callable[[str], str]:
    it: Iterator[str] = iter(values)
    return lambda _prompt="": next(it)


def test_repl_loop_echo_and_exit(monkeypatch, capsys):
    # Avoid touching environment or sleeping during the loop
    monkeypatch.setattr(
        "app.__main__.decide_mode", lambda force_mock: (RunMode.MOCK, None)
    )
    monkeypatch.setattr("app.__main__.show_startup_status", lambda mode: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["hello", "exit"]))

    repl_loop(force_mock=False)

    out = capsys.readouterr().out
    assert "echo: hello" in out
    assert "Goodbye." in out


def test_repl_loop_warns_when_decide_mode_returns_warning(monkeypatch, capsys):
    monkeypatch.setattr(
        "app.__main__.decide_mode",
        lambda force_mock: (RunMode.MOCK, "鍵不足によりモックへフォールバックします"),
    )
    monkeypatch.setattr("app.__main__.show_startup_status", lambda mode: None)
    monkeypatch.setattr(builtins, "input", _iter_inputs(["exit"]))

    repl_loop(force_mock=False)

    out = capsys.readouterr().out
    assert "Warning" in out
    assert "モック" in out
