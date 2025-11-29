from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping

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


def _cap_max_results(payload: Mapping[str, Any], limit: int) -> dict[str, Any]:
    capped = dict(payload)
    try:
        requested = int(capped.get("max_results", limit))
    except Exception:  # noqa: BLE001
        requested = limit
    capped["max_results"] = min(requested, limit)
    return capped


async def run_search_and_fetch_pipeline(
    searches: Iterable[Mapping[str, Any]],
    *,
    search_runners: Mapping[str, SearchRunner],
    fetch_runners: Mapping[str, FetchRunner],
    max_results_per_service: int = MAX_RESULTS_PER_SERVICE,
) -> PipelineOutput:
    """Run search + fetch in two asynchronous waves with per-service caps.

    - Searches for each service run in parallel using ``asyncio.gather``.
    - After all searches complete, fetches for the mapped results run in parallel.
    - Both stages enforce the per-service ``max_results`` cap (default: 3).
    - Failures during fetch are logged/warned but do not stop other services.
    """

    warnings: list[str] = []

    async def _run_single_search(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        service = payload.get("service")
        if not service:
            raise ValueError("search payload missing service")

        runner = search_runners.get(service)
        if not runner:
            raise ValueError(f"no search runner registered for {service}")

        capped = _cap_max_results(payload, max_results_per_service)
        try:
            return await runner(capped) or []
        except Exception as exc:  # noqa: BLE001
            warning = f"{service} search failed: {exc}"
            warnings.append(warning)
            logger.warning(warning)
            return []

    search_tasks = [asyncio.create_task(_run_single_search(payload)) for payload in searches]
    raw_batches = await asyncio.gather(*search_tasks) if search_tasks else []
    raw_results = [item for batch in raw_batches for item in batch]

    mapped_results = map_search_results(raw_results)

    async def _run_fetch(result: SearchResult) -> FetchResult | None:
        runner = fetch_runners.get(result.fetch_tool) or fetch_runners.get(result.service)
        if not runner:
            warning = f"{result.service} fetch runner missing for {result.fetch_tool}"
            warnings.append(warning)
            logger.warning(warning)
            return None

        try:
            content = await runner(result)
        except Exception as exc:  # noqa: BLE001
            warning = f"{result.service} fetch failed: {exc}"
            warnings.append(warning)
            logger.warning(warning)
            return None

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
