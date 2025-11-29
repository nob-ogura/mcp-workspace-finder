#!/usr/bin/env python3
"""Mock Google Drive MCP server with JSON-RPC 2.0 support."""
from __future__ import annotations

from typing import Any

from tests.mocks.base_mcp_server import BaseMcpServer


class GdriveMcpServer(BaseMcpServer):
    """Mock Google Drive MCP server that returns fake search results."""

    def __init__(self):
        super().__init__("gdrive")

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search",
                "description": "Search for files in Google Drive",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name == "search":
            query = arguments.get("query", "")
            # GDrive search doesn't support limit parameter
            return [
                {
                    "service": "gdrive",
                    "title": f"Drive document about {query}",
                    "snippet": f"This is a mock Drive document mentioning {query}",
                    "uri": f"gdrive:///file{i}",  # Use gdrive:// URI format
                    "kind": "file",
                    "mimeType": "application/pdf",
                }
                for i in range(3)  # Fixed to 3 results since no limit param
            ]
        else:
            raise ValueError(f"Unknown tool: {tool_name}")


def main() -> None:
    server = GdriveMcpServer()
    server.run()


if __name__ == "__main__":
    main()
