"""Evidence extraction: what do the session receipts actually show?

Every Bash command, its recorded output and exit code; every file the
agent actually wrote or edited; every test/lint/build run and whether
it passed. This is the "ground truth" side of the polygraph.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set

from agentpolygraph.session import Session, TextEvent, TodoEvent, ToolResult, ToolUse

# --------------------------------------------------------------------------
# Command classification
# --------------------------------------------------------------------------

_TEST_CMD = [
    r"\bpytest\b", r"-m\s+pytest", r"-m\s+unittest", r"unittest\s+discover",
    r"\bnpm\s+test\b", r"npm\s+run\s+test", r"\byarn\s+test\b",
    r"yarn\s+run\s+test", r"\bpnpm\s+test\b", r"\bpnpm\s+run\s+test\b",
    r"\bvitest\b", r"\bjest\b", r"\bgo\s+test\b", r"\bcargo\s+test\b",
    r"\bmvn\s+test\b", r"\bgradle\s+test\b", r"\bgradlew\s+test\b",
    r"\brspec\b", r"\bmake\s+test", r"\bmake\s+check\b", r"\bdotnet\s+test\b",
    r"\bbun\s+test\b", r"\bdeno\s+test\b", r"\bphpunit\b", r"\bbusted\b",
    r"\btox\b", r"\bnox\b",
]
_LINT_CMD = [
    r"\bruff\b", r"\beslint\b", r"\bflake8\b", r"\bpylint\b", r"\bmypy\b",
    r"\bprettier\b", r"\bgolangci", r"\bshellcheck\b", r"\brubocop\b",
    r"\bstylelint\b", r"\bbiome\b", r"\bclang-tidy\b", r"\bhadolint\b",
    r"black\s+--check", r"isort\s+--check", r"tsc\s+--noEmit",
]
_BUILD_CMD = [
    r"cmake\s+--build", r"(?:^|&&|\|\|;|\s)make(?:\s|$)", r"npm\s+run\s+build",
    r"yarn\s+build", r"pnpm\s+build", r"(?:^|\s)tsc\b", r"cargo\s+build",
    r"\bgo\s+build\b", r"dotnet\s+build", r"gradle\s+build", r"gradlew\b",
    r"mvn\s+(?:package|compile)", r"vite\s+build", r"webpack\b",
]
_FORMAT_CMD = [
    r"ruff\s+format", r"\bblack\b", r"prettier\s+--write", r"\bgofmt\b",
    r"clang-format\s+-i", r"\bisort\b(?!\s+--check)", r"\bautopep8\b",
    r"\brustfmt\b",
]
_BASH_MUTATION = re.compile(
    r"\bsed\s+[^\n]*-i|(?<![\w.-])mv(?![\w.-])|(?<![\w.-])rm(?![\w.-])|"
    r"(?<![\w.-])cp(?![\w.-])|(?<![\w.-])tee(?![\w.-])|\bmkdir\b|\btouch\b|"
    r"\btruncate\b|(?:^|\s)>{1,2}\s*\S"
)
_PATH_TOKEN = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,10}")
_REDIRECT_TARGET = re.compile(r"(?:^|\s)>{1,2}\s*(\S+)")


def _matches_any(cmd: str, patterns: List[str]) -> bool:
    return any(re.search(p, cmd, re.I) for p in patterns)


def classify_command(cmd: str) -> Set[str]:
    kinds: Set[str] = set()
    low = cmd
    if _matches_any(low, _TEST_CMD):
        kinds.add("test")
    if _matches_any(low, _LINT_CMD):
        kinds.add("lint")
    if _matches_any(low, _BUILD_CMD) and "test" not in low.split("make")[-1][:6]:
        kinds.add("build")
    if _matches_any(low, _FORMAT_CMD):
        kinds.add("format")
    return kinds


# --------------------------------------------------------------------------
# Outcome parsing
# --------------------------------------------------------------------------

_FAILED_N = re.compile(r"(\d+)\s+(?:failed|failures?)", re.I)
_ERRORS_N = re.compile(r"(\d+)\s+errors?\b", re.I)
_FAILURES_EQ = re.compile(r"failures=(\d+)")
_PASSED_N = re.compile(r"(\d+)\s+passed", re.I)
_TESTS_PASSED_N = re.compile(r"(\d+)\s+tests?\s+passed", re.I)
_FAIL_SIGNALS = re.compile(
    r"\bFAILED\b|---\s*FAIL|^FAIL\b|Traceback \(most recent call last\)|"
    r"^ok\b.*\(.*fail", re.I | re.M,
)
_GO_OK = re.compile(r"^ok\b", re.M)
_UNITTEST_OK = re.compile(r"^OK\b", re.M)

_LINT_PROBLEMS_N = re.compile(
    r"(\d+)\s+(?:errors?|problems?|issues?)", re.I
)
_LINT_FOUND_N = re.compile(r"Found\s+(\d+)\s+errors?", re.I)
_LINT_FAIL_SIGNALS = re.compile(
    r"✖|Code style issues found|would reformat|reformat-needed|"
    r"isn'?t formatted|not formatted",
    re.I,
)
_LINT_PASS_SIGNALS = re.compile(
    r"no issues|0 problems|0 errors|All checks passed|all files? unchanged|"
    r"would make no changes|no problems found",
    re.I,
)

_BUILD_FAIL_SIGNALS = re.compile(
    r"error[s]?:|fatal error|BUILD FAILED|compilation (?:failed|error)|"
    r"✖|\berror\d*\b.*\bl\d+\b",
    re.I,
)


def _combined_text(res: ToolResult) -> str:
    # toolUseResult.stdout/stderr can duplicate the tool_result block
    # content (Claude Code mirrors them); prefer the structured fields.
    if res.stdout or res.stderr:
        return "\n".join(p for p in (res.stdout, res.stderr) if p)
    return res.text


def _parse_test_outcome(res: ToolResult, text: str) -> str:
    failed = sum(int(x) for x in _FAILED_N.findall(text))
    failed += sum(int(x) for x in _FAILURES_EQ.findall(text))
    errors = sum(int(x) for x in _ERRORS_N.findall(text))
    passed = sum(int(x) for x in _PASSED_N.findall(text))
    passed += sum(int(x) for x in _TESTS_PASSED_N.findall(text))
    if failed or errors or _FAIL_SIGNALS.search(text):
        return "fail"
    if passed or _GO_OK.search(text) or _UNITTEST_OK.search(text):
        return "pass"
    if res.exit_code == 5:
        return "unknown"  # pytest: no tests collected
    if res.exit_code == 0:
        return "pass"
    if res.exit_code is not None:
        return "fail"
    return "unknown"


def _parse_lint_outcome(res: ToolResult, text: str) -> str:
    n = sum(int(x) for x in _LINT_PROBLEMS_N.findall(text))
    n += sum(int(x) for x in _LINT_FOUND_N.findall(text))
    if n > 0 or _LINT_FAIL_SIGNALS.search(text):
        return "fail"
    if _LINT_PASS_SIGNALS.search(text):
        return "pass"
    if res.exit_code == 0:
        return "pass"
    if res.exit_code is not None:
        return "fail"
    return "unknown"


def _parse_build_outcome(res: ToolResult, text: str) -> str:
    if _BUILD_FAIL_SIGNALS.search(text):
        return "fail"
    if res.exit_code == 0:
        return "pass"
    if res.exit_code is not None:
        return "fail"
    return "unknown"


def _interesting_lines(text: str, outcome: str) -> List[str]:
    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if outcome == "fail" and (
            _FAIL_SIGNALS.search(ln)
            or _FAILED_N.search(ln)
            or _ERRORS_N.search(ln)
            or _LINT_PROBLEMS_N.search(ln)
            or _LINT_FOUND_N.search(ln)
            or _BUILD_FAIL_SIGNALS.search(ln)
        ):
            lines.append(ln)
        elif outcome == "pass" and (
            _PASSED_N.search(ln)
            or _TESTS_PASSED_N.search(ln)
            or _GO_OK.search(ln)
            or _UNITTEST_OK.search(ln)
            or _LINT_PASS_SIGNALS.search(ln)
        ):
            lines.append(ln)
        if len(lines) >= 3:
            break
    return lines


def _result_failed(res: Optional[ToolResult]) -> bool:
    if res is None:
        return True  # tool_use with no recorded result: don't count it
    return bool(res.is_error) or res.exit_code not in (None, 0)


# --------------------------------------------------------------------------
# Scope detection (was the test run the full suite or one file?)
# --------------------------------------------------------------------------

_SCOPED_PATTERNS = [
    re.compile(r"(?:pytest|python3?\s+-m\s+pytest)\s+(?:-{1,2}[\w=.-]+\s+)*([^\s-][^\s]*)"),
    re.compile(r"(?:^|\s)(?:jest|vitest)\s+(?:-{1,2}[\w=.-]+\s+)*([^\s-][^\s]*)"),
    re.compile(r"\bgo\s+test\s+(\S+)"),
]


def _scoped_target(cmd: str) -> Optional[str]:
    for pat in _SCOPED_PATTERNS:
        m = pat.search(cmd)
        if not m:
            continue
        target = m.group(1).strip("\"'")
        if target.startswith("-"):
            continue
        if target in ("./...", "...", "-v"):
            continue
        if "/" in target or target.startswith("test") or "." in target:
            return target
    return None


# --------------------------------------------------------------------------
# Evidence index
# --------------------------------------------------------------------------


@dataclass
class RunReceipt:
    index: int
    kind: str            # test | lint | build | format
    cmd: str
    outcome: str         # pass | fail | unknown
    passed: int = 0
    failed: int = 0
    scoped: Optional[str] = None
    exit_code: Optional[int] = None
    lines: List[str] = field(default_factory=list)

    def quote(self) -> str:
        if self.lines:
            return self.lines[0][:100]
        if self.exit_code is not None:
            return "exit code {}".format(self.exit_code)
        return "(no output recorded)"


@dataclass
class Mutation:
    index: int
    src: str            # "tool" | "bash"
    path: Optional[str]
    cmd: str
    failed: bool = False

    def describe(self) -> str:
        if self.src == "tool":
            return "{} {}".format(self.cmd, self.path or "")
        return self.cmd[:100]


@dataclass
class Evidence:
    runs: List[RunReceipt] = field(default_factory=list)
    mutations: List[Mutation] = field(default_factory=list)
    read_paths: Set[str] = field(default_factory=set)
    read_events: List[tuple] = field(default_factory=list)
    todos: List[dict] = field(default_factory=list)
    todo_index: int = -1
    n_bash_commands: int = 0
    last_assistant_text_index: int = -1

    @property
    def test_runs(self) -> List[RunReceipt]:
        return [r for r in self.runs if r.kind == "test"]

    @property
    def lint_runs(self) -> List[RunReceipt]:
        return [r for r in self.runs if r.kind == "lint"]

    @property
    def build_runs(self) -> List[RunReceipt]:
        return [r for r in self.runs if r.kind == "build"]

    @property
    def format_runs(self) -> List[RunReceipt]:
        return [r for r in self.runs if r.kind == "format"]

    def last_test_outcome(self) -> str:
        return self.test_runs[-1].outcome if self.test_runs else "none"


def _norm_path(p: str) -> str:
    p = p.strip().strip("\"'`").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _bash_paths(cmd: str) -> List[str]:
    paths = set()
    for tok in _PATH_TOKEN.findall(cmd):
        paths.add(_norm_path(tok))
    for m in _REDIRECT_TARGET.finditer(cmd):
        paths.add(_norm_path(m.group(1)))
    return [p for p in paths if p]


def build_evidence(session: Session) -> Evidence:
    ev = Evidence()
    results = session.tool_results_by_id

    for e in session.events:
        if isinstance(e, ToolUse):
            if e.name in ("Bash", "bash"):
                ev.n_bash_commands += 1
                cmd = str(e.input.get("command") or "")
                if not cmd:
                    continue
                res = results.get(e.id)
                text = _combined_text(res) if res else ""
                kinds = classify_command(cmd)
                for kind in kinds:
                    if kind == "test":
                        outcome = _parse_test_outcome(res, text) if res else "unknown"
                        passed = sum(
                            int(x) for x in _PASSED_N.findall(text)
                        ) + sum(
                            int(x) for x in _TESTS_PASSED_N.findall(text)
                        )
                        failed = sum(
                            int(x) for x in _FAILED_N.findall(text)
                        )
                        ev.runs.append(
                            RunReceipt(
                                index=e.index, kind="test", cmd=cmd,
                                outcome=outcome, passed=passed, failed=failed,
                                scoped=_scoped_target(cmd),
                                exit_code=res.exit_code if res else None,
                                lines=_interesting_lines(text, outcome),
                            )
                        )
                    elif kind == "lint":
                        outcome = (
                            _parse_lint_outcome(res, text) if res else "unknown"
                        )
                        ev.runs.append(
                            RunReceipt(
                                index=e.index, kind="lint", cmd=cmd,
                                outcome=outcome,
                                exit_code=res.exit_code if res else None,
                                lines=_interesting_lines(text, outcome),
                            )
                        )
                    elif kind == "build":
                        outcome = (
                            _parse_build_outcome(res, text) if res else "unknown"
                        )
                        ev.runs.append(
                            RunReceipt(
                                index=e.index, kind="build", cmd=cmd,
                                outcome=outcome,
                                exit_code=res.exit_code if res else None,
                                lines=_interesting_lines(text, outcome),
                            )
                        )
                    else:  # format
                        ev.runs.append(
                            RunReceipt(
                                index=e.index, kind="format", cmd=cmd,
                                outcome=(
                                    "fail"
                                    if res and _result_failed(res)
                                    else "pass"
                                ),
                                exit_code=res.exit_code if res else None,
                                lines=[],
                            )
                        )
                if _BASH_MUTATION.search(cmd):
                    failed = _result_failed(res)
                    for p in _bash_paths(cmd):
                        ev.mutations.append(
                            Mutation(e.index, "bash", p, cmd, failed)
                        )
                    if not _bash_paths(cmd):
                        ev.mutations.append(
                            Mutation(e.index, "bash", None, cmd, failed)
                        )
                # reads via bash
                if re.search(r"\bcat\b|\bhead\b|\btail\b|\bless\b", cmd):
                    for p in _bash_paths(cmd):
                        ev.read_paths.add(p)
                        ev.read_events.append((e.index, p))
            elif e.name in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
                p = e.input.get("file_path") or e.input.get("notebook_path") or ""
                res = results.get(e.id)
                ev.mutations.append(
                    Mutation(
                        e.index, "tool", _norm_path(str(p)),
                        e.name, _result_failed(res),
                    )
                )
            elif e.name in ("Read", "Glob", "Grep"):
                p = (
                    e.input.get("file_path")
                    or e.input.get("path")
                    or e.input.get("pattern")
                    or ""
                )
                if p:
                    ev.read_paths.add(_norm_path(str(p)))
                    ev.read_events.append((e.index, _norm_path(str(p))))
        elif isinstance(e, TextEvent) and e.role == "assistant":
            ev.last_assistant_text_index = e.index
        elif isinstance(e, TodoEvent):
            ev.todos = e.todos
            ev.todo_index = e.index
    return ev


def path_matches(claim_path: str, receipt_path: Optional[str]) -> bool:
    """Tolerant path match: exact, or one is a suffix of the other."""
    if not receipt_path:
        return False
    a, b = _norm_path(claim_path), _norm_path(receipt_path)
    if not a or not b:
        return False
    if a == b:
        return True
    return a.endswith("/" + b) or b.endswith("/" + a)
