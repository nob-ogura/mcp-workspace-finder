from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, defaultdict
from typing import Iterable

import pytest

from app.search_mapping import MAX_RESULTS_PER_SERVICE
from app.summary_pipeline import run_search_fetch_and_summarize_pipeline


SERVICES = ("slack", "github", "gdrive")


class FakeResponder:
    """Async stub that mimics search/fetch behaviour of Phase6 mock servers."""

    def __init__(self, service: str, *, delay: float = 0.0, fail_fetch: bool = False, rate_limit: bool = False):
        self.service = service
        self.delay = delay
        self.fail_fetch = fail_fetch
        self.rate_limit = rate_limit
        self.search_calls = 0
        self.fetch_calls = 0

    async def search(self, payload):  # pragma: no cover - exercised via tests
        self.search_calls += 1
        if self.rate_limit:
            raise RuntimeError("rate limited")

        await asyncio.sleep(self.delay)

        return [
            {
                "service": self.service,
                "title": f"{self.service}-title-{i}",
                "snippet": "preview",
                "uri": f"{self.service}://{i}",
                "kind": "issue" if self.service == "github" else "file",
            }
            for i in range(MAX_RESULTS_PER_SERVICE + 2)
        ]

    async def fetch(self, result):  # pragma: no cover - exercised via tests
        self.fetch_calls += 1
        await asyncio.sleep(self.delay)

        if self.fail_fetch:
            raise RuntimeError("forced fetch failure")

        return f"{self.service}-content-{result.uri}"


class StubSummaryClient:
    """Synchronous stub that returns deterministic Markdown with evidence numbers."""

    def __init__(self, *, delay: float = 0.0, fail: bool = False):
        self.delay = delay
        self.fail = fail
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise TimeoutError("LLM unavailable")

        if self.delay:
            time.sleep(self.delay)

        user_payload = json.loads(kwargs["messages"][-1]["content"])
        documents = user_payload["documents"]

        grouped: dict[str, list[dict]] = defaultdict(list)
        for doc in documents:
            grouped[doc["service"]].append(doc)

        parts: list[str] = []
        for service, items in grouped.items():
            heading = f"## {service.capitalize()}"
            bullets = [f"- [{doc['id']}] {doc['title']}" for doc in items]
            parts.append("\n".join([heading, *bullets]))

        payload = {
            "markdown": "\n\n".join(parts),
            "evidence_count": len(documents),
        }
        return {
            "function_call": {
                "name": "write_markdown_summary",
                "arguments": json.dumps(payload),
            }
        }


def _build_payloads() -> Iterable[dict]:
    return [{"service": name, "query": f"{name} query", "max_results": 5} for name in SERVICES]


def _build_runners(responders: dict[str, FakeResponder]):
    search_runners = {name: responder.search for name, responder in responders.items()}
    fetch_runners = {
        # Slack fetch runner
        "slack": responders["slack"].fetch,
        "slack.conversations_replies": responders["slack"].fetch,
        # GitHub fetch runners (get_issue for issues, __read_resource__ for code)
        "github": responders["github"].fetch,
        "github.get_issue": responders["github"].fetch,
        "github.__read_resource__": responders["github"].fetch,
        # GDrive uses read_resource
        "gdrive": responders["gdrive"].fetch,
        "gdrive.__read_resource__": responders["gdrive"].fetch,
    }
    return search_runners, fetch_runners


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_end_to_end_returns_summary_and_links():
    responders = {name: FakeResponder(name) for name in SERVICES}
    search_runners, fetch_runners = _build_runners(responders)

    result = await run_search_fetch_and_summarize_pipeline(
        "最新の議論をまとめて",
        _build_payloads(),
        search_runners=search_runners,
        fetch_runners=fetch_runners,
        llm_client=StubSummaryClient(),
    )

    counts = Counter(doc.service for doc in result.documents)
    assert counts == {name: MAX_RESULTS_PER_SERVICE for name in SERVICES}

    assert not result.used_fallback
    assert len(result.links) == len(result.documents)
    assert [link.number for link in result.links] == list(range(1, len(result.documents) + 1))
    assert all(service.capitalize() in result.summary_markdown for service in SERVICES)


@pytest.mark.anyio("asyncio")
async def test_summary_failure_returns_fallback_and_docs(caplog):
    caplog.set_level("WARNING")

    responders = {name: FakeResponder(name) for name in SERVICES}
    search_runners, fetch_runners = _build_runners(responders)

    result = await run_search_fetch_and_summarize_pipeline(
        "要約に失敗しても続行",
        _build_payloads(),
        search_runners=search_runners,
        fetch_runners=fetch_runners,
        llm_client=StubSummaryClient(fail=True),
    )

    assert result.used_fallback
    assert "取得本文" in result.summary_markdown
    assert len(result.documents) == len(result.links)
    assert any("summary" in warning.lower() or "timeout" in warning.lower() for warning in result.warnings)
    assert "timeout" in caplog.text.lower()


@pytest.mark.anyio("asyncio")
async def test_full_pipeline_finishes_under_two_seconds():
    responders = {name: FakeResponder(name, delay=0.5) for name in SERVICES}
    search_runners, fetch_runners = _build_runners(responders)

    start = time.perf_counter()
    result = await run_search_fetch_and_summarize_pipeline(
        "速度検証",
        _build_payloads(),
        search_runners=search_runners,
        fetch_runners=fetch_runners,
        llm_client=StubSummaryClient(delay=0.5),
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
    assert len(result.documents) == len(SERVICES) * MAX_RESULTS_PER_SERVICE
