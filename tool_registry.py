#!/usr/bin/env python3
"""
tool_registry.py - Tool Registry + Execution Engine

Provides OpenAI-compatible function calling capabilities:
  1. Register custom tools (name, description, JSON Schema parameters, handler)
  2. Execute tool calls and return results
  3. Export schema list in OpenAI tools format
  4. Built-in common tools (time, HTTP, file operations, Python execution, directory listing)

Usage:
    registry = ToolRegistry()
    registry.register("my_tool", "Description", {...schema...}, my_handler)
    result = await registry.execute("my_tool", {"arg": "value"})
    schemas = registry.get_openai_schemas()
"""

import ast
import asyncio
import datetime
import json
import logging
import re
import shlex
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger("tools")


# ── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    """Tool execution result"""
    tool_call_id: str
    content: str
    is_error: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tool_call_id": self.tool_call_id,
            "role": "tool",
            "content": self.content,
            "is_error": self.is_error,
        }


@dataclass
class ToolDefinition:
    """Tool definition"""
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[[dict, dict], Awaitable[Any]]
    category: str = "general"
    enabled: bool = True

    def to_openai_schema(self) -> dict:
        """Export as OpenAI function calling schema format"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ── Tool Registry ───────────────────────────────────────────────────────────

class ToolRegistry:
    """Tool registry + execution engine"""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._register_builtins()

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable[[dict, dict], Awaitable[Any]],
        category: str = "custom",
        enabled: bool = True,
    ):
        """Register a tool"""
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            category=category,
            enabled=enabled,
        )
        logger.info("Registered tool: %s", name)

    def unregister(self, name: str):
        """Unregister a tool"""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def list_enabled(self) -> list[ToolDefinition]:
        return [t for t in self._tools.values() if t.enabled]

    def get_openai_schemas(self, names: list[str] | None = None) -> list[dict]:
        """Export list of schemas in OpenAI tools format"""
        tools = self.list_enabled()
        if names:
            tools = [t for t in tools if t.name in names]
        return [t.to_openai_schema() for t in tools]

    async def execute(
        self,
        name: str,
        arguments: dict,
        context: dict | None = None,
        tool_call_id: str = "",
    ) -> ToolResult:
        """Execute a tool call"""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                tool_call_id=tool_call_id,
                content=f"Error: Tool '{name}' not found",
                is_error=True,
            )
        if not tool.enabled:
            return ToolResult(
                tool_call_id=tool_call_id,
                content=f"Error: Tool '{name}' is disabled",
                is_error=True,
            )

        ctx = context or {}
        logger.info("Executing tool: %s, args=%s", name, json.dumps(arguments, ensure_ascii=False)[:200])

        try:
            result = await tool.handler(arguments, ctx)
            if isinstance(result, ToolResult):
                return result
            content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
            return ToolResult(
                tool_call_id=tool_call_id,
                content=content,
            )
        except Exception as e:
            logger.error("Tool '%s' execution failed: %s", name, e)
            return ToolResult(
                tool_call_id=tool_call_id,
                content=f"Error executing tool '{name}': {e}",
                is_error=True,
            )

    # ── Built-in Tools ──────────────────────────────────────────────────────

    def _register_builtins(self):
        """Register built-in tools"""

        self.register(
            name="get_current_time",
            description="Get current date and time. Allows specifying a timezone.",
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Timezone name, e.g. 'UTC', 'America/New_York'. Defaults to local timezone.",
                    },
                },
            },
            handler=self._tool_get_current_time,
            category="system",
        )

        self.register(
            name="http_get",
            description="Send an HTTP GET request and return the response content. Suitable for fetching web pages, API data, etc.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Request URL"},
                    "headers": {
                        "type": "object",
                        "description": "Custom request headers",
                    },
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 30},
                    "max_length": {"type": "integer", "description": "Maximum character length of returned content", "default": 5000},
                },
                "required": ["url"],
            },
            handler=self._tool_http_get,
            category="network",
        )

        self.register(
            name="read_file",
            description="Read local file contents. Supports text files.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "encoding": {"type": "string", "description": "File encoding", "default": "utf-8"},
                    "max_lines": {"type": "integer", "description": "Maximum lines to read", "default": 500},
                },
                "required": ["path"],
            },
            handler=self._tool_read_file,
            category="filesystem",
        )

        self.register(
            name="write_file",
            description="Write content to a local file. Overwrites if file already exists.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "File content"},
                    "encoding": {"type": "string", "description": "File encoding", "default": "utf-8"},
                    "append": {"type": "boolean", "description": "Whether to append to file", "default": False},
                },
                "required": ["path", "content"],
            },
            handler=self._tool_write_file,
            category="filesystem",
        )

        self.register(
            name="list_directory",
            description="List directory contents.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path"},
                    "pattern": {"type": "string", "description": "File name filter pattern (glob)", "default": "*"},
                },
                "required": ["path"],
            },
            handler=self._tool_list_directory,
            category="filesystem",
        )

        self.register(
            name="run_python",
            description="Execute Python code and return stdout/evaluated expression.",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code"},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 10},
                },
                "required": ["code"],
            },
            handler=self._tool_run_python,
            category="compute",
        )

        self.register(
            name="run_shell",
            description="Execute a shell command and return its output.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command"},
                    "cwd": {"type": "string", "description": "Working directory", "default": "."},
                    "timeout": {"type": "number", "description": "Timeout in seconds", "default": 30},
                },
                "required": ["command"],
            },
            handler=self._tool_run_shell,
            category="system",
        )

        self.register(
            name="json_parse",
            description="Parse a JSON string and return formatted result.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "JSON string"},
                },
                "required": ["text"],
            },
            handler=self._tool_json_parse,
            category="utility",
        )

    # ── Built-in Tool Handlers ──────────────────────────────────────────────

    async def _tool_get_current_time(self, args: dict, ctx: dict) -> str:
        tz_name = args.get("timezone", "local")
        try:
            if tz_name.lower() in ("local", "", None):
                now = datetime.datetime.now()
            else:
                from zoneinfo import ZoneInfo
                now = datetime.datetime.now(ZoneInfo(tz_name))
            return json.dumps({
                "datetime": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "weekday": now.strftime("%A"),
                "timezone": tz_name,
            }, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    async def _tool_http_get(self, args: dict, ctx: dict) -> str:
        import httpx
        url = args["url"]
        headers = args.get("headers") or {}
        timeout = args.get("timeout", 30)
        max_length = args.get("max_length", 5000)

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                content = resp.text
                truncated = len(content) > max_length
                if truncated:
                    content = content[:max_length] + f"\n... (truncated, total {len(resp.text)} chars)"
                return json.dumps({
                    "status_code": resp.status_code,
                    "content": content,
                    "truncated": truncated,
                }, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    async def _tool_read_file(self, args: dict, ctx: dict) -> str:
        path = args["path"]
        encoding = args.get("encoding", "utf-8")
        max_lines = args.get("max_lines", 500)

        try:
            p = Path(path)
            if not p.exists():
                return f"Error: File not found: {path}"
            if not p.is_file():
                return f"Error: Not a file: {path}"

            content = p.read_text(encoding=encoding, errors="replace")
            lines = content.split("\n")
            truncated = len(lines) > max_lines
            if truncated:
                content = "\n".join(lines[:max_lines]) + f"\n... (truncated, total {len(lines)} lines)"

            return json.dumps({
                "path": str(p.resolve()),
                "size": p.stat().st_size,
                "lines": len(lines),
                "truncated": truncated,
                "content": content,
            }, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    async def _tool_write_file(self, args: dict, ctx: dict) -> str:
        path = args["path"]
        content = args["content"]
        encoding = args.get("encoding", "utf-8")
        append = args.get("append", False)

        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(p, mode, encoding=encoding) as f:
                f.write(content)
            return json.dumps({
                "path": str(p.resolve()),
                "bytes_written": len(content.encode(encoding)),
                "mode": "append" if append else "overwrite",
            }, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    async def _tool_list_directory(self, args: dict, ctx: dict) -> str:
        path = args["path"]

        try:
            p = Path(path)
            if not p.exists():
                return f"Error: Directory not found: {path}"
            if not p.is_dir():
                return f"Error: Not a directory: {path}"

            entries = []
            for entry in sorted(p.iterdir()):
                if not entry.name.startswith("."):
                    entries.append({
                        "name": entry.name,
                        "type": "directory" if entry.is_dir() else "file",
                        "size": entry.stat().st_size if entry.is_file() else None,
                    })

            return json.dumps({
                "path": str(p.resolve()),
                "entries": entries,
                "total": len(entries),
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Error: {e}"

    async def _tool_run_python(self, args: dict, ctx: dict) -> str:
        code = args["code"]

        import io
        from contextlib import redirect_stdout, redirect_stderr

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        safe_globals = {
            "__builtins__": {
                n: getattr(__builtins__, n) if hasattr(__builtins__, n) else __builtins__[n]
                for n in dir(__builtins__)
                if not n.startswith("_")
            }
        }
        safe_globals["__builtins__"]["__import__"] = __import__

        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(compile(code, "<tool>", "exec"), safe_globals)
            return json.dumps({
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
                "exit_code": 0,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue() + f"\n{e}",
                "exit_code": 1,
                "error": str(e),
            }, ensure_ascii=False)

    async def _tool_run_shell(self, args: dict, ctx: dict) -> str:
        command = args["command"]
        cwd = args.get("cwd", ".")
        timeout = args.get("timeout", 30)

        try:
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_exec(
                    *shlex.split(command, posix=False),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return json.dumps({"error": f"Command timed out after {timeout}s"}, ensure_ascii=False)

            return json.dumps({
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "exit_code": proc.returncode,
            }, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    async def _tool_json_parse(self, args: dict, ctx: dict) -> str:
        text = args["text"]
        try:
            data = json.loads(text)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except json.JSONDecodeError as e:
            return f"JSON parse error: {e}"


# ToolCallParser has moved to tool_call_parser.py
from tool_call_parser import ToolCallParser  # noqa: E402,F401}