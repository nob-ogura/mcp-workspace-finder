from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping

from app.config import RunMode, ServerDefinition, ResolvedService, cli_override_warning, resolve_service_modes
from app.retry_policy import run_with_retry
from app.search_mapping import MAX_RESULTS_PER_SERVICE, SearchResult, map_search_results

logger = logging.getLogger(__name__)


SearchRunner = Callable[[Mapping[str, Any]], Awaitable[list[Mapping[str, Any]]]]
FetchRunner = Callable[[SearchResult], Awaitable[Any]]


@dataclass
class FetchResult:
    service: str
    kind: str
    title: str
    snippet: str
    uri: str
    content: Any


@dataclass
class PipelineOutput:
    documents: list[FetchResult]
    warnings: list[str]


@dataclass
class ModeAwareRunners:
    search_runners: dict[str, SearchRunner]
    fetch_runners: dict[str, FetchRunner]
    warnings: list[str]
    resolved_services: dict[str, ResolvedService]


def _cap_max_results(payload: Mapping[str, Any], limit: int) -> dict[str, Any]:
    capped = dict(payload)
    try:
        requested = int(capped.get("max_results", limit))
    except Exception:  # noqa: BLE001
        requested = limit
    capped["max_results"] = min(requested, limit)
    return capped


def _fetch_keys_for_service(source: Mapping[str, FetchRunner], service: str) -> dict[str, FetchRunner]:
    return {
        key: runner
        for key, runner in source.items()
        if key == service or key.startswith(f"{service}.")
    }


def prepare_mode_aware_runners(
    definitions: Mapping[str, ServerDefinition],
    *,
    search_runners_real: Mapping[str, SearchRunner],
    search_runners_mock: Mapping[str, SearchRunner],
    fetch_runners_real: Mapping[str, FetchRunner],
    fetch_runners_mock: Mapping[str, FetchRunner],
    force_mock: bool,
    allow_real: bool | None = None,
    logger: logging.Logger | None = None,
) -> ModeAwareRunners:
    """Apply mode priority and select search/fetch runners per service.

    Priority: ``--mock`` (``force_mock=True``) > ``ALLOW_REAL=1`` > fallback mock.
    CLI/env overrides relative to ``servers.yaml`` are warned once with "CLI override".
    """

    log = logger or logging.getLogger(__name__)
    allow_real_flag = allow_real if allow_real is not None else os.getenv("ALLOW_REAL") == "1"

    resolved = resolve_service_modes(
        definitions,
        force_mock=force_mock,
        allow_real=allow_real_flag,
    )

    warnings: list[str] = []

    override = cli_override_warning(
        definitions,
        force_mock=force_mock,
        allow_real=allow_real_flag,
    )
    if override:
        warnings.append(override)
        log.warning(override)

    for decision in resolved.values():
        if decision.warning:
            warnings.append(decision.warning)
            log.warning(decision.warning)

    selected_search: dict[str, SearchRunner] = {}
    selected_fetch: dict[str, FetchRunner] = {}

    for name, decision in resolved.items():
        mode = decision.selected_mode
        search_source = search_runners_real if mode is RunMode.REAL else search_runners_mock
        fetch_source = fetch_runners_real if mode is RunMode.REAL else fetch_runners_mock

        if name in search_source:
            selected_search[name] = search_source[name]
        else:
            warning = f"{name} search runner missing for mode {mode.value}"
            warnings.append(warning)
            log.warning(warning)

        fetch_subset = _fetch_keys_for_service(fetch_source, name)
        if fetch_subset:
            selected_fetch.update(fetch_subset)
        else:
            warning = f"{name} fetch runner missing for mode {mode.value}"
            warnings.append(warning)
            log.warning(warning)

    return ModeAwareRunners(
        search_runners=selected_search,
        fetch_runners=selected_fetch,
        warnings=warnings,
        resolved_services=dict(resolved),
    )


async def run_search_and_fetch_pipeline(
    searches: Iterable[Mapping[str, Any]],
    *,
    search_runners: Mapping[str, SearchRunner],
    fetch_runners: Mapping[str, FetchRunner],
    max_results_per_service: int = MAX_RESULTS_PER_SERVICE,
    initial_warnings: list[str] | None = None,
) -> PipelineOutput:
    """Run search + fetch in two asynchronous waves with per-service caps.

    - Searches for each service run in parallel using ``asyncio.gather``.
    - After all searches complete, fetches for the mapped results run in parallel.
    - Both stages enforce the per-service ``max_results`` cap (default: 3).
    - Failures during fetch are logged/warned but do not stop other services.
    """

    warnings: list[str] = list(initial_warnings or [])

    async def _run_single_search(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        service = payload.get("service")
        if not service:
            raise ValueError("search payload missing service")

        runner = search_runners.get(service)
        if not runner:
            raise ValueError(f"no search runner registered for {service}")

        capped = _cap_max_results(payload, max_results_per_service)

        outcome = await run_with_retry(
            lambda: runner(capped),
            service=service,
            stage="search",
            warnings=warnings,
            logger=logger,
        )

        if not outcome.success:
            return []

        results = outcome.result or []
        if not isinstance(results, list):
            warning = f"{service} search returned non-list result; skipping"
            warnings.append(warning)
            logger.warning(warning)
            return []

        return results

    search_tasks = [asyncio.create_task(_run_single_search(payload)) for payload in searches]
    raw_batches = await asyncio.gather(*search_tasks) if search_tasks else []
    raw_results = [item for batch in raw_batches for item in batch]

    mapped_results = map_search_results(raw_results)

    async def _run_fetch(result: SearchResult) -> FetchResult | None:
        # Skip fetch if explicitly marked as skip (e.g., "slack.skip")
        if result.fetch_tool.endswith(".skip"):
            # Use snippet as content when fetch is skipped
            return FetchResult(
                service=result.service,
                kind=result.kind,
                title=result.title,
                snippet=result.snippet,
                uri=result.uri,
                content=result.snippet,  # Use snippet as content
            )

        runner = fetch_runners.get(result.fetch_tool) or fetch_runners.get(result.service)
        if not runner:
            warning = f"{result.service} fetch runner missing for {result.fetch_tool}"
            warnings.append(warning)
            logger.warning(warning)
            return None

        outcome = await run_with_retry(
            lambda: runner(result),
            service=result.service,
            stage="fetch",
            warnings=warnings,
            logger=logger,
        )

        if not outcome.success:
            return None

        content = outcome.result

        return FetchResult(
            service=result.service,
            kind=result.kind,
            title=result.title,
            snippet=result.snippet,
            uri=result.uri,
            content=content,
        )

    fetch_tasks = [asyncio.create_task(_run_fetch(result)) for result in mapped_results]
    fetched = await asyncio.gather(*fetch_tasks) if fetch_tasks else []

    documents = [item for item in fetched if item is not None]
    return PipelineOutput(documents=documents, warnings=warnings)
