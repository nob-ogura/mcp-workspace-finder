"""MCP client-based search and fetch runners.

This module provides search and fetch runner factories that communicate
with MCP servers via stdio. These runners implement the interface expected
by search_pipeline.py.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Mapping

from app.config import load_server_definitions, resolve_service_modes
from app.process import launch_services_async, RuntimeStatus
from app.search_pipeline import FetchResult, SearchResult

logger = logging.getLogger(__name__)


class McpClientError(Exception):
    """Raised when MCP client operations fail."""


class StdioMcpClient:
    """Simple MCP client that communicates via stdio with a subprocess.

    This is a minimal implementation for the PoC. For production use,
    consider using the official mcp package's ClientSession.
    """

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        name: str,
    ):
        self._process = process
        self._name = name
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and return the response."""
        if self._process.stdin is None or self._process.stdout is None:
            raise McpClientError(f"{self._name}: process stdin/stdout not available")

        self._request_id += 1
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params

        try:
            request_line = json.dumps(request) + "\n"
            self._process.stdin.write(request_line.encode())
            await self._process.stdin.drain()

            # Read response with timeout
            response_line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=30.0,
            )

            if not response_line:
                raise McpClientError(f"{self._name}: empty response from server")

            response = json.loads(response_line.decode())

            if "error" in response:
                error = response["error"]
                raise McpClientError(
                    f"{self._name}: {error.get('message', 'unknown error')}"
                )

            return response.get("result")

        except asyncio.TimeoutError:
            raise McpClientError(f"{self._name}: timeout waiting for response")
        except json.JSONDecodeError as exc:
            raise McpClientError(f"{self._name}: invalid JSON response: {exc}")

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if self._process.stdin is None:
            raise McpClientError(f"{self._name}: process stdin not available")

        notification: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params

        try:
            notification_line = json.dumps(notification) + "\n"
            self._process.stdin.write(notification_line.encode())
            await self._process.stdin.drain()
        except Exception as exc:
            raise McpClientError(f"{self._name}: failed to send notification: {exc}")

    async def initialize(self) -> None:
        """Perform MCP protocol initialization handshake."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            # Send initialize request
            init_params = {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mcp-workspace-finder",
                    "version": "0.1.0",
                },
            }

            try:
                result = await self._send_request("initialize", init_params)
                logger.debug("%s: initialized with capabilities: %s", self._name, result)

                # Note: Skip "initialized" notification as some MCP servers (e.g., github v0.6.2)
                # don't support it and will return "Method not found" errors
                self._initialized = True

            except McpClientError as exc:
                logger.warning("%s: initialization failed: %s", self._name, exc)
                raise

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on the MCP server and return the result."""
        # Ensure initialized before calling tools
        if not self._initialized:
            await self.initialize()

        async with self._lock:
            result = await self._send_request(
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments,
                },
            )
            # Extract content from result
            if isinstance(result, dict):
                return result.get("content", [])
            return result

    async def read_resource(self, uri: str) -> str:
        """Read a resource from the MCP server using resources/read.

        Args:
            uri: The resource URI (e.g., "gdrive:///file_id")

        Returns:
            The resource content as a string

        Raises:
            McpClientError: If the request fails or returns invalid data
        """
        # Ensure initialized before reading resources
        if not self._initialized:
            await self.initialize()

        async with self._lock:
            result = await self._send_request(
                "resources/read",
                {"uri": uri},
            )

            # Extract content from result
            # MCP resources/read returns: { contents: [{ uri, text?, blob?, mimeType? }] }
            if not isinstance(result, dict):
                raise McpClientError(f"{self._name}: invalid resources/read response format")

            contents = result.get("contents")
            if not contents or not isinstance(contents, list) or len(contents) == 0:
                raise McpClientError(f"{self._name}: empty contents in resources/read response")

            first_content = contents[0]
            if not isinstance(first_content, dict):
                raise McpClientError(f"{self._name}: invalid content item in resources/read response")

            # Try text first, then blob (base64 encoded)
            if "text" in first_content:
                return first_content["text"]
            elif "blob" in first_content:
                try:
                    decoded = base64.b64decode(first_content["blob"])
                    return decoded.decode("utf-8")
                except (ValueError, UnicodeDecodeError) as exc:
                    raise McpClientError(f"{self._name}: failed to decode blob content: {exc}")
            else:
                raise McpClientError(
                    f"{self._name}: resources/read response has neither 'text' nor 'blob'"
                )


