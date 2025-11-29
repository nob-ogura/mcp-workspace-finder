from __future__ import annotations

import asyncio
import time
import textwrap
from pathlib import Path

import pytest

from app.config import RunMode, load_server_definitions
from app.search_pipeline import prepare_mode_aware_runners, run_search_and_fetch_pipeline


SERVICES = ("slack", "github", "gdrive")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _build_search_payloads():
    return [{"service": service, "query": f"{service} query", "max_results": 3} for service in SERVICES]


def _write_config(tmp_path: Path, yaml_text: str):
    path = tmp_path / "servers.yaml"
    path.write_text(textwrap.dedent(yaml_text))
    return load_server_definitions(path)


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


@pytest.mark.anyio("asyncio")
async def test_rate_limit_search_is_skipped_without_retry(caplog):
    caplog.set_level("WARNING")

    class RateLimitError(Exception):
        def __init__(self):
            super().__init__("rate limited")
            self.status_code = 429

    calls = {"github": 0}

    async def rate_limited_search(params):
        calls["github"] += 1
        raise RateLimitError()

    async def ok_search(params):
        return [
            {
                "service": params["service"],
                "title": f"{params['service']} title",
                "snippet": "preview",
                "uri": f"{params['service']}://1",
            }
        ]

    async def ok_fetch(result):
        return f"content for {result.service}"

    output = await run_search_and_fetch_pipeline(
        [
            {"service": "github", "query": "retry policy"},
            {"service": "slack", "query": "project"},
        ],
        search_runners={"github": rate_limited_search, "slack": ok_search},
        fetch_runners={"slack": ok_fetch},
    )

    assert calls["github"] == 1  # no retry for 429
    assert {doc.service for doc in output.documents} == {"slack"}
    assert any("429" in warning or "rate limit" in warning.lower() for warning in output.warnings)
    assert "429" in caplog.text


@pytest.mark.anyio("asyncio")
async def test_transient_fetch_retried_once_with_backoff(caplog):
    caplog.set_level("WARNING")

    async def search_once(params):
        return [
            {
                "service": params["service"],
                "title": "t1",
                "snippet": "",
                "uri": "uri1",
            }
        ]

    attempts = 0

    async def flaky_fetch(result):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("connection reset")
        return "recovered"

    start = time.perf_counter()
    output = await run_search_and_fetch_pipeline(
        [{"service": "slack", "query": "design"}],
        search_runners={"slack": search_once},
        fetch_runners={"slack": flaky_fetch},
    )
    elapsed = time.perf_counter() - start

    assert attempts == 2  # one retry
    assert output.documents and output.documents[0].content == "recovered"
    assert elapsed >= 0.5  # backoff applied
    assert elapsed < 2.0
    assert any("retry" in warning.lower() for warning in output.warnings)


@pytest.mark.anyio("asyncio")
async def test_cli_override_forces_mock_mode_and_logs(monkeypatch, tmp_path, caplog):
    caplog.set_level("WARNING")

    definitions = _write_config(
        tmp_path,
        """
        services:
          gdrive:
            mode: real
            kind: node
            exec: /bin/echo
            args: []
            workdir: .
            env:
              DRIVE_TOKEN_PATH: ${DRIVE_TOKEN_PATH}
            mock:
              exec: /bin/echo
              args: ["mock-drive"]
              workdir: .
        """,
    )

    monkeypatch.setenv("ALLOW_REAL", "1")

    real_calls: list[str] = []
    mock_calls: list[str] = []
    fetch_calls: list[tuple[str, str]] = []

    async def real_search(params):
        real_calls.append(params["service"])
        return [
            {
                "service": params["service"],
                "title": "t",
                "snippet": "",
                "uri": "uri1",
            }
        ]

    async def mock_search(params):
        mock_calls.append(params["service"])
        return [
            {
                "service": params["service"],
                "title": "t",
                "snippet": "",
                "uri": "uri1",
            }
        ]

    async def real_fetch(result):
        fetch_calls.append(("real", result.service))
        return "real-content"

    async def mock_fetch(result):
        fetch_calls.append(("mock", result.service))
        return "mock-content"

    prepared = prepare_mode_aware_runners(
        definitions,
        force_mock=True,
        search_runners_real={"gdrive": real_search},
        search_runners_mock={"gdrive": mock_search},
        fetch_runners_real={"gdrive.read_resource": real_fetch},
        fetch_runners_mock={"gdrive.read_resource": mock_fetch},
    )

    output = await run_search_and_fetch_pipeline(
        [{"service": "gdrive", "query": "design"}],
        search_runners=prepared.search_runners,
        fetch_runners=prepared.fetch_runners,
        initial_warnings=prepared.warnings,
    )

    assert prepared.resolved_services["gdrive"].selected_mode is RunMode.MOCK
    assert not real_calls
    assert mock_calls == ["gdrive"]
    assert fetch_calls == [("mock", "gdrive")]
    assert any("CLI override" in warning for warning in output.warnings)
    assert any("CLI override" in record.message for record in caplog.records)


@pytest.mark.anyio("asyncio")
async def test_allow_real_prefers_real_without_override(monkeypatch, tmp_path, caplog):
    caplog.set_level("WARNING")

    definitions = _write_config(
        tmp_path,
        """
        services:
          github:
            mode: real
            kind: python
            exec: /bin/echo
            args: []
            workdir: .
            env:
              GITHUB_TOKEN: ${GITHUB_TOKEN}
            mock:
              exec: /bin/echo
              args: ["mock-github"]
              workdir: .
        """,
    )

    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "token-present")

    real_calls: list[str] = []
    mock_calls: list[str] = []

    async def real_search(params):
        real_calls.append(params["service"])
        return [
            {
                "service": params["service"],
                "title": "t",
                "snippet": "",
                "uri": "uri1",
            }
        ]

    async def mock_search(params):
        mock_calls.append(params["service"])
        return [
            {
                "service": params["service"],
                "title": "t",
                "snippet": "",
                "uri": "uri1",
            }
        ]

    async def real_fetch(result):
        return "real-content"

    async def mock_fetch(result):
        return "mock-content"

    prepared = prepare_mode_aware_runners(
        definitions,
        force_mock=False,
        search_runners_real={"github": real_search},
        search_runners_mock={"github": mock_search},
        fetch_runners_real={"github.get_issue": real_fetch, "github": real_fetch},
        fetch_runners_mock={"github.get_issue": mock_fetch, "github": mock_fetch},
    )

    output = await run_search_and_fetch_pipeline(
        [{"service": "github", "query": "design"}],
        search_runners=prepared.search_runners,
        fetch_runners=prepared.fetch_runners,
        initial_warnings=prepared.warnings,
    )

    assert prepared.resolved_services["github"].selected_mode is RunMode.REAL
    assert real_calls == ["github"]
    assert not mock_calls
    assert output.documents and output.documents[0].content == "real-content"
    assert not any("CLI override" in warning for warning in output.warnings)
    assert all("CLI override" not in record.message for record in caplog.records)
