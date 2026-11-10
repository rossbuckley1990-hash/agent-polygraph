# AgentPolygraph

> **Your AI agent says the tests pass. AgentPolygraph reads the receipts.**
> One CLI audits any Claude Code session — every claim the agent made, cross-checked against the tool evidence recorded in the same transcript. **Zero setup. Works retroactively. 100% local. Zero API cost.**

```text
  ❌ CONTRADICTED    "✅ All tests pass now — 5 tests passed."
      last test run (event 4) shows failure: "2 failed, 3 passed in 0.41s"
```

Every developer who works with coding agents knows this moment: the agent cheerfully announces *"All tests pass! I created the config file and fixed the bug."* — and half the time, it didn't. Tests were never run. The file was never written. The "fix" was never verified.

Here is the thing nobody tells you: **the evidence was there all along.** Claude Code records every command and its output, every file it wrote, every exit code — in a local JSONL transcript, right next to the agent's claims. The receipts and the lies sit in the same file. Nobody cross-checks them.

**AgentPolygraph does.** No hooks to install, no config, no API keys. Point it at any session — including sessions from *before* you ever heard of this tool — and it reads the receipts.

---

## Quickstart

```bash
# try it in 10 seconds — no Claude Code needed:
uvx --from git+https://github.com/rossbuckley1990-hash/agent-polygraph agentpolygraph demo

# audit your own most recent Claude Code session:
uvx --from git+https://github.com/rossbuckley1990-hash/agent-polygraph agentpolygraph
```

or:

```bash
git clone https://github.com/rossbuckley1990-hash/agent-polygraph
cd agent-polygraph
pip install -e .
agentpolygraph            # audits your newest session, instantly
agentpolygraph demo       # catch a lying agent in 10 seconds
agentpolygraph sessions   # list what it can see
```

No `~/.claude` sessions on this machine? `demo` runs a built-in synthetic session so you can watch it catch a lie anyway.

## What it looks like

```text
  AgentPolygraph — receipts for your AI agent
  session   : a1b2c3d4 · please fix the failing test in tests/test_auth.py and …
  project   : /home/you/code/app
  evidence  : 1 commands · 1 test runs (last: FAIL) · 1 file edits

  THE AGENT SAID:

  ❌ CONTRADICTED    "✅ All tests pass now — 5 tests passed."
      last test run (event 4) shows failure: "2 failed, 3 passed in 0.41s"  [event 4]

  ⚠️  UNVERIFIABLE    "I've read through tests/test_auth.py carefully, fixed the login bug, and I also c…"
      edits found (Edit tests/test_auth.py at event 7), but nothing was re-tested after them

  ❌ CONTRADICTED    "I've read through tests/test_auth.py carefully, fixed the login bug, and I also c…"
      no Write/Edit receipt for this path in the session receipts

  ✅ VERIFIED        "I've read through tests/test_auth.py carefully, fixed the login bug, and I also c…"
      receipt: read tests/test_auth.py (event 2)

  verdict: 1 verified · 2 contradicted · 1 unverifiable · 0 hedged
  ❌ the receipts disagree with the agent.
```

Run it on an honest session (`agentpolygraph audit agentpolygraph/examples/the_honest.jsonl`) and every claim comes back ✅ — the tool does not accuse agents that show receipts.

## The verdicts

