from __future__ import annotations

import json
import time

from rich.console import Console

import app.__main__ as main_module


class DummyClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, **kwargs):
        if not self.responses:
            raise RuntimeError("no more responses")
        return self.responses.pop(0)


def make_response(payload: dict):
    return {"function_call": {"name": "build_search_queries", "arguments": json.dumps(payload)}}


def test_zero_results_outputs_alternatives_only(monkeypatch):
    console = Console(record=True)
    monkeypatch.setattr(main_module, "console", console)

    class SilentProgress:
        def __init__(self, console):
            self.console = console

        def run(self, steps, *, delay: float = 0.0, spinner: str = "dots"):
            # suppress spinner output during tests
            return None

    monkeypatch.setattr(main_module, "ProgressDisplay", lambda console: SilentProgress(console))

    payload = {
        "searches": [
            {"service": "slack", "query": "design", "max_results": 1},
            {"service": "github", "query": "design", "max_results": 1},
            {"service": "gdrive", "query": "design", "max_results": 1},
        ],
        "alternatives": ["設計 ドキュメント", "設計レビュー 議事録"],
    }

    client = DummyClient([make_response(payload)])

    main_module.run_oneshot(
        "設計について検索",
        force_mock=True,
        llm_client=client,
        search_runner=lambda searches: [],
    )

    out = console.export_text()
    assert "設計 ドキュメント" in out
    assert "設計レビュー 議事録" in out
    assert "Result:" not in out
    assert "Summarizing" not in out


def test_zero_results_skips_summary_and_finishes_quickly(monkeypatch):
    console = Console(record=True, force_terminal=True, color_system=None, width=120)
    monkeypatch.setattr(main_module, "console", console)

    class SilentProgress:
        def __init__(self, console):
            self.console = console

        def run(self, steps, *, delay: float = 0.0, spinner: str = "dots"):
            return None

    monkeypatch.setattr(main_module, "ProgressDisplay", lambda console: SilentProgress(console))

    payload = {
        "searches": [
            {"service": "slack", "query": "design", "max_results": 1},
            {"service": "github", "query": "design", "max_results": 1},
            {"service": "gdrive", "query": "design", "max_results": 1},
        ],
        "alternatives": ["設計 ドキュメント", "設計レビュー 議事録"],
    }

    client = DummyClient([make_response(payload)])

    summary_called = False

    def fake_summary(question, documents, llm_client=None):  # pragma: no cover - behaviour assertion
        nonlocal summary_called
        summary_called = True
        return None

    start = time.perf_counter()
    main_module.run_oneshot(
        "設計について検索",
        force_mock=True,
        llm_client=client,
        search_runner=lambda searches: [],
        summarizer=fake_summary,
    )
    elapsed = time.perf_counter() - start

    out = console.export_text()
    assert "設計 ドキュメント" in out
    assert "設計レビュー 議事録" in out
    assert "次の検索候補" in out
    assert summary_called is False
    assert elapsed < 0.5


def test_zero_results_without_alternatives_shows_fallback(monkeypatch):
    console = Console(record=True, force_terminal=True, color_system=None, width=120)
    monkeypatch.setattr(main_module, "console", console)

    main_module._render_alternatives_only([])

    out = console.export_text()
    assert "代替クエリを生成できませんでした" in out
