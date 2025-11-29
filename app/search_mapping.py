from __future__ import annotations

import re
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


def _parse_slack_permalink(permalink: str) -> tuple[str, str]:
    """Parse channel_id and thread_ts from a Slack permalink.

    Slack permalink format: https://slack.example.com/archives/{channel_id}/p{timestamp}
    Returns: (channel_id, thread_ts)
    """
    # Pattern: /archives/C12345/p1234567890123456
    match = re.search(r"/archives/([A-Z0-9]+)/p(\d+)", permalink, re.IGNORECASE)
    if match:
        channel_id = match.group(1)
        # Convert p123456789012 to 123456789.012 format
        ts_raw = match.group(2)
        if len(ts_raw) > 6:
            thread_ts = f"{ts_raw[:-6]}.{ts_raw[-6:]}"
        else:
            thread_ts = ts_raw
        return channel_id, thread_ts
    return "", ""


def _build_fetch_info(item: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Build fetch tool name and parameters for a search result.

    Tool names and parameters are matched to the actual MCP server implementations:
    - Slack (korotovsky/slack-mcp-server): conversations_replies
    - GitHub (@modelcontextprotocol/server-github): get_issue, get_file_contents
    - GDrive (@modelcontextprotocol/server-gdrive): Uses resources, not tools
    """
    service = item.get("service")
    if service == "slack":
        # korotovsky/slack-mcp-server uses conversations_replies
        # Try to get channel_id and thread_ts directly, or parse from permalink
        channel_id = item.get("channel_id", "")
        thread_ts = item.get("thread_ts", "")

        if not channel_id or not thread_ts:
            permalink = item.get("permalink", item.get("uri", ""))
            parsed_channel, parsed_ts = _parse_slack_permalink(permalink)
            channel_id = channel_id or parsed_channel
            thread_ts = thread_ts or parsed_ts

        # The real Slack MCP server returns channel names (e.g., "#general") not IDs
        # and the search results already include the full text, so skip fetch
        if not channel_id or not channel_id.startswith(("C", "D", "G")):
            # Channel name detected or missing - use snippet as content
            return "slack.skip", {}, "message"

        return "slack.conversations_replies", {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
        }, "message"

    if service == "github":
        # @modelcontextprotocol/server-github uses get_issue, get_file_contents
        kind = item.get("kind", "code")
        
        # For issues/PRs with proper metadata, use get_issue
        if kind in {"issue", "pr", "pull_request"}:
            owner = item.get("owner", "")
            repo = item.get("repo", "")
            issue_number = item.get("issue_number")
            # Only fetch if we have all required parameters (issue_number can be 0 which is valid)
            if owner and repo and issue_number is not None:
                return "github.get_issue", {
                    "owner": owner,
                    "repo": repo,
                    "issue_number": issue_number,
                }, kind
        
        # For code search results, the snippet already contains relevant info
        # Skip fetch since parsing owner/repo from search_code results is complex
        return "github.skip", {}, kind

    if service == "gdrive":
        # GDrive MCP server uses resources (gdrive:///<file_id>), not tools
        # Skip fetch and use snippet as content since resource reading requires MCP resources protocol
        return "gdrive.skip", {}, item.get("kind", "file")

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
