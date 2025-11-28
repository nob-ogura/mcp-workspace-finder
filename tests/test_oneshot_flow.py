from typer.testing import CliRunner

import app.__main__ as main_module


runner = CliRunner()


def _force_tty(monkeypatch):
    monkeypatch.setattr(main_module, "_stream_isatty", lambda stream: True)


def _force_stdin_pipe(monkeypatch):
    def fake_isatty(stream):
        if stream is main_module.sys.stdin:
            return False
        return True

    monkeypatch.setattr(main_module, "_stream_isatty", fake_isatty)


def _assert_progress(output: str):
    assert "Preparing query..." in output
    assert "Searching" in output
    assert "Summarizing" in output
    assert output.index("Preparing query...") < output.index("Searching")
    assert output.index("Searching") < output.index("Summarizing")


def test_oneshot_with_query_shows_progress_and_exits(monkeypatch):
    _force_tty(monkeypatch)

    result = runner.invoke(main_module.app, ["--query", "GitHub の最新PRを教えて"])

    output = result.output
    assert result.exit_code == 0
    _assert_progress(output)
    assert "GitHub の最新PRを教えて" in output
    assert output.count("Result") == 1
    assert "mcp>" not in output


def test_oneshot_with_stdin_shows_progress_and_exits(monkeypatch):
    _force_stdin_pipe(monkeypatch)

    result = runner.invoke(main_module.app, [], input="Google Driveの設計資料\n")

    output = result.output
    assert result.exit_code == 0
    _assert_progress(output)
    assert "Google Driveの設計資料" in output
    assert output.count("Result") == 1
    assert "mcp>" not in output


def test_oneshot_uses_progress_display(monkeypatch):
    _force_tty(monkeypatch)

    calls = []

    class FakeProgress:
        def __init__(self, console):
            self.console = console

        def run(self, steps, *, delay: float = 0.0):
            calls.append(tuple(steps))

    monkeypatch.setattr(main_module, "ProgressDisplay", lambda console: FakeProgress(console))

    result = runner.invoke(main_module.app, ["--query", "progress check"])

    assert result.exit_code == 0
    assert calls == [main_module.ONESHOT_PROGRESS_STEPS]
