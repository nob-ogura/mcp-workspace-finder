from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

MAX_RESULTS_PER_SERVICE = 3


@dataclass
class SearchResult:
    service: str
    kind: str
    title: str
    snippet: str
    uri: str
    fetch_tool: str
    fetch_params: dict[str, Any]


def _build_fetch_info(item: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    service = item.get("service")
    if service == "slack":
        thread_ts = item.get("thread_ts")
        if thread_ts:
            return "slack.get_thread", {"permalink": item.get("uri"), "thread_ts": thread_ts}, "message"
        return "slack.get_message", {"permalink": item.get("uri")}, "message"

    if service == "github":
        kind = item.get("kind", "issue")
        if kind in {"issue", "pr", "pull_request"}:
            return "github.get_issue", {"uri": item.get("uri")}, kind
        return "github.read_resource", {"uri": item.get("uri")}, kind

    if service == "gdrive":
        return "gdrive.read_resource", {"uri": item.get("uri")}, item.get("kind", "file")

    raise ValueError(f"unsupported service: {service}")


def map_search_results(raw_results: Iterable[Mapping[str, Any]]) -> list[SearchResult]:
    counts: dict[str, int] = {}
    mapped: list[SearchResult] = []

    for item in raw_results:
        service = item.get("service")
        if not service:
            raise ValueError("search result missing service")

        current = counts.get(service, 0)
        if current >= MAX_RESULTS_PER_SERVICE:
            continue

        fetch_tool, fetch_params, kind = _build_fetch_info(item)

        result = SearchResult(
            service=service,
            kind=kind,
            title=item.get("title", ""),
            snippet=item.get("snippet", ""),
            uri=item.get("uri", ""),
            fetch_tool=fetch_tool,
            fetch_params=fetch_params,
        )

        mapped.append(result)
        counts[service] = current + 1

    return mapped