def _parse_slack_csv_results(csv_text: str, max_results: int = 3) -> list[Mapping[str, Any]]:
    """Parse CSV results from korotovsky/slack-mcp-server.

    The real Slack MCP server returns CSV with columns:
    MsgID,UserID,UserName,RealName,Channel,ThreadTs,Text,Time,Reactions,Cursor
    """
    import csv
    import io

    results = []
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            if len(results) >= max_results:
                break
            # Extract channel_id from Channel field (e.g., "#general" -> need to lookup)
            # For now, use MsgID as thread_ts since that's the message identifier
            msg_id = row.get("MsgID", "")
            channel = row.get("Channel", "")
            text = row.get("Text", "")
            user = row.get("RealName", row.get("UserName", ""))
            time = row.get("Time", "")

            # The MsgID is in format like "1762499528.459139" which is the timestamp
            thread_ts = msg_id

            results.append({
                "service": "slack",
                "title": f"Message from {user}" if user else "Slack message",
                "snippet": text[:200] if text else "",
                "uri": "",  # Real server doesn't return permalinks
                "kind": "message",
                "channel": channel,  # Channel name, not ID
                "thread_ts": thread_ts,
                "msg_id": msg_id,
                "user": user,
                "time": time,
            })
    except Exception as exc:
        logger.warning("Failed to parse Slack CSV: %s", exc)

    return results


def create_search_runner(
    client: StdioMcpClient,
    service: str,
    tool_name: str,
) -> Any:
    """Create a search runner function for the given MCP client and service.

    Args:
        client: The MCP client to use for communication
        service: Service name (slack, github, gdrive)
        tool_name: The tool name to call for search (e.g., "conversations_search_messages")

    Returns:
        An async function that takes search params and returns results
    """
    # Get the parameter mapping for this service
    param_mapping = SEARCH_PARAM_MAPPINGS.get(service, {"query_param": "query", "limit_param": "limit"})

    async def search_runner(params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        query = params.get("query", "")
        max_results = params.get("max_results", 3)

        # Build arguments using the correct parameter names for this service
        arguments: dict[str, Any] = {
            param_mapping["query_param"]: query,
        }
        if param_mapping.get("limit_param"):
            arguments[param_mapping["limit_param"]] = max_results

        try:
            result = await client.call_tool(
                tool_name,
                arguments,
            )

            # Handle Slack's special CSV format
            if service == "slack" and isinstance(result, list) and result:
                first_item = result[0]
                if isinstance(first_item, dict) and first_item.get("type") == "text":
                    csv_text = first_item.get("text", "")
                    return _parse_slack_csv_results(csv_text, max_results)

            # Normalize results to expected format (for other services and mock servers)
            normalized = []
            for item in result if isinstance(result, list) else [result]:
                if isinstance(item, dict):
                    normalized.append({
                        "service": service,
                        "title": item.get("title", item.get("name", "Untitled")),
                        "snippet": item.get("snippet", item.get("text", ""))[:200],
                        "uri": item.get("uri", item.get("url", item.get("permalink", ""))),
                        "kind": item.get("kind", item.get("type", "file")),
                        **{k: v for k, v in item.items() if k not in ("title", "snippet", "uri", "kind")},
                    })

            return normalized

        except McpClientError as exc:
            logger.warning("%s search failed: %s", service, exc)
            raise

    return search_runner


def create_fetch_runner(
    client: StdioMcpClient,
    service: str,
    tool_name: str | None,
) -> Any:
    """Create a fetch runner function for the given MCP client and service.

    Args:
        client: The MCP client to use for communication
        service: Service name (slack, github, gdrive)
        tool_name: The tool name to call for fetch (e.g., "get_message").
            If None, the runner will use read_resource with the URI from fetch_params.

    Returns:
        An async function that takes a SearchResult and returns content
    """

    async def fetch_runner(result: SearchResult) -> Any:
        try:
            # If tool_name is None, use read_resource with the URI
            if tool_name is None:
                uri = result.fetch_params.get("uri")
                if not uri:
                    raise McpClientError(
                        f"{service}: fetch_params missing 'uri' for read_resource"
                    )
                return await client.read_resource(uri)

            # Otherwise, use the tool call
            content = await client.call_tool(
                tool_name,
                result.fetch_params,
            )

            # Extract text content from response
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict):
                    return first.get("text", first.get("content", str(first)))
                return str(first)
            return str(content) if content else ""

        except McpClientError as exc:
            logger.warning("%s fetch failed: %s", service, exc)
            raise

    return fetch_runner


# Tool name mappings for each service
# Note: "drive" in servers.yaml maps to "gdrive" in search parameters
SEARCH_TOOLS = {
    "slack": "conversations_search_messages",  # korotovsky/slack-mcp-server
    "github": "search_code",  # @modelcontextprotocol/server-github
    "gdrive": "search",  # @modelcontextprotocol/server-gdrive
    "drive": "search",  # alias for gdrive
}

# Parameter name mappings for each service's search tool
# Each MCP server uses different parameter names
SEARCH_PARAM_MAPPINGS = {
    "slack": {"query_param": "search_query", "limit_param": "limit"},
    "github": {"query_param": "q", "limit_param": "per_page"},
    "gdrive": {"query_param": "query", "limit_param": None},  # gdrive doesn't support limit
    "drive": {"query_param": "query", "limit_param": None},
}

