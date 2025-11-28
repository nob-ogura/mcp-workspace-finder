import json
import logging
from io import StringIO

from app.llm_search import generate_search_parameters
from app.logging_utils import install_log_masking


class DummyClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, **kwargs):
        if not self.responses:
            raise RuntimeError("no more responses")
        return self.responses.pop(0)


def make_response(payload: dict):
    return {
        "function_call": {
            "name": "build_search_queries",
            "arguments": json.dumps(payload),
        }
    }


def test_llm_logging_masks_openai_key(monkeypatch):
    secret = "sk-test-ABC1234567890"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    payload = {
        "searches": [
            {"service": "slack", "query": "design", "max_results": 1},
            {"service": "github", "query": "repo:org/design", "max_results": 2},
            {"service": "gdrive", "query": "design doc", "max_results": 3},
        ],
        "alternatives": ["設計 レビュー", "デザイン ドキュメント"],
    }
    client = DummyClient([make_response(payload)])

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    original_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    try:
        install_log_masking()
        generate_search_parameters(f"質問にキー {secret} が含まれている", client)
    finally:
        root.setLevel(original_level)
        root.removeHandler(handler)

    output = stream.getvalue()

    assert "LLM request" in output
    assert "LLM response" in output
    assert secret not in output
    assert "[redacted]" in output
