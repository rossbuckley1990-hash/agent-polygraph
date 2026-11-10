"""Unit tests for evidence extraction and outcome parsing."""
from agentpolygraph.evidence import (
    RunReceipt, classify_command, path_matches, _scoped_target,
    _parse_test_outcome, _parse_lint_outcome,
)
from agentpolygraph.session import ToolResult


def res(stdout="", stderr="", text="", exit_code=None, is_error=False):
    return ToolResult(
        index=0, tool_use_id="t", is_error=is_error, text=text,
        stdout=stdout, stderr=stderr, exit_code=exit_code,
    )


def test_classify():
    assert "test" in classify_command("python -m pytest tests/ -q")
    assert "test" in classify_command("npm test")
    assert "test" in classify_command("go test ./...")
    assert "test" in classify_command("make test")
    assert "lint" in classify_command("ruff check .")
    assert "lint" in classify_command("npx eslint src/")
    assert "build" in classify_command("npm run build")
    assert "format" in classify_command("black .")
    assert "format" in classify_command("ruff format .")
    assert not classify_command("ls -la")


def test_pytest_fail():
    r = res(stdout="2 failed, 3 passed in 0.41s", exit_code=1)
    assert _parse_test_outcome(r, "2 failed, 3 passed in 0.41s") == "fail"


def test_pytest_pass():
    r = res(stdout="5 passed in 0.35s", exit_code=0)
    assert _parse_test_outcome(r, "5 passed in 0.35s") == "pass"


def test_jest_style():
    r = res(stdout="Tests: 2 failed, 3 passed, 5 total", exit_code=1)
    assert _parse_test_outcome(r, "Tests: 2 failed, 3 passed, 5 total") == "fail"


def test_go_test():
    assert _parse_test_outcome(res("--- FAIL: TestLogin", 1), "--- FAIL: TestLogin") == "fail"
    assert _parse_test_outcome(res("ok  \tgithub.com/x/y\t0.2s", 0), "ok  \tgithub.com/x/y\t0.2s") == "pass"


def test_traceback_is_fail():
    r = res(stdout="Traceback (most recent call last):\n  File ...", exit_code=1)
    assert _parse_test_outcome(r, r.stdout) == "fail"


def test_lint_outcomes():
    assert _parse_lint_outcome(res("Found 2 errors (line-length E501)", 1),
                               "Found 2 errors (line-length E501)") == "fail"
    assert _parse_lint_outcome(res("All checks passed!", 0),
                               "All checks passed!") == "pass"
    assert _parse_lint_outcome(res("0 problems", 0), "0 problems") == "pass"


def test_scoped_detection():
    assert _scoped_target("pytest tests/test_mini.py -q") == "tests/test_mini.py"
    assert _scoped_target("python -m pytest tests/test_mini.py -q") == "tests/test_mini.py"
    assert _scoped_target("pytest -q") is None
    assert _scoped_target("pytest") is None
    assert _scoped_target("go test ./...") is None
    assert _scoped_target("go test ./pkg/auth") == "./pkg/auth"


def test_path_matches():
    assert path_matches("config/settings.yaml", "config/settings.yaml")
    assert path_matches("config/settings.yaml", "/home/x/app/config/settings.yaml")
    assert path_matches("src/util.py", "src/util.py")
    assert not path_matches("config/settings.yaml", "config/other.yaml")
    assert not path_matches("config/settings.yaml", None)
