"""Terminal / JSON / Markdown report rendering."""
from __future__ import annotations

import json
from typing import Optional

from agentpolygraph.audit import AuditResult, Verdict

SYMBOL = {
    "verified": "✅",
    "contradicted": "❌",
    "unverifiable": "⚠️ ",
    "hedged": "⊘",
}
LABEL = {
    "verified": "VERIFIED",
    "contradicted": "CONTRADICTED",
    "unverifiable": "UNVERIFIABLE",
    "hedged": "HEDGED",
}

_C = {"green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
      "dim": "\033[2m", "bold": "\033[1m", "cyan": "\033[36m",
      "reset": "\033[0m"}


class Painter:
    def __init__(self, color: bool):
        self.color = color

    def __call__(self, text: str, *codes: str) -> str:
        if not self.color:
            return text
        return "".join(_C[c] for c in codes) + text + _C["reset"]


def _outcome_word(o: str) -> str:
    return {"pass": "PASS", "fail": "FAIL", "unknown": "?", "none": "none"}.get(
        o, o
    )


def render_report(res: AuditResult, color: bool = True) -> str:
    p = Painter(color)
    s, ev = res.session, res.evidence
    out = []
    out.append("")
    out.append(p("  AgentPolygraph", "bold", "cyan") + p(" — receipts for your AI agent", "bold"))
    sid = (s.session_id or "—")[:8]
    out.append(p("  session   : ", "dim") + "{} · {}".format(
        sid, s.title or "(untitled)"
    ))
    if s.project:
        out.append(p("  project   : ", "dim") + s.project)
    out.append(p("  evidence  : ", "dim") + "{} commands · {} test runs (last: {}) · {} file edits".format(
        ev.n_bash_commands,
        len(ev.test_runs),
        _outcome_word(ev.last_test_outcome()),
        len([m for m in ev.mutations if not m.failed]),
    ))
    out.append("")

    if not res.verdicts:
        out.append(p("  (no checkable claims found in this session)", "dim"))
        out.append("")
        return "\n".join(out)

    out.append(p("  THE AGENT SAID:", "bold"))
    out.append("")
    for v in res.verdicts:
        sym = SYMBOL[v.verdict]
        if v.verdict == "verified":
            sym = p(sym, "green"); lab = p(LABEL["verified"].ljust(15), "green")
        elif v.verdict == "contradicted":
            sym = p(sym, "red"); lab = p(LABEL["contradicted"].ljust(15), "red")
        elif v.verdict == "unverifiable":
            sym = p(sym, "yellow"); lab = p(LABEL["unverifiable"].ljust(15), "yellow")
        else:
            sym = p(sym, "dim"); lab = p(LABEL["hedged"].ljust(15), "dim")
        quote = v.claim.quote
        if len(quote) > 84:
            quote = quote[:81] + "..."
        out.append("  {} {} {}".format(sym, lab, p('"{}"'.format(quote), "bold")))
        note = v.note
        if v.evidence_index >= 0:
            note = note + p("  [event {}]".format(v.evidence_index), "dim")
        out.append(p("      " + note, "dim"))
        out.append("")

    c = res.counts
    out.append(
        p("  verdict: ", "bold")
        + p("{} verified".format(c["verified"]), "green") + " · "
        + p("{} contradicted".format(c["contradicted"]), "red") + " · "
        + p("{} unverifiable".format(c["unverifiable"]), "yellow") + " · "
        + p("{} hedged".format(c["hedged"]), "dim")
    )
    if c["contradicted"]:
        out.append(p("  ❌ the receipts disagree with the agent.", "red", "bold"))
    elif c["unverifiable"]:
        out.append(p("  ⚠️  no lies caught, but some claims have no receipts.", "yellow"))
    else:
        out.append(p("  ✅ every checked claim survived the receipts.", "green"))
    out.append("")
    return "\n".join(out)


def render_json(res: AuditResult) -> str:
    s, ev = res.session, res.evidence
    doc = {
        "tool": "agentpolygraph",
        "version": "0.1.0",
        "session": {
            "path": s.path,
            "session_id": s.session_id,
            "project": s.project,
            "title": s.title,
            "started": s.started,
            "ended": s.ended,
        },
        "stats": {
            "bash_commands": ev.n_bash_commands,
            "test_runs": [
                {"event": r.index, "cmd": r.cmd, "outcome": r.outcome,
                 "passed": r.passed, "failed": r.failed, "scoped": r.scoped}
                for r in ev.test_runs
            ],
            "file_edits": len([m for m in ev.mutations if not m.failed]),
        },
        "claims": [
            {
                "type": v.claim.type,
                "quote": v.claim.quote,
                "verdict": v.verdict,
                "note": v.note,
                "evidence": {
                    "event": v.evidence_index,
                    "command": v.evidence_cmd,
                    "output_line": v.evidence_line,
                },
            }
            for v in res.verdicts
        ],
        "summary": res.counts,
        "exit_code": res.exit_code,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def render_markdown(res: AuditResult) -> str:
    s, ev = res.session, res.evidence
    lines = ["# AgentPolygraph report", ""]
    lines.append("- **Session**: `{}` ({})".format(
        (s.session_id or "—")[:8], s.title or "untitled"))
    if s.project:
        lines.append("- **Project**: `{}`".format(s.project))
    lines.append("- **Evidence**: {} commands · {} test runs (last: {}) · {} file edits".format(
        ev.n_bash_commands, len(ev.test_runs),
        _outcome_word(ev.last_test_outcome()),
        len([m for m in ev.mutations if not m.failed])))
    lines.append("")
    lines.append("| Verdict | Claim | Note |")
    lines.append("|---|---|---|")
    for v in res.verdicts:
        q = v.claim.quote.replace("|", "\\|")
        n = v.note.replace("|", "\\|")
        lines.append("| {} {} | {} | {} |".format(
            SYMBOL[v.verdict], LABEL[v.verdict], q, n))
    lines.append("")
    c = res.counts
    lines.append("**{} verified · {} contradicted · {} unverifiable · {} hedged**".format(
        c["verified"], c["contradicted"], c["unverifiable"], c["hedged"]))
    lines.append("")
    return "\n".join(lines)
