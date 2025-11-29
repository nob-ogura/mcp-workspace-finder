from typer.testing import CliRunner
from rich.console import Console

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
    # Mock LLM client to None so query is echoed back (no LLM transformation)
    monkeypatch.setattr(main_module, "create_llm_client", lambda: None)

    result = runner.invoke(main_module.app, ["--query", "GitHub の最新PRを教えて"])

    output = result.output
    assert result.exit_code == 0
    _assert_progress(output)
    assert "GitHub の最新PRを教えて" in output
    assert output.count("Result") == 1
    assert "mcp>" not in output


def test_oneshot_with_stdin_shows_progress_and_exits(monkeypatch):
    _force_stdin_pipe(monkeypatch)
    # Mock LLM client to None so query is echoed back (no LLM transformation)
    monkeypatch.setattr(main_module, "create_llm_client", lambda: None)

    result = runner.invoke(main_module.app, [], input="Google Driveの設計資料\n")

    output = result.output
    assert result.exit_code == 0
    _assert_progress(output)
    assert "Google Driveの設計資料" in output
    assert output.count("Result") == 1
    assert "mcp>" not in output


def test_oneshot_uses_progress_display(monkeypatch):
    _force_tty(monkeypatch)
    # Mock LLM client to None to avoid real API calls
    monkeypatch.setattr(main_module, "create_llm_client", lambda: None)

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


def test_oneshot_renders_summary_with_links(monkeypatch):
    # capture console output
    console = Console(record=True, force_terminal=True, color_system=None, width=120)
    monkeypatch.setattr(main_module, "console", console)

    from app.evidence_links import EvidenceLink
    from app.summary_pipeline import SummaryPipelineResult

    def summarizer(question, documents, client=None):
        return SummaryPipelineResult(
            summary_markdown="## Slack\n- Update [1]",
            links=[EvidenceLink(number=1, title="Slack thread", service="slack", uri="https://slack.test/1")],
            warnings=[],
            used_fallback=False,
        )

    main_module.run_oneshot(
        "LLM summary please",
        force_mock=True,
        llm_client=None,
        search_runner=lambda searches: ["dummy"],
        summarizer=summarizer,
    )

    out = console.export_text()
    assert "Slack" in out
    assert "根拠リンク" in out
    assert "[1] Slack thread (slack)" in out
    assert "Result:" not in out
