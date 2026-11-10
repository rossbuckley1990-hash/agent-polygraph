"""Parse Claude Code session transcripts (JSONL) into an event timeline.

Claude Code stores every session at:
    ~/.claude/projects/<munged-project-path>/<session-id>.jsonl
one JSON object per line. Assistant lines carry Anthropic-API-style
messages (text / thinking / tool_use blocks); user lines carry either a
plain prompt or tool_result blocks; system/summary lines carry metadata.

This module is deliberately tolerant: fields are duck-typed so the
parser survives minor format drift between Claude Code versions.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TextEvent:
    index: int
    role: str  # "user" | "assistant"
    text: str
    ts: Optional[str] = None


@dataclass
class ToolUse:
    index: int
    id: str
    name: str  # Bash, Write, Edit, Read, Glob, Grep, NotebookEdit, ...
    input: Dict[str, Any]
    ts: Optional[str] = None


@dataclass
class ToolResult:
    index: int
    tool_use_id: str
    is_error: bool
    text: str
    stdout: str
    stderr: str
    exit_code: Optional[int]
    ts: Optional[str] = None


@dataclass
class TodoEvent:
    index: int
    todos: List[Dict[str, Any]]
    ts: Optional[str] = None


@dataclass
class Session:
    path: str
    session_id: str = ""
    project: str = ""
    started: Optional[str] = None
    ended: Optional[str] = None
    title: str = ""
    events: List[Any] = field(default_factory=list)

    @property
    def tool_results_by_id(self) -> Dict[str, ToolResult]:
        return {
            e.tool_use_id: e for e in self.events if isinstance(e, ToolResult)
        }


def _block_text(content: Any) -> str:
    """Extract plain text from a message content value (str or block list)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text")
                if t:
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _result_text(block: Dict[str, Any]) -> str:
    """Extract text from a tool_result block (content may be str or list)."""
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                t = b.get("text")
                if t:
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _mk_tool_result(obj: Dict[str, Any], block: Dict[str, Any], idx: int,
                    ts: Optional[str]) -> ToolResult:
    tur = obj.get("toolUseResult")
    stdout, stderr, exit_code = "", "", None
    if isinstance(tur, dict):
        stdout = str(tur.get("stdout") or "")
        stderr = str(tur.get("stderr") or "")
        ec = tur.get("exitCode")
        if isinstance(ec, int):
            exit_code = ec
    text = _result_text(block)
    is_error = bool(block.get("isError") or block.get("is_error"))
    return ToolResult(
        index=idx,
        tool_use_id=str(block.get("tool_use_id") or ""),
        is_error=is_error,
        text=text,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        ts=ts,
    )


def parse_session(path: str) -> Session:
    """Parse one session JSONL file into an ordered event timeline."""
    s = Session(path=path)
    first_user: Optional[str] = None
    title_from_summary = ""
    project_cwd = ""

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        idx = 0
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            typ = obj.get("type")
            ts = obj.get("timestamp")
            if not s.session_id and obj.get("sessionId"):
                s.session_id = str(obj["sessionId"])
            if not s.started and ts:
                s.started = str(ts)
            if ts:
                s.ended = str(ts)
            if not project_cwd and obj.get("cwd"):
                project_cwd = str(obj["cwd"])

            if typ == "summary":
                if not title_from_summary and obj.get("summary"):
                    title_from_summary = str(obj["summary"])
                continue
            if typ != "user" and typ != "assistant":
                continue  # system, file-history-snapshot, attachment, ...

            msg = obj.get("message") or {}
            content = msg.get("content")

            if isinstance(content, str):
                if typ == "user" and content.strip():
                    if first_user is None:
                        first_user = content.strip()
                    s.events.append(TextEvent(idx, "user", content, ts))
                    idx += 1
                continue
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text":
                    txt = block.get("text") or ""
                    if txt.strip():
                        if typ == "user" and first_user is None:
                            first_user = txt.strip()
                        s.events.append(TextEvent(idx, typ, txt, ts))
                        idx += 1
                elif bt == "tool_use":
                    name = str(block.get("name") or "")
                    inp = block.get("input") or {}
                    if name == "TodoWrite":
                        s.events.append(
                            TodoEvent(idx, _as_todo_list(inp.get("todos")), ts)
                        )
                    else:
                        s.events.append(
                            ToolUse(
                                idx,
                                str(block.get("id") or ""),
                                name,
                                inp if isinstance(inp, dict) else {},
                                ts,
                            )
                        )
                    idx += 1
                elif bt == "tool_result":
                    s.events.append(_mk_tool_result(obj, block, idx, ts))
                    idx += 1
                # "thinking" blocks are deliberately ignored

    if not s.title:
        if first_user:
            s.title = first_user.splitlines()[0][:80]
        elif title_from_summary:
            s.title = title_from_summary[:80]
    if not s.project:
        s.project = project_cwd or _decode_project_dir(
            os.path.basename(os.path.dirname(os.path.abspath(path)))
        )
    return s


def _as_todo_list(v: Any) -> List[Dict[str, Any]]:
    if not isinstance(v, list):
        return []
    out = []
    for item in v:
        if isinstance(item, dict):
            out.append(item)
    return out


def _decode_project_dir(dirname: str) -> str:
    """Best-effort decode of the munged project directory name."""
    if dirname.startswith("-"):
        return dirname.replace("-", "/")
    return dirname
