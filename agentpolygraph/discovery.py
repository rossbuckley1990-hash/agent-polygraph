"""Find Claude Code session transcripts on this machine."""
from __future__ import annotations

import glob
import os
import time
from typing import List, Optional


def session_roots() -> List[str]:
    roots = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        roots.append(os.path.join(os.path.expanduser(env), "projects"))
    roots.append(os.path.expanduser("~/.claude/projects"))
    roots.append(os.path.expanduser("~/.config/claude/projects"))
    return [r for r in roots if os.path.isdir(r)]


def find_sessions(roots: Optional[List[str]] = None, limit: int = 50) -> List[str]:
    if roots is None:
        roots = session_roots()
    files: List[str] = []
    for r in roots:
        for path in glob.glob(
            os.path.join(r, "**", "*.jsonl"), recursive=True
        ):
            if os.sep + "subagents" + os.sep in path:
                continue
            files.append(path)
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    return files[:limit]


def describe_session(path: str) -> dict:
    st = os.stat(path)
    n_lines = 0
    first_user = ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            n_lines += 1
            if not first_user and '"type":"user"' in line.replace(" ", ""):
                first_user = line.strip()[:120]
    return {
        "path": path,
        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
        "lines": n_lines,
        "size_kb": int(st.st_size / 1024),
    }
