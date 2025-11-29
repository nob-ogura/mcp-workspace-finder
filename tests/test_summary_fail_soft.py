from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.search_pipeline import FetchResult
from app.summary_pipeline import run_summary_pipeline


class DummyClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise RuntimeError("no more responses")
        return self.responses.pop(0)


def make_response(payload: dict):
    return {
        "function_call": {
            "name": "write_markdown_summary",
            "arguments": json.dumps(payload),
        }
    }


def _docs():
    return [
        FetchResult(
            service="slack",
            kind="message",
            title="Slack thread",
            snippet="Discussed rollout plan",
            uri="https://slack.test/1",
            content="Full Slack message body",
        ),
        FetchResult(
            service="github",
            kind="issue",
            title="Fix design bug",
            snippet="Issue about layout regression",
            uri="https://github.test/2",
            content="GitHub issue body with details",
        ),
        FetchResult(
            service="gdrive",
            kind="file",
            title="Design doc",
            snippet="Updated architecture",
            uri="https://drive.test/3",
            content="Document content goes here",
        ),
    ]


def test_summary_timeout_returns_fallback_and_warning(caplog):
    class TimeoutClient:
        def create(self, **kwargs):
            raise TimeoutError("LLM timeout")

    docs = _docs()
    client = TimeoutClient()

    with caplog.at_level(logging.WARNING):
        result = run_summary_pipeline("最新の議論をまとめて", docs, client)

    assert "Slack thread" in result.summary_markdown
    assert "https://slack.test/1" in result.summary_markdown
    assert result.links  # evidence links still produced
    assert any("timeout" in warning.lower() for warning in result.warnings)
    assert "timeout" in caplog.text.lower()


def test_debug_mode_writes_llm_jsonl_only_when_enabled(tmp_path: Path):
    docs = _docs()
    payload = {
        "markdown": "## Slack\n- Update [1]\n## GitHub\n- Fix [2]\n## Drive\n- Doc [3]",
        "evidence_count": len(docs),
    }

    # Debug enabled: file is written
    client = DummyClient([make_response(payload)])
    log_dir = tmp_path / "logs"
    result = run_summary_pipeline("Q", docs, client, debug_enabled=True, log_dir=log_dir)

    log_file = log_dir / "llm-summary.jsonl"
    assert log_file.exists()
    records = [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]
    assert {record.get("direction") for record in records} >= {"request", "response"}
    assert all(record.get("stage") == "summary" for record in records)
    assert result.summary_markdown.startswith("## Slack")

    # Debug disabled: no file is created
    client2 = DummyClient([make_response(payload)])
    quiet_dir = tmp_path / "logs_disabled"
    _ = run_summary_pipeline("Q", docs, client2, debug_enabled=False, log_dir=quiet_dir)
    assert not (quiet_dir / "llm-summary.jsonl").exists()

