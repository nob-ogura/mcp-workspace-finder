from pathlib import Path

import pytest

from app.schema_validation import validate_search_payload


def test_slack_payload_valid():
    payload = {"service": "slack", "query": "design OR 設計", "max_results": 3}

    # Should not raise
    validate_search_payload(payload)


def test_github_max_results_exceeds_limit():
    payload = {"service": "github", "query": "repo:org/repo bug", "max_results": 4}

    with pytest.raises(ValueError) as excinfo:
        validate_search_payload(payload)

    assert "max_results" in str(excinfo.value)


def test_drive_missing_query_fails():
    payload = {"service": "gdrive", "max_results": 2}

    with pytest.raises(ValueError) as excinfo:
        validate_search_payload(payload)

    assert "query" in str(excinfo.value)
