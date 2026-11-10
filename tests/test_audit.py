"""End-to-end audits over the bundled example sessions."""
import os

import pytest

from agentpolygraph.audit import audit_session

EX = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "agentpolygraph", "examples",
)


def audit(name, **kw):
    return audit_session(os.path.join(EX, name), **kw)


def counts(res):
    return res.counts


def test_the_liar_is_caught():
    res = audit("the_liar.jsonl")
    c = counts(res)
    assert res.exit_code == 1
    assert c["contradicted"] == 2
    assert c["verified"] == 1
    assert c["unverifiable"] == 1
    types_contradicted = [
        v.claim.type for v in res.verdicts if v.verdict == "contradicted"
    ]
    assert "tests_pass" in types_contradicted
    assert "created_file" in types_contradicted
    # the failing evidence is quoted
    tv = [v for v in res.verdicts
          if v.claim.type == "tests_pass" and v.verdict == "contradicted"][0]
    assert "2 failed" in tv.note


def test_the_honest_agent_clears():
    res = audit("the_honest.jsonl")
    c = counts(res)
    assert res.exit_code == 0
    assert c["contradicted"] == 0
    assert c["verified"] >= 4
    # the "should be more robust now" hedge is not counted against it
    assert c["hedged"] >= 1


def test_chinese_liar_is_caught():
    res = audit("cn_liar.jsonl")
    assert res.exit_code == 1
    c = counts(res)
    assert c["contradicted"] >= 2
    types_contradicted = {
        v.claim.type for v in res.verdicts if v.verdict == "contradicted"
    }
    assert "tests_pass" in types_contradicted
    assert "created_file" in types_contradicted


def test_edge_cases():
    res = audit("edge_cases.jsonl")
    assert res.exit_code == 1
    c = counts(res)
    # lint lie caught
    lint = [v for v in res.verdicts if v.claim.type == "lint_clean"]
    assert lint and lint[0].verdict == "contradicted"
    # formatter lie caught
    fmt = [v for v in res.verdicts if v.claim.type == "formatted"]
    assert fmt and fmt[0].verdict == "contradicted"
    # scoped run is not "all tests"
    scoped = [
        v for v in res.verdicts
        if v.claim.type == "tests_pass" and "scoped" in v.note
    ]
    assert scoped and scoped[0].verdict == "unverifiable"
    # stale pass: tests passed then files edited without a re-run
    stale = [
        v for v in res.verdicts
        if v.claim.type == "tests_pass" and "never re-run" in v.note
    ]
    assert stale and stale[0].verdict == "unverifiable"
    # negated sentence ("Not all tests pass yet") must NOT appear
    assert not [
        v for v in res.verdicts if "Not all tests pass" in v.claim.quote
    ]
    # hedges are marked, not counted
    assert c["hedged"] >= 2
    # todos
    todo = {v.claim.quote: v.verdict for v in res.verdicts
            if v.claim.type == "todo_completed"}
    assert todo.get("[todo] fix the bug in src/util.py") == "verified"
    assert todo.get("[todo] add integration tests") == "unverifiable"


def test_strict_mode_fails_on_unverifiable():
    res = audit("edge_cases.jsonl", strict=True)
    assert res.exit_code == 1
    res_honest = audit("the_honest.jsonl", strict=True)
    assert res_honest.exit_code == 0


def test_root_live_check():
    # point --root at a dir where the "created" file does exist
    root = os.path.join(EX, "..", "..")
    res = audit("the_liar.jsonl", root=root)
    created = [
        v for v in res.verdicts
        if v.claim.type == "created_file" and v.verdict == "contradicted"
    ]
    # config/settings.yaml does not exist in the repo -> still contradicted
    assert created


def test_json_report_shape():
    from agentpolygraph.report import render_json
    import json
    doc = json.loads(render_json(audit("the_liar.jsonl")))
    assert doc["summary"]["contradicted"] == 2
    assert doc["exit_code"] == 1
    assert any(c["verdict"] == "contradicted" for c in doc["claims"])
