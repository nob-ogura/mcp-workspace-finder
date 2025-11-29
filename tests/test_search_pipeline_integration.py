from __future__ import annotations

import asyncio
import time
from collections import Counter

import pytest

from app.search_mapping import MAX_RESULTS_PER_SERVICE
from app.search_pipeline import run_search_and_fetch_pipeline


SERVICES = ("slack", "github", "gdrive")


class RateLimitError(Exception):
    """Minimal 429-style error to exercise retry policy skip logic."""

    def __init__(self, message: str = "rate limited"):
        super().__init__(message)
        self.status_code = 429


class FakeMcpResponder:
    """Async stub that mimics a MCP server's search/fetch behaviour."""

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
            raise RateLimitError()

        await asyncio.sleep(self.delay)

        # Return more than the cap to ensure trimming happens inside the pipeline.
        results = []
        for i in range(MAX_RESULTS_PER_SERVICE + 2):
            result = {
                "service": self.service,
                "title": f"{self.service}-title-{i}",
                "snippet": "preview",
                "uri": f"{self.service}://{i}",
                "kind": "issue" if self.service == "github" else "file",
            }
            # Add required fields for proper fetch
            if self.service == "slack":
                result["channel_id"] = f"C{i}ABC"
                result["thread_ts"] = f"1234567890.{i:06d}"
            elif self.service == "github":
                result["owner"] = "org"
                result["repo"] = "repo"
                result["issue_number"] = i
            results.append(result)
        return results

    async def fetch(self, result):  # pragma: no cover - exercised via tests
        self.fetch_calls += 1
        await asyncio.sleep(self.delay)

        if self.fail_fetch:
            raise RuntimeError("forced fetch failure")

        return f"{self.service}-content-{result.uri}"


def _build_payloads():
    return [{"service": name, "query": f"{name} query", "max_results": 5} for name in SERVICES]


def _build_runners(responders: dict[str, FakeMcpResponder]):
    search_runners = {name: responder.search for name, responder in responders.items()}
    fetch_runners = {
        # New tool names to match real MCP server implementations
        "slack": responders["slack"].fetch,
        "slack.conversations_replies": responders["slack"].fetch,
        "github": responders["github"].fetch,
        "github.get_issue": responders["github"].fetch,
        # gdrive uses skip, no fetch runner needed (but include for test)
        "gdrive": responders["gdrive"].fetch,
    }
    return search_runners, fetch_runners


@pytest.fixture
def anyio_backend():  # pytest-anyio uses this to pick asyncio backend
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_mock_servers_return_documents_within_cap():
    responders = {name: FakeMcpResponder(name) for name in SERVICES}
    search_runners, fetch_runners = _build_runners(responders)

    output = await run_search_and_fetch_pipeline(
        _build_payloads(),
        search_runners=search_runners,
        fetch_runners=fetch_runners,
    )

    counts = Counter(doc.service for doc in output.documents)
    assert counts == {name: MAX_RESULTS_PER_SERVICE for name in SERVICES}
    # slack and github get fetched content, gdrive uses snippet (via skip)
    for doc in output.documents:
        if doc.service in ("slack", "github"):
            assert doc.content.startswith(doc.service)
        else:
            assert doc.content == "preview"  # gdrive uses snippet via skip
    assert output.warnings == []


@pytest.mark.anyio("asyncio")
async def test_mock_servers_fail_soft_when_one_fetch_errors(caplog):
    caplog.set_level("WARNING")

    # Make github fail instead of slack, since slack might use skip behavior
    responders = {
        "slack": FakeMcpResponder("slack"),
        "github": FakeMcpResponder("github", fail_fetch=True),
        "gdrive": FakeMcpResponder("gdrive"),
    }
    search_runners, fetch_runners = _build_runners(responders)

    output = await run_search_and_fetch_pipeline(
        _build_payloads(),
        search_runners=search_runners,
        fetch_runners=fetch_runners,
    )

    services = {doc.service for doc in output.documents}
    # slack and gdrive succeed, github fails
    assert services == {"slack", "gdrive"}
    assert responders["github"].fetch_calls == MAX_RESULTS_PER_SERVICE
    assert any("github fetch failed" in warning.lower() for warning in output.warnings)
    assert "github fetch failed" in caplog.text.lower()


@pytest.mark.anyio("asyncio")
async def test_mock_servers_finish_under_timeout_with_parallelism():
    responders = {name: FakeMcpResponder(name, delay=0.3) for name in SERVICES}  # Reduced delay
    search_runners, fetch_runners = _build_runners(responders)

    start = time.perf_counter()
    output = await run_search_and_fetch_pipeline(
        _build_payloads(),
        search_runners=search_runners,
        fetch_runners=fetch_runners,
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
    assert len(output.documents) == len(SERVICES) * MAX_RESULTS_PER_SERVICE