| Verdict | Meaning |
|---|---|
| ✅ **VERIFIED** | A receipt confirms it: tests actually ran *and* passed, the Write actually happened, the fix was re-tested. |
| ❌ **CONTRADICTED** | The receipts actively disagree: tests ran and **failed**, the "created" file has no Write receipt (and isn't on disk), nothing was ever modified. |
| ⚠️ **UNVERIFIABLE** | No receipt settles it. "Tests pass" but no test was ever run. Tests passed *then files were edited and never re-run* (the classic trap). A scoped run (`pytest one_file.py`) can't back "all tests". |
| ⊘ **HEDGED** | "Tests *should* pass now", "I *will* run the suite" — predictions and plans are never counted against the agent. |

Exit code `1` when anything is contradicted — so CI can gate on it.

## What gets cross-checked

- **Test claims** — "all tests pass", "5 tests passed" (count is compared!), "I ran the tests". Understands pytest, unittest, jest, vitest, go test, cargo test, rspec, make test, npm/yarn/pnpm test, and more; parses real pass/fail output lines, exit codes, and scoped vs. full-suite runs.
- **Fix claims** — "the bug is fixed": were files actually edited, and was anything *re-tested after the edit*?
- **File claims** — created / updated / deleted `path/to/file`: matched against Write/Edit receipts, and optionally against the live filesystem (`--root`).
- **Lint & build claims** — "lint is clean", "build succeeds": matched against ruff/eslint/mypy/tsc/make/cargo receipts and their recorded output.
- **Formatter claims** — "I formatted the code".
- **Todo claims** — items the agent marked `completed` in its todo list, checked for backing actions.
- **Both English and Chinese** — “所有测试已通过”, “已修复该 bug”, “创建了文件 config/settings.yaml”.

## CLI

```text
agentpolygraph                       # audit the most recent session
agentpolygraph audit <session.jsonl> # audit a specific session
agentpolygraph audit --root .        # also check the live filesystem
agentpolygraph audit --json          # machine-readable output
agentpolygraph audit --strict        # exit 1 on unverifiable claims too
agentpolygraph audit --md report.md  # also write a markdown report
agentpolygraph sessions              # list recent sessions
agentpolygraph demo                  # built-in liar demo (no Claude Code needed)
```

### Gate your agent's PRs on truth

```yaml
# .github/workflows/agent-gate.yml
- name: The agent's claims must survive the receipts
  run: agentpolygraph audit $SESSION_JSONL --root .
```

Any contradicted claim fails the step. *The agent does not merge until the receipts agree.*

### Let agents audit themselves (MCP)

```bash
claude mcp add agentpolygraph -- agentpolygraph mcp
```

Exposes `audit_last_session` / `audit_session` over stdio MCP — an agent can polygraph its own session (or its subagent's) before claiming done. Self-check before you have to.

## Privacy

- Reads local files only: `~/.claude/projects/**/*.jsonl` (or the session path you give it).
- No network calls, no telemetry, no API keys, zero dependencies (pure Python stdlib).
- Point it at a file and it never leaves your machine.

## Honest limitations

- Claim extraction is pattern-based. Common phrasings (EN + 中文) are covered; exotic ones can slip through. When in doubt, the tool says **unverifiable** rather than accusing.
- Verdicts are about the *session receipts*: "contradicted" means the transcript's own evidence disagrees. It does not re-run your tests.
- v1 speaks Claude Code's transcript format (the most widely used agent log). Cursor / Gemini CLI / Codex CLI adapters are on the roadmap — the engine is format-agnostic, only the parser changes.
- Subagent sidechain files (`…/subagents/*.jsonl`) are not merged into the main audit yet.

## How it works

1. Parse the session JSONL into a timeline: assistant messages, tool calls, tool results, exit codes.
2. Extract **claims** from what the agent *said* (bilingual patterns, with hedge/negation/question guards so honest agents aren't misread).
3. Extract **receipts** from what the agent *did*: which commands ran, what their output said, which files were written or edited.
4. Cross-check: state claims are judged temporally (the *last* run before the claim; edits after a pass make it stale), action claims by receipt existence.
5. Report: verdicts with the exact contradicting evidence line, and an exit code CI can gate on.

~1,500 lines of dependency-free Python. A 6,000-event session audits in about half a second.

## 中文说明

**AgentPolygraph —— 给你的 AI Agent 做测谎。**

你的 Agent 说“所有测试已通过”？它可能根本没跑测试。Claude Code 会把每一条命令和它的真实输出记录在本地 JSONL 里 —— 证据一直都在，只是没人去核对。

一条命令，审计任意会话：每个说法（“测试通过”、“已修复”、“创建了文件”）都与同一场会话里记录的工具凭证逐一比对。零配置、可回溯、纯本地、零 API 费用，中英文说法都支持。

```bash
uvx --from git+https://github.com/rossbuckley1990-hash/agent-polygraph agentpolygraph demo
```

## Credits & kinship

Inspired by the philosophy of [agent-reach](https://github.com/Panniantong/agent-reach) — unlock something expensive with a clever side-door, ship it as one dead-simple CLI — aimed at the other half of the agent trust problem: not *what your agent can see*, but *whether you can believe what it says*.

## License

MIT
