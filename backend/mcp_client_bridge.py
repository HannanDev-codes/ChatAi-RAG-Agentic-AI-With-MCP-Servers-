"""
MCP Client (path A): connect to MCP servers and expose their tools to the agent.

Default: starts this project's own MCP server (mcp_rag_server) over stdio.
Optional: set MCP_EXTRA_SERVERS as JSON list of {name, command, args}.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def format_error(exc: BaseException) -> str:
    children = getattr(exc, "exceptions", None)
    if children:
        return "; ".join(format_error(child) for child in children)[:1000]
    detail = str(exc).strip()
    if isinstance(exc, TimeoutError):
        return "TimeoutError: MCP server did not respond before the timeout. Check /api/mcp/status and restart the app."
    return type(exc).__name__ + (": " + detail if detail else ": MCP operation failed without an error message")


@dataclass
class McpTool:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpBridge:
    tools: list[McpTool] = field(default_factory=list)
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _sessions: dict[str, Any] = field(default_factory=dict)

    def enabled(self) -> bool:
        return os.getenv("ENABLE_MCP", "0").strip() not in {"0", "false", "False"}

    async def connect_default(self) -> None:
        """Connect to built-in RAG MCP server + optional extras."""
        if not self.enabled():
            return
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            self.errors["setup"] = format_error(exc)
            return

        # Built-in MCP servers shipped with this project
        py = os.getenv("PYTHON", sys.executable)
        servers = [
            {
                "name": "rag-agent-tools",
                "command": py,
                "args": ["-m", "backend.mcp_rag_server"],
                "cwd": str(ROOT),
            },
            {
                "name": "filesystem",
                "command": py,
                "args": ["-m", "backend.mcp_filesystem_server"],
                "cwd": str(ROOT),
            },
            {
                "name": "sqlite",
                "command": py,
                "args": ["-m", "backend.mcp_sqlite_server"],
                "cwd": str(ROOT),
            },
            {
                "name": "fetch",
                "command": py,
                "args": ["-m", "backend.mcp_fetch_server"],
                "cwd": str(ROOT),
            },
            {
                "name": "memory-dev",
                "command": py,
                "args": ["-m", "backend.mcp_memory_dev_server"],
                "cwd": str(ROOT),
            },
        ]

        extra = os.getenv("MCP_EXTRA_SERVERS", "").strip()
        if extra:
            try:
                servers.extend(json.loads(extra))
            except json.JSONDecodeError:
                pass

        await self._start_servers(servers)

    async def _start_servers(self, servers: list[dict]) -> None:
        """Each server's transport must outlive the request that first uses it."""
        ready_events = []
        for spec in servers:
            name = spec.get("name") or "server"
            if name in self._tasks:
                continue
            ready = asyncio.Event()
            self._tasks[name] = asyncio.create_task(self._serve(spec, ready), name="mcp-" + name)
            ready_events.append(ready.wait())
        if ready_events:
            await asyncio.gather(*ready_events)

    async def _serve(self, spec: dict, ready: asyncio.Event) -> None:
        # Enter AND exit AnyIO task groups in this same long-lived task.
        # Entering them in an HTTP request leaves the transport tied to that
        # request's lifetime; another request may then see a dead session.
        name = spec.get("name") or "server"
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(
                command=spec.get("command") or sys.executable,
                args=spec.get("args") or [],
                cwd=spec.get("cwd") or str(ROOT),
                env={**os.environ},
            )
            async with AsyncExitStack() as stack:
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), timeout=30)
                listed = await asyncio.wait_for(session.list_tools(), timeout=15)
                self._sessions[name] = session
                self.tools.extend(McpTool(
                    server=name, name=t.name, description=t.description or t.name,
                    input_schema=t.inputSchema or {"type": "object", "properties": {}},
                ) for t in listed.tools)
                self.errors.pop(name, None)
                ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.errors[name] = format_error(exc)
        finally:
            self._sessions.pop(name, None)
            self.tools[:] = [t for t in self.tools if t.server != name]
            ready.set()

    async def close(self) -> None:
        self._stop.set()
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._sessions.clear()
        self.tools.clear()

    def as_openai_tools(self) -> list[dict]:
        """Convert MCP tools to Groq/OpenAI function-tool schema."""
        out = []
        for t in self.tools:
            # prefix to avoid name clashes with local tools
            full = f"mcp_{t.server}_{t.name}".replace("-", "_")
            props = (t.input_schema or {}).get("properties") or {}
            required = (t.input_schema or {}).get("required") or []
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": full,
                        "description": f"[MCP:{t.server}] {t.description}",
                        "parameters": {
                            "type": "object",
                            "properties": props,
                            "required": required,
                        },
                    },
                }
            )
        return out

    def resolve(self, full_name: str) -> tuple[str, str] | None:
        """Map mcp_<server>_<tool> back to (server, tool)."""
        if not full_name.startswith("mcp_"):
            return None
        rest = full_name[len("mcp_") :]
        for t in self.tools:
            prefix = f"{t.server}_{t.name}".replace("-", "_")
            if rest == prefix:
                return t.server, t.name
        # fuzzy: longest server match
        for t in self.tools:
            s = t.server.replace("-", "_")
            if rest.startswith(s + "_"):
                tool = rest[len(s) + 1 :]
                if tool == t.name.replace("-", "_") or tool == t.name:
                    return t.server, t.name
        return None

    async def call(self, full_name: str, arguments: dict) -> str:
        resolved = self.resolve(full_name)
        if not resolved:
            detail = self.errors.get("memory-dev") if full_name.startswith("mcp_memory_dev_") else None
            return json.dumps({"ok": False, "error": detail or f"MCP tool unavailable: {full_name}. Check ENABLE_MCP=1 and /api/mcp/status."})
        server, tool = resolved
        session = self._sessions.get(server)
        if not session:
            return json.dumps({"ok": False, "error": f"MCP server not connected: {server}"})
        try:
            result = await asyncio.wait_for(session.call_tool(tool, arguments or {}), timeout=30)
            parts = []
            for block in result.content or []:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
                else:
                    parts.append(str(block))
            text = "\n".join(parts)
            if getattr(result, "isError", False):
                return json.dumps({"ok": False, "error": text or "MCP tool failed"})
            structured = getattr(result, "structuredContent", None)
            if structured is None:
                try:
                    structured = json.loads(text)
                except (TypeError, ValueError):
                    structured = text
            # FastMCP v1 may encode a list as one JSON text block per item.
            # Joining those blocks is not a JSON array; preserve every note.
            if server == "memory-dev" and tool in {"search_notes", "get_all_notes", "list_directory"}:
                if isinstance(structured, dict) and "result" in structured:
                    structured = structured["result"]
                if not isinstance(structured, list):
                    notes = []
                    for part in parts:
                        item = json.loads(part)
                        if isinstance(item, list):
                            notes.extend(item)
                        elif isinstance(item, dict) and ("content" in item or (tool == "list_directory" and "name" in item)):
                            notes.append(item)
                        else:
                            raise ValueError("Unexpected memory note response")
                    structured = notes
            return json.dumps({"ok": True, "data": structured})
        except Exception as e:
            return json.dumps({"ok": False, "error": format_error(e)})


# Process-wide bridge (lazy)
_bridge: McpBridge | None = None
_lock = asyncio.Lock()


async def get_mcp_bridge() -> McpBridge:
    global _bridge
    async with _lock:
        if _bridge is None:
            _bridge = McpBridge()
            await _bridge.connect_default()
        return _bridge
