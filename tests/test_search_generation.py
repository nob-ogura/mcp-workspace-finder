import json
import os
import pytest

from app.llm_search import (
    generate_search_parameters,
    _apply_github_search_scope,
    _get_github_search_scope,
)
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
            {"service": "github", "query": "design review", "max_results": 3},
            {"service": "gdrive", "query": "design review", "max_results": 3},
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
    # Bad payload: invalid max_results and empty query for slack, missing services
    bad_payload = {
        "searches": [
            {"service": "slack", "query": "", "max_results": 5},
            {"service": "github", "query": "test", "max_results": 3},
            {"service": "gdrive", "query": "test", "max_results": 3},
        ],
        "alternatives": [],
    }
    client = DummyClient([make_response(bad_payload), make_response(bad_payload)])

    with pytest.raises(ValueError):
        generate_search_parameters("foo", client)

    assert len(client.calls) == 2


def test_missing_service_triggers_retry_and_error():
    # Bad payload: missing github and gdrive services
    bad_payload = {
        "searches": [{"service": "slack", "query": "test", "max_results": 3}],
        "alternatives": ["alt1", "alt2"],
    }
    client = DummyClient([make_response(bad_payload), make_response(bad_payload)])

    with pytest.raises(ValueError, match="searches missing required services"):
        generate_search_parameters("foo", client)

    assert len(client.calls) == 2


def test_alternatives_must_be_two_or_more_non_empty():
    bad_payload = {
        "searches": [
            {"service": "slack", "query": "design", "max_results": 2},
            {"service": "github", "query": "design", "max_results": 2},
            {"service": "gdrive", "query": "design", "max_results": 2},
        ],
        "alternatives": ["only-one", ""],
    }
    good_payload = {
        "searches": [
            {"service": "slack", "query": "design", "max_results": 2},
            {"service": "github", "query": "design", "max_results": 2},
            {"service": "gdrive", "query": "design", "max_results": 2},
        ],
        "alternatives": ["design doc", "design review"],
    }
    client = DummyClient([make_response(bad_payload), make_response(good_payload)])

    result = generate_search_parameters("設計に関する質問", client)

    assert len(client.calls) == 2  # retry once after rejecting bad alternatives
    assert result.alternatives == good_payload["alternatives"]


def test_alternatives_preserve_design_intent():
    payload = {
        "searches": [
            {"service": "slack", "query": "private docs", "max_results": 1},
            {"service": "github", "query": "repo:org/private", "max_results": 1},
            {"service": "gdrive", "query": "private docs", "max_results": 1},
        ],
        "alternatives": ["private repo docs", "internal documentation"],
    }
    client = DummyClient([make_response(payload)])

    result = generate_search_parameters("非公開リポジトリの設計資料", client)

    assert any("設計" in alt or "デザイン" in alt for alt in result.alternatives)


class TestGitHubSearchScope:
    """Tests for GitHub search scope functionality."""

    def test_get_github_search_scope_from_search_scope_var(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SEARCH_SCOPE", "my-org/my-repo")
        assert _get_github_search_scope() == "my-org/my-repo"

    def test_get_github_search_scope_fallback_to_smoke_repo(self, monkeypatch):
        monkeypatch.delenv("GITHUB_SEARCH_SCOPE", raising=False)
        monkeypatch.setenv("GITHUB_SMOKE_REPO", "nob-ogura/mcp-workspace-finder")
        assert _get_github_search_scope() == "nob-ogura/mcp-workspace-finder"

    def test_get_github_search_scope_prefers_search_scope(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SEARCH_SCOPE", "preferred/repo")
        monkeypatch.setenv("GITHUB_SMOKE_REPO", "fallback/repo")
        assert _get_github_search_scope() == "preferred/repo"

    def test_get_github_search_scope_returns_none_if_not_set(self, monkeypatch):
        monkeypatch.delenv("GITHUB_SEARCH_SCOPE", raising=False)
        monkeypatch.delenv("GITHUB_SMOKE_REPO", raising=False)
        assert _get_github_search_scope() is None

    def test_apply_github_search_scope_adds_repo_filter(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SMOKE_REPO", "nob-ogura/mcp-workspace-finder")
        searches = [
            {"service": "github", "query": "LLM issue", "max_results": 3},
            {"service": "slack", "query": "some query", "max_results": 2},
        ]

        result = _apply_github_search_scope(searches)

        # GitHub search should have repo: filter added
        github_search = next(s for s in result if s["service"] == "github")
        assert "repo:nob-ogura/mcp-workspace-finder" in github_search["query"]
        # Slack search should be unchanged
        slack_search = next(s for s in result if s["service"] == "slack")
        assert slack_search["query"] == "some query"

    def test_apply_github_search_scope_adds_org_filter(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SEARCH_SCOPE", "my-organization")
        searches = [{"service": "github", "query": "design docs", "max_results": 3}]

        result = _apply_github_search_scope(searches)

        assert "org:my-organization" in result[0]["query"]

    def test_apply_github_search_scope_skips_existing_repo_filter(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SMOKE_REPO", "nob-ogura/other-repo")
        searches = [{"service": "github", "query": "repo:existing/repo design", "max_results": 3}]

        result = _apply_github_search_scope(searches)

        # Should not add another repo: filter
        assert result[0]["query"] == "repo:existing/repo design"
        assert "nob-ogura" not in result[0]["query"]

    def test_apply_github_search_scope_skips_existing_org_filter(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SEARCH_SCOPE", "my-org")
        searches = [{"service": "github", "query": "org:other-org docs", "max_results": 3}]

        result = _apply_github_search_scope(searches)

        # Should not add another org: filter
        assert result[0]["query"] == "org:other-org docs"

    def test_apply_github_search_scope_no_env_returns_unchanged(self, monkeypatch):
        monkeypatch.delenv("GITHUB_SEARCH_SCOPE", raising=False)
        monkeypatch.delenv("GITHUB_SMOKE_REPO", raising=False)
        searches = [{"service": "github", "query": "design", "max_results": 3}]

        result = _apply_github_search_scope(searches)

        assert result == searches

    def test_generate_search_parameters_applies_github_scope(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SMOKE_REPO", "nob-ogura/mcp-workspace-finder")
        payload = {
            "searches": [
                {"service": "slack", "query": "LLM discussion", "max_results": 3},
                {"service": "github", "query": "LLM issue", "max_results": 3},
                {"service": "gdrive", "query": "LLM documentation", "max_results": 3},
            ],
            "alternatives": ["LLM documentation", "AI integration"],
        }
        client = DummyClient([make_response(payload)])

        result = generate_search_parameters("LLMに関する issue", client)

        github_search = next(s for s in result.searches if s["service"] == "github")
        assert "repo:nob-ogura/mcp-workspace-finder" in github_search["query"]