FETCH_TOOLS = {
    # Slack: Use conversations_replies for fetching thread messages
    "slack": "conversations_replies",
    "slack.conversations_replies": "conversations_replies",
    # GitHub: get_issue for issues, get_file_contents for code
    "github": "get_issue",
    "github.get_issue": "get_issue",
    "github.get_file_contents": "get_file_contents",
    # GDrive: Uses resources (gdrive:///<file_id>) rather than tools
    # The fetch runner should handle this via MCP resources protocol
    "gdrive": None,  # Resource-based, not tool-based
    "drive": None,
}

# Service name normalization (servers.yaml -> search params)
SERVICE_NAME_MAP = {
    "drive": "gdrive",
}


async def create_mcp_runners_from_processes(
    processes: Mapping[str, asyncio.subprocess.Process],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create search and fetch runners from running MCP server processes.

    Args:
        processes: Mapping of service names to their subprocess handles

    Returns:
        Tuple of (search_runners, fetch_runners) dicts
    """
    search_runners: dict[str, Any] = {}
    fetch_runners: dict[str, Any] = {}

    for raw_service, process in processes.items():
        # Normalize service name (e.g., "drive" -> "gdrive")
        service = SERVICE_NAME_MAP.get(raw_service, raw_service)
        client = StdioMcpClient(process, service)

        search_tool = SEARCH_TOOLS.get(service) or SEARCH_TOOLS.get(raw_service)
        if search_tool:
            runner = create_search_runner(client, service, search_tool)
            search_runners[service] = runner
            # Also register under original name for compatibility
            if raw_service != service:
                search_runners[raw_service] = runner

        # Add fetch runners with both simple and qualified names
        # Check if service has a fetch tool defined (can be None for resource-based fetch)
        if service in FETCH_TOOLS or raw_service in FETCH_TOOLS:
            fetch_tool = FETCH_TOOLS.get(service, FETCH_TOOLS.get(raw_service))
            runner = create_fetch_runner(client, service, fetch_tool)
            fetch_runners[service] = runner
            if fetch_tool:
                fetch_runners[f"{service}.{fetch_tool}"] = runner
            else:
                # For resource-based fetch (tool_name=None), register with special name
                fetch_runners[f"{service}.__read_resource__"] = runner
            
            # Always register a __read_resource__ runner for services that support it
            # This allows code search results to be fetched via resources/read
            if fetch_tool is not None:
                # Create a separate runner for read_resource
                resource_runner = create_fetch_runner(client, service, None)
                fetch_runners[f"{service}.__read_resource__"] = resource_runner
                if raw_service != service:
                    fetch_runners[f"{raw_service}.__read_resource__"] = resource_runner
            
            # Also register under original name for compatibility
            if raw_service != service:
                fetch_runners[raw_service] = runner
                if fetch_tool:
                    fetch_runners[f"{raw_service}.{fetch_tool}"] = runner
                else:
                    fetch_runners[f"{raw_service}.__read_resource__"] = runner

    return search_runners, fetch_runners


async def run_oneshot_with_mcp(
    query: str,
    *,
    force_mock: bool,
    llm_client: Any | None = None,
    config_path: Any | None = None,
) -> Any | None:
    """Run oneshot search using MCP servers.

    This function:
    1. Loads server definitions from config
    2. Resolves service modes (mock/real)
    3. Launches MCP servers
    4. Creates search/fetch runners
    5. Generates search parameters using LLM (if available)
    6. Executes the search-fetch-summarize pipeline
    7. Cleans up server processes

    Args:
        query: The search query
        force_mock: If True, force mock mode for all services
        llm_client: Optional LLM client for search parameter generation
        config_path: Optional path to servers.yaml

    Returns:
        SearchFetchSummaryResult if successful, None otherwise
    """
    from app.llm_search import generate_search_parameters
    from app.summary_pipeline import run_search_fetch_and_summarize_pipeline

    # Load configuration
    definitions = load_server_definitions(config_path)

    # Resolve service modes
    resolved = resolve_service_modes(
        definitions,
        force_mock=force_mock,
        allow_real=not force_mock,
    )

    # Launch MCP servers
    statuses = await launch_services_async(
        definitions,
        resolved,
        readiness_timeout=10.0,
    )

    # Get running processes
    processes = {
        name: status.process
        for name, status in statuses.items()
        if status.process is not None
    }

    if not processes:
        logger.warning("No MCP servers could be started")
        return None

    try:
        # Create runners from processes
        search_runners, fetch_runners = await create_mcp_runners_from_processes(
            processes
        )

        if not search_runners:
            logger.warning("No search runners available")
            return None

        # Generate search parameters
        if llm_client is not None:
            generation = generate_search_parameters(query, llm_client)
            searches = generation.searches
        else:
            # Fallback: create simple search for each available service
            searches = [
                {"service": name, "query": query, "max_results": 3}
                for name in search_runners.keys()
            ]

        if not searches:
            logger.warning("No search parameters generated")
            return None

        # Run the full pipeline
        result = await run_search_fetch_and_summarize_pipeline(
            query,
            searches,
            search_runners=search_runners,
            fetch_runners=fetch_runners,
            llm_client=llm_client,
        )

        return result

    finally:
        # Cleanup: terminate all processes
        for process in processes.values():
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    process.kill()

