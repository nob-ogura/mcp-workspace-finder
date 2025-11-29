#!/usr/bin/env python3
"""Mock Slack MCP server with JSON-RPC 2.0 support."""
from __future__ import annotations

from typing import Any

from tests.mocks.base_mcp_server import BaseMcpServer


class SlackMcpServer(BaseMcpServer):
    """Mock Slack MCP server that returns fake search results."""

    def __init__(self):
        super().__init__("slack")

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "conversations_search_messages",
                "description": "Search messages in Slack channels, threads, and DMs",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "search_query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            },
            {
                "name": "conversations_replies",
                "description": "Get thread replies for a message",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "string"},
                        "thread_ts": {"type": "string"},
                    },
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name == "conversations_search_messages":
            query = arguments.get("search_query", "")
            limit = arguments.get("limit", 3)
            return [
                {
                    "service": "slack",
                    "title": f"Slack message about {query}",
                    "snippet": f"This is a mock Slack message mentioning {query}",
                    "uri": f"https://slack.example.com/archives/C123{i}/p1234{i}56789",
                    "kind": "message",
                    "permalink": f"https://slack.example.com/archives/C123{i}/p1234{i}56789",
                    "channel_id": f"C123{i}",
                    "thread_ts": f"1234{i}.56789",
                }
                for i in range(min(limit, 5))
            ]
        elif tool_name == "conversations_replies":
            channel_id = arguments.get("channel_id", "")
            thread_ts = arguments.get("thread_ts", "")
            return [{"text": f"Full content of Slack thread from {channel_id}/{thread_ts}"}]
        else:
            raise ValueError(f"Unknown tool: {tool_name}")


def main() -> None:
    server = SlackMcpServer()
    server.run()


if __name__ == "__main__":
    main()
