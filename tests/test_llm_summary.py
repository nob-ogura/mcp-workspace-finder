import json
import logging

import pytest

from app.llm_summary import summarize_documents
from app.search_pipeline import FetchResult


class DummyClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

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


def _sample_docs():
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


def test_markdown_summary_includes_sections_and_numbers():
    docs = _sample_docs()
    payload = {
        "markdown": "## Slack\n- Update [1]\n## GitHub\n- Bug [2]\n## Drive\n- Doc [3]",
        "evidence_count": 3,
    }
    client = DummyClient([make_response(payload)])

    result = summarize_documents("最新の設計議論をまとめて", docs, client)

    assert result.markdown.startswith("## Slack")
    assert result.evidence_count == len(docs)
    for idx in range(1, len(docs) + 1):
        assert f"[{idx}]" in result.markdown

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert 0.29 <= call["temperature"] <= 0.31
    assert call["timeout"] == 15
    assert call["max_retries"] == 1
    assert "documents" in call["messages"][-1]["content"]


def test_schema_mismatch_retries_and_warns(caplog):
    docs = _sample_docs()
    bad_payload = {"markdown": "missing evidence_count"}
    client = DummyClient([make_response(bad_payload), make_response(bad_payload)])

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError):
            summarize_documents("Q", docs, client)

    assert len(client.calls) == 2
    assert any("retry" in record.message.lower() for record in caplog.records)
