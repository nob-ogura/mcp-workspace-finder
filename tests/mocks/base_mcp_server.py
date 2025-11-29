#!/usr/bin/env python3
"""Base MCP server implementation for mock servers.

Implements JSON-RPC 2.0 over stdio for MCP protocol compatibility.
"""
from __future__ import annotations

import json
import signal
import sys
from abc import ABC, abstractmethod
from typing import Any


class BaseMcpServer(ABC):
    """Base class for MCP mock servers that handle JSON-RPC 2.0 over stdio."""

    def __init__(self, name: str):
        self.name = name
        self._running = True

    def _handle_term(self, signum, frame):
        """Exit cleanly on SIGTERM."""
        self._running = False
        sys.exit(0)

    def _send_response(self, request_id: int | str, result: Any) -> None:
        """Send a JSON-RPC 2.0 response."""
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()

    def _send_error(self, request_id: int | str | None, code: int, message: str) -> None:
        """Send a JSON-RPC 2.0 error response."""
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()

    @abstractmethod
    def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Handle a tools/call request. Override in subclasses."""
        pass

    def handle_read_resource(self, uri: str) -> dict[str, Any]:
        """Handle a resources/read request. Override in subclasses if needed.
        
        Returns a dict with 'contents' key containing list of content items.
        Each content item should have 'uri' and either 'text' or 'blob'.
        """
        raise NotImplementedError(f"resources/read not implemented for {self.name}")

    def _process_request(self, line: str) -> None:
        """Process a single JSON-RPC request."""
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            self._send_error(None, -32700, f"Parse error: {e}")
            return

        request_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result = self.handle_tool_call(tool_name, arguments)
                self._send_response(request_id, {"content": result})
            except Exception as e:
                self._send_error(request_id, -32000, str(e))
        elif method == "resources/read":
            uri = params.get("uri", "")
            try:
                result = self.handle_read_resource(uri)
                self._send_response(request_id, result)
            except NotImplementedError as e:
                self._send_error(request_id, -32601, str(e))
            except Exception as e:
                self._send_error(request_id, -32000, str(e))
        elif method == "tools/list":
            # Return list of available tools
            self._send_response(request_id, {"tools": self.list_tools()})
        elif method == "initialize":
            # MCP initialization handshake
            self._send_response(request_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": self.name, "version": "1.0.0"},
                "capabilities": {"tools": {}, "resources": {}},
            })
        else:
            self._send_error(request_id, -32601, f"Method not found: {method}")

    def list_tools(self) -> list[dict[str, Any]]:
        """Return list of available tools. Override in subclasses."""
        return []

    def run(self) -> None:
        """Main loop: read stdin, process requests, write responses."""
        signal.signal(signal.SIGTERM, self._handle_term)

        # Signal readiness
        print(f"mock {self.name} server ready", flush=True)

        try:
            while self._running:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if line:
                    self._process_request(line)
        except KeyboardInterrupt:
            pass


