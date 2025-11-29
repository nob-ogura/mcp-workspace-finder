#!/usr/bin/env python3
"""Mock GitHub MCP server with JSON-RPC 2.0 support."""
from __future__ import annotations

from typing import Any

from tests.mocks.base_mcp_server import BaseMcpServer


class GitHubMcpServer(BaseMcpServer):
    """Mock GitHub MCP server that returns fake search results."""

    def __init__(self):
        super().__init__("github")

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_code",
                "description": "Search for code across GitHub repositories",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string"},
                        "per_page": {"type": "integer"},
                    },
                    "required": ["q"],
                },
            },
            {
                "name": "search_issues",
                "description": "Search for issues and pull requests",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string"},
                        "per_page": {"type": "integer"},
                    },
                    "required": ["q"],
                },
            },
            {
                "name": "get_issue",
                "description": "Get the contents of an issue within a repository",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "repo": {"type": "string"},
                        "issue_number": {"type": "integer"},
                    },
                    "required": ["owner", "repo", "issue_number"],
                },
            },
            {
                "name": "get_file_contents",
                "description": "Get the contents of a file from a repository",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "repo": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["owner", "repo", "path"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name == "search_code":
            query = arguments.get("q", "")
            limit = arguments.get("per_page", 3)
            return [
                {
                    "service": "github",
                    "title": f"org/repo: src/file{i}.py",
                    "snippet": f"This is a mock GitHub code about {query}",
                    "uri": f"https://github.com/org/repo/blob/main/src/file{i}.py",
                    "kind": "code",
                    "owner": "org",
                    "repo": "repo",
                    "path": f"src/file{i}.py",
                }
                for i in range(min(limit, 5))
            ]
        elif tool_name == "search_issues":
            query = arguments.get("q", "")
            limit = arguments.get("per_page", 3)
            return [
                {
                    "service": "github",
                    "title": f"GitHub issue matching {query}",
                    "snippet": f"This is a mock GitHub issue about {query}",
                    "uri": f"https://github.com/org/repo/issues/{i}",
                    "kind": "issue",
                    "owner": "org",
                    "repo": "repo",
                    "issue_number": i,
                }
                for i in range(min(limit, 5))
            ]
        elif tool_name == "get_issue":
            owner = arguments.get("owner", "")
            repo = arguments.get("repo", "")
            issue_number = arguments.get("issue_number", 0)
            return [{"text": f"Full content of GitHub issue from {owner}/{repo}#{issue_number}"}]
        elif tool_name == "get_file_contents":
            owner = arguments.get("owner", "")
            repo = arguments.get("repo", "")
            path = arguments.get("path", "")
            return [{"text": f"# File contents from {owner}/{repo}/{path}\n\ndef main():\n    pass"}]
        else:
            raise ValueError(f"Unknown tool: {tool_name}")


def main() -> None:
    server = GitHubMcpServer()
    server.run()


if __name__ == "__main__":
    main()
