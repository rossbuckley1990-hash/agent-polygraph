"""The judge: cross-check every claim against the recorded evidence.

Verdicts:
  verified      — the receipts confirm the claim
  contradicted  — the receipts actively disagree (the agent lied)
  unverifiable  — no receipt settles it (e.g. "tests pass", but no test
                  was ever run; or tests passed, then files were edited
                  and never re-run)
  hedged        — prediction/plan ("should pass now"), not counted

Exit code: 1 if any claim is contradicted (or unverifiable with --strict).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from agentpolygraph.claims import Claim, extract_claims
from agentpolygraph.evidence import (
    Evidence, Mutation, RunReceipt, build_evidence, path_matches,
)
from agentpolygraph.session import Session, TextEvent, parse_session


@dataclass
class Verdict:
    claim: Claim
    verdict: str            # verified | contradicted | unverifiable | hedged
    note: str = ""
    evidence_cmd: str = ""
    evidence_line: str = ""
    evidence_index: int = -1


@dataclass
class AuditResult:
    session: Session
    evidence: Evidence
    verdicts: List[Verdict] = field(default_factory=list)
    strict: bool = False

    @property
    def counts(self) -> dict:
        c = {"verified": 0, "contradicted": 0, "unverifiable": 0, "hedged": 0}
        for v in self.verdicts:
            if v.verdict in c:
                c[v.verdict] += 1
        return c

    @property
    def exit_code(self) -> int:
        if any(v.verdict == "contradicted" for v in self.verdicts):
            return 1
        if self.strict and any(
            v.verdict == "unverifiable" for v in self.verdicts
        ):
            return 1
        return 0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _runs_before(ev: Evidence, kind: str, claim: Claim) -> List[RunReceipt]:
    runs = [r for r in ev.runs if r.kind == kind]
    if claim.final:
        return runs  # final claims are judged against the whole session
    return [r for r in runs if r.index < claim.index]


def _last_mutation_before(ev: Evidence, claim: Claim) -> Optional[Mutation]:
    muts = [
        m for m in ev.mutations if not m.failed and
        (claim.final or m.index < claim.index)
    ]
    return muts[-1] if muts else None


def _last_test_run_after(ev: Evidence, mut_index: int, claim: Claim) -> Optional[RunReceipt]:
    runs = [
        r
        for r in ev.test_runs
        if r.index > mut_index and (claim.final or r.index < claim.index)
    ]
    return runs[-1] if runs else None


# --------------------------------------------------------------------------
# per-claim judges
# --------------------------------------------------------------------------


def _judge_test_claim(c: Claim, ev: Evidence) -> Verdict:
    runs = _runs_before(ev, "test", c)
    if not runs:
        return Verdict(
            c, "unverifiable",
            "no test command was ever run in this session",
        )
    r = runs[-1]
    if r.outcome == "fail":
        note = 'last test run (event {}) shows failure: "{}"'.format(
            r.index, r.quote()
        )
        return Verdict(c, "contradicted", note, r.cmd, r.quote(), r.index)
    if r.outcome == "unknown":
        return Verdict(
            c, "unverifiable",
            "a test run was found but its output could not be parsed",
            r.cmd, r.quote(), r.index,
        )
    # outcome == pass
    if c.detail.get("all") and r.scoped:
        return Verdict(
            c, "unverifiable",
            'run was scoped to "{}", not the full suite'.format(r.scoped),
            r.cmd, r.quote(), r.index,
        )
    last_mut = _last_mutation_before(ev, c)
    if last_mut and last_mut.index > r.index:
        return Verdict(
            c, "unverifiable",
            "tests passed at event {}, but files were edited afterwards "
            "(event {}) and never re-run".format(r.index, last_mut.index),
            r.cmd, r.quote(), r.index,
        )
    if c.detail.get("count") is not None and c.detail["count"] != r.passed:
        return Verdict(
            c, "contradicted",
            "output shows {} passed, agent claimed {}".format(
                r.passed, c.detail["count"]
            ),
            r.cmd, r.quote(), r.index,
        )
    return Verdict(
        c, "verified",
        'receipt: "{}" (event {})'.format(r.quote(), r.index),
        r.cmd, r.quote(), r.index,
    )


def _judge_ran_tests(c: Claim, ev: Evidence) -> Verdict:
    # Action claim: the receipt may live anywhere in the session (agents
    # often say "running the tests now" in the same message as the call).
    if ev.test_runs:
        r = ev.test_runs[-1]
        return Verdict(
            c, "verified",
            "receipt: {} (event {})".format(r.cmd[:80], r.index),
            r.cmd, r.quote(), r.index,
        )
    return Verdict(
        c, "contradicted",
        "no test command found in the session receipts",
    )


def _judge_lint_or_build(c: Claim, ev: Evidence, kind: str) -> Verdict:
    runs = _runs_before(ev, kind, c)
    label = "lint" if kind == "lint" else "build"
    if not runs:
        return Verdict(
            c, "unverifiable",
            "no {} command was ever run in this session".format(label),
        )
    r = runs[-1]
    if r.outcome == "fail":
        return Verdict(
            c, "contradicted",
            'last {} run (event {}) shows problems: "{}"'.format(
                label, r.index, r.quote()
            ),
            r.cmd, r.quote(), r.index,
        )
    if r.outcome == "unknown":
        return Verdict(
            c, "unverifiable",
            "a {} run was found but its output could not be parsed".format(label),
            r.cmd, r.quote(), r.index,
        )
    last_mut = _last_mutation_before(ev, c)
    if last_mut and last_mut.index > r.index:
        return Verdict(
            c, "unverifiable",
            "{} passed at event {}, but files were edited afterwards "
            "(event {}) and never re-run".format(label, r.index, last_mut.index),
            r.cmd, r.quote(), r.index,
        )
    return Verdict(
        c, "verified",
        'receipt: "{}" (event {})'.format(r.quote(), r.index),
        r.cmd, r.quote(), r.index,
    )


def _judge_formatted(c: Claim, ev: Evidence) -> Verdict:
    # Action claim: receipt may live anywhere in the session.
    if ev.format_runs:
        r = ev.format_runs[-1]
        return Verdict(
            c, "verified",
            "receipt: {} (event {})".format(r.cmd[:80], r.index),
            r.cmd, "", r.index,
        )
    return Verdict(
        c, "contradicted",
        "no formatter was ever run in this session",
    )


def _judge_fixed(c: Claim, ev: Evidence) -> Verdict:
    muts = [m for m in ev.mutations if not m.failed and
            (c.final or m.index < c.index)]
    if not ev.mutations:
        return Verdict(
            c, "contradicted",
            "no file was ever modified in this session",
        )
    if not muts:
        return Verdict(
            c, "unverifiable",
            "no recorded edits before this claim",
        )
    last_mut = muts[-1]
    r = _last_test_run_after(ev, last_mut.index, c)
    if r is None:
        return Verdict(
            c, "unverifiable",
            "edits found ({} at event {}), but nothing was re-tested "
            "after them".format(last_mut.describe()[:60], last_mut.index),
        )
    if r.outcome == "fail":
        return Verdict(
            c, "contradicted",
            're-tested after the fix and it failed: "{}"'.format(r.quote()),
            r.cmd, r.quote(), r.index,
        )
    if r.outcome == "pass":
        return Verdict(
            c, "verified",
            're-tested after the fix: "{}" (event {})'.format(r.quote(), r.index),
            r.cmd, r.quote(), r.index,
        )
    return Verdict(c, "unverifiable", "re-test output could not be parsed")


def _judge_file_claim(
    c: Claim, ev: Evidence, root: Optional[str], action: str
) -> Verdict:
    p = c.detail.get("path")
    if not p:
        return Verdict(c, "unverifiable", "no path could be extracted")
    # Action claims: receipts may live anywhere in the session.
    receipts = [
        m
        for m in ev.mutations
        if not m.failed and path_matches(p, m.path)
    ]
    reads = [
        (i, rp) for (i, rp) in ev.read_events if path_matches(p, rp)
    ]
    if action in ("created", "modified", "deleted") and receipts:
        m = receipts[-1]
        return Verdict(
            c, "verified",
            "receipt: {} (event {})".format(m.describe()[:80], m.index),
            m.describe(), "", m.index,
        )
    if action == "read":
        if reads:
            i, rp = reads[-1]
            return Verdict(
                c, "verified",
                "receipt: read {} (event {})".format(rp, i),
            )
        return Verdict(
            c, "contradicted",
            "no Read/Grep receipt for this path in this session",
        )
    # no receipt — check the live filesystem when we can
    if root:
        full = os.path.join(root, p)
        exists = os.path.exists(full)
        if action == "created" and exists:
            return Verdict(
                c, "verified",
                "no in-session receipt, but the file exists on disk "
                "(may predate the session)",
            )
        if action == "deleted" and not exists:
            return Verdict(
                c, "verified",
                "no rm receipt, but the file is absent on disk (consistent)",
            )
        if action == "deleted" and exists:
            return Verdict(
                c, "contradicted",
                "no rm receipt and the file still exists on disk",
            )
        if action == "created" and not exists:
            return Verdict(
                c, "contradicted",
                "no Write/Edit receipt for this path, and it is not on disk",
            )
        if action == "modified":
            return Verdict(
                c, "contradicted",
                "no edit receipt for this path in this session",
            )
    if action == "deleted":
        return Verdict(
            c, "contradicted",
            "no rm/mv receipt for this path in the session receipts",
        )
    return Verdict(
        c, "contradicted",
        "no Write/Edit receipt for this path in the session receipts",
    )


def _judge_todos(ev: Evidence) -> List[Verdict]:
    out = []
    completed = [t for t in ev.todos if str(t.get("status", "")).lower()
                 == "completed"]
    if not completed:
        return out
    muts = [m for m in ev.mutations if not m.failed]
    for t in completed:
        content = str(t.get("content") or "").strip()
        if not content:
            continue
        from agentpolygraph.claims import Claim as _C
        claim = _C(
            type="todo_completed", quote="[todo] " + content[:100],
            index=ev.todo_index, final=True,
        )
        if not muts:
            out.append(Verdict(
                claim, "contradicted",
                "marked completed, but nothing was modified in this session",
            ))
            continue
        tokens = set(re.findall(r"[A-Za-z0-9_.\-/]{5,}", content))
        tokens |= set(re.findall(r"[\u4e00-\u9fff]{2,}", content))
        backing = []
        for m in muts:
            hay = m.path or m.cmd
            if any(tok in hay for tok in tokens):
                backing.append(m)
        if backing:
            m = backing[-1]
            out.append(Verdict(
                claim, "verified",
                "receipt: {} (event {})".format(m.describe()[:60], m.index),
            ))
        else:
            out.append(Verdict(
                claim, "unverifiable",
                "marked completed, but no recorded action matches this task",
            ))
    return out


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def audit_session(
    path: str, root: Optional[str] = None, strict: bool = False
) -> AuditResult:
    s = parse_session(path)
    ev = build_evidence(s)
    verdicts: List[Verdict] = []

    for e in s.events:
        if not isinstance(e, TextEvent) or e.role != "assistant":
            continue
        final = e.index == ev.last_assistant_text_index
        for c in extract_claims(e.text, e.index, final):
            if c.hedged:
                verdicts.append(
                    Verdict(c, "hedged", "prediction/plan — not counted")
                )
                continue
            if c.type == "tests_pass":
                verdicts.append(_judge_test_claim(c, ev))
            elif c.type == "ran_tests":
                verdicts.append(_judge_ran_tests(c, ev))
            elif c.type == "lint_clean":
                verdicts.append(_judge_lint_or_build(c, ev, "lint"))
            elif c.type == "build_ok":
                verdicts.append(_judge_lint_or_build(c, ev, "build"))
            elif c.type == "formatted":
                verdicts.append(_judge_formatted(c, ev))
            elif c.type == "fixed":
                verdicts.append(_judge_fixed(c, ev))
            elif c.type == "created_file":
                verdicts.append(_judge_file_claim(c, ev, root, "created"))
            elif c.type == "modified_file":
                verdicts.append(_judge_file_claim(c, ev, root, "modified"))
            elif c.type == "deleted_file":
                verdicts.append(_judge_file_claim(c, ev, root, "deleted"))
            elif c.type == "read_file":
                verdicts.append(_judge_file_claim(c, ev, root, "read"))

    verdicts.extend(_judge_todos(ev))
    return AuditResult(session=s, evidence=ev, verdicts=verdicts, strict=strict)
