"""Minimal stdio MCP server (zero dependencies).

Exposes two tools so an agent can audit itself (or its peers):

  - audit_last_session  — audit the most recent Claude Code session
  - audit_session       — audit a given session .jsonl path

Wire it into Claude Code:
    claude mcp add agentpolygraph -- agentpolygraph mcp
"""
from __future__ import annotations

import json
import sys

from agentpolygraph.audit import audit_session
from agentpolygraph.discovery import find_sessions
from agentpolygraph.report import render_report

_TOOLS = [
    {
        "name": "audit_last_session",
        "description": (
            "Audit the most recent Claude Code session on this machine: "
            "cross-check the agent's claims against recorded tool evidence. "
            "Returns a verdict report (verified / contradicted / unverifiable)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "audit_session",
        "description": (
            "Audit a specific Claude Code session transcript (.jsonl)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "path to .jsonl"}
            },
            "required": ["path"],
        },
    },
]


def _result_text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _handle_tool_call(name: str, arguments: dict) -> dict:
    if name == "audit_last_session":
        sessions = find_sessions(limit=1)
        if not sessions:
            return _result_text("No Claude Code sessions found on this machine.")
        res = audit_session(sessions[0])
        return _result_text(render_report(res, color=False))
    if name == "audit_session":
        path = arguments.get("path", "")
        try:
            res = audit_session(path)
        except Exception as exc:  # noqa: BLE001
            return {"content": [{"type": "text", "text": "error: {}".format(exc)}],
                    "isError": True}
        return _result_text(render_report(res, color=False))
    raise ValueError("unknown tool: {}".format(name))


def serve() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method", "")
        msg_id = msg.get("id")
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "agentpolygraph", "version": "0.1.0"},
                },
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _TOOLS}}
        elif method == "tools/call":
            try:
                result = _handle_tool_call(
                    msg["params"]["name"], msg["params"].get("arguments") or {}
                )
                resp = {"jsonrpc": "2.0", "id": msg_id, "result": result}
            except Exception as exc:  # noqa: BLE001
                resp = {
                    "jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        else:
            if msg_id is not None:
                resp = {
                    "jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
            else:
                continue
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
    return 0
