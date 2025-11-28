import json
import pytest

from app.llm_search import generate_search_parameters
from app.schema_validation import validate_search_payload


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
    return {"function_call": {"name": "build_search_queries", "arguments": json.dumps(payload)}}


def test_generate_three_services_valid():
    payload = {
        "searches": [
            {"service": "slack", "query": "design review", "max_results": 3},
            {"service": "github", "query": "repo:org/repo design", "max_results": 2},
            {"service": "gdrive", "query": "design docs", "max_results": 1},
        ],
        "alternatives": ["design review summary", "design doc"]
    }
    client = DummyClient([make_response(payload)])

    result = generate_search_parameters("設計レビューの議事録を探して", client)

    assert len(result.searches) == 3
    for search in result.searches:
        validate_search_payload(search)
    assert result.alternatives == payload["alternatives"]


def test_openai_call_parameters_fixed():
    payload = {
        "searches": [
            {"service": "slack", "query": "design review", "max_results": 3},
        ],
        "alternatives": ["alt1", "alt2"],
    }
    client = DummyClient([make_response(payload)])

    generate_search_parameters("設計レビュー", client)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert 0.2 <= call["temperature"] <= 0.3
    assert call["timeout"] == 15
    assert call["max_retries"] == 1
    assert call["tools"][0]["function"]["name"] == "build_search_queries"
    assert "Slack" in call["messages"][0]["content"]


def test_schema_violation_triggers_retry_and_error():
    bad_payload = {"searches": [{"service": "slack", "query": "", "max_results": 5}], "alternatives": []}
    client = DummyClient([make_response(bad_payload), make_response(bad_payload)])

    with pytest.raises(ValueError):
        generate_search_parameters("foo", client)

    assert len(client.calls) == 2
