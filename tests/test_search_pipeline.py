from __future__ import annotations

import asyncio
import time

import pytest

from app.search_pipeline import run_search_and_fetch_pipeline


SERVICES = ("slack", "github", "gdrive")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _build_search_payloads():
    return [{"service": service, "query": f"{service} query", "max_results": 3} for service in SERVICES]


@pytest.mark.anyio("asyncio")
async def test_search_and_fetch_execute_in_parallel():
    delay = 0.18  # slightly under 0.2s to keep total under 0.4s with overhead

    async def delayed_search(params):
        await asyncio.sleep(delay)
        return [
            {
                "service": params["service"],
                "title": f"{params['service']} title",
                "snippet": "preview",
                "uri": f"{params['service']}://1",
            }
        ]

    async def delayed_fetch(result):
        await asyncio.sleep(delay)
        return f"content for {result.service}"

    start = time.perf_counter()
    output = await run_search_and_fetch_pipeline(
        _build_search_payloads(),
        search_runners={service: delayed_search for service in SERVICES},
        fetch_runners={service: delayed_fetch for service in SERVICES},
    )
    elapsed = time.perf_counter() - start

    assert elapsed < 0.4
    assert {doc.service for doc in output.documents} == set(SERVICES)
    assert all(doc.content.startswith("content for") for doc in output.documents)


@pytest.mark.anyio("asyncio")
async def test_fetch_failure_does_not_block_other_services():
    async def search_once(params):
        return [
            {
                "service": params["service"],
                "title": f"{params['service']} title",
                "snippet": "preview",
                "uri": f"{params['service']}://1",
            }
        ]

    async def ok_fetch(result):
        return f"ok-{result.service}"

    async def failing_fetch(result):
        raise RuntimeError("boom")

    output = await run_search_and_fetch_pipeline(
        _build_search_payloads(),
        search_runners={service: search_once for service in SERVICES},
        fetch_runners={
            "slack": ok_fetch,
            "gdrive": ok_fetch,
            "github": failing_fetch,
        },
    )

    services = {doc.service for doc in output.documents}
    assert services == {"slack", "gdrive"}
    assert any("github" in warning.lower() for warning in output.warnings)


@pytest.mark.anyio("asyncio")
async def test_fetch_respects_max_results_limit():
    calls: list[str] = []

    async def search_many(params):
        # deliberately return more than the cap to ensure the pipeline trims it
        return [
            {"service": params["service"], "title": f"t{i}", "snippet": "", "uri": f"u{i}"}
            for i in range(5)
        ]

    async def record_fetch(result):
        calls.append(result.uri)
        return "ok"

    output = await run_search_and_fetch_pipeline(
        [{"service": "slack", "query": "design", "max_results": 5}],
        search_runners={"slack": search_many},
        fetch_runners={"slack": record_fetch},
    )

    assert len(calls) == 3  # capped at MAX_RESULTS_PER_SERVICE
    assert len(output.documents) == 3
