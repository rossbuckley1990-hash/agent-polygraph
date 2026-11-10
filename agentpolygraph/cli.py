"""AgentPolygraph command-line interface."""
from __future__ import annotations

import argparse
import json
import os
import sys

from agentpolygraph import __version__
from agentpolygraph.audit import audit_session
from agentpolygraph.discovery import describe_session, find_sessions, session_roots
from agentpolygraph.report import render_json, render_markdown, render_report


def _examples_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")


def _cmd_audit(args: argparse.Namespace) -> int:
    path = args.session
    if not path:
        sessions = find_sessions(limit=1)
        if not sessions:
            roots = session_roots()
            print(
                "No Claude Code sessions found{}.".format(
                    " (looked in {})".format(", ".join(roots)) if roots else ""
                )
            )
            print("Tip: try the built-in demo instead:\n")
            print("    agentpolygraph demo")
            return 2
        path = sessions[0]
        print("(auditing most recent session: {})".format(path))
    if not os.path.isfile(path):
        print("error: session file not found: {}".format(path), file=sys.stderr)
        return 2
    try:
        res = audit_session(path, root=args.root, strict=args.strict)
    except Exception as exc:  # noqa: BLE001
        print("error: failed to parse session: {}".format(exc), file=sys.stderr)
        return 2

    if args.json:
        print(render_json(res))
    else:
        color = sys.stdout.isatty() and not args.no_color and not os.environ.get("NO_COLOR")
        print(render_report(res, color=color))
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(render_markdown(res))
        print("(markdown report written to {})".format(args.md))
    return res.exit_code


def _cmd_sessions(args: argparse.Namespace) -> int:
    sessions = find_sessions(limit=args.limit)
    if not sessions:
        print("No Claude Code sessions found.")
        print("Tip: agentpolygraph demo")
        return 0
    print("{:<60} {:<10} {:>8}".format("SESSION", "MODIFIED", "LINES"))
    for s in sessions:
        d = describe_session(s)
        name = os.path.relpath(s, os.path.dirname(os.path.dirname(s)))
        if len(name) > 58:
            name = name[:55] + "..."
        print("{:<60} {:<10} {:>8}".format(name, d["mtime"], d["lines"]))
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    liar = os.path.join(_examples_dir(), "the_liar.jsonl")
    print("=== DEMO: a synthetic Claude Code session where the agent lies ===")
    print()
    try:
        res = audit_session(liar, root=None, strict=False)
    except Exception as exc:  # noqa: BLE001
        print("error: demo failed: {}".format(exc), file=sys.stderr)
        return 2
    color = sys.stdout.isatty() and not args.no_color and not os.environ.get("NO_COLOR")
    print(render_report(res, color=color))
    print("(run `agentpolygraph audit agentpolygraph/examples/the_honest.jsonl` "
          "to see an honest agent clear the polygraph)")
    return res.exit_code


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="agentpolygraph",
        description=(
            "The polygraph for your AI agent: audits Claude Code sessions "
            "and cross-checks every claim against the recorded tool evidence."
        ),
    )
    ap.add_argument("--version", action="version",
                    version="agentpolygraph {}".format(__version__))
    sub = ap.add_subparsers(dest="cmd")

    audit = sub.add_parser(
        "audit", help="audit a session (default: the most recent one)"
    )
    audit.add_argument("session", nargs="?", default=None,
                       help="path to a session .jsonl (default: most recent)")
    audit.add_argument("--root", default=None,
                       help="project directory for live file checks")
    audit.add_argument("--json", action="store_true",
                       help="machine-readable JSON output")
    audit.add_argument("--md", default=None, metavar="FILE",
                       help="also write a markdown report to FILE")
    audit.add_argument("--strict", action="store_true",
                       help="exit 1 on unverifiable claims too")
    audit.add_argument("--no-color", action="store_true")
    audit.set_defaults(func=_cmd_audit)

    sessions = sub.add_parser("sessions", help="list recent sessions")
    sessions.add_argument("--limit", type=int, default=15)
    sessions.set_defaults(func=_cmd_sessions)

    demo = sub.add_parser("demo", help="run the built-in liar demo")
    demo.add_argument("--no-color", action="store_true")
    demo.set_defaults(func=_cmd_demo)

    mcp = sub.add_parser("mcp", help="run as a stdio MCP server")
    mcp.set_defaults(func=_cmd_mcp)

    return ap


def _cmd_mcp(args: argparse.Namespace) -> int:
    from agentpolygraph.mcp import serve
    return serve()


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # bare `agentpolygraph` == `agentpolygraph audit` (zero-typing wow)
    if not argv:
        argv = ["audit"]
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
