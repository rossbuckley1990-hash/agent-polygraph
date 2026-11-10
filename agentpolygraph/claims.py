"""Claim extraction: find checkable statements in agent messages.

A "claim" is something the agent asserted about its own work:
  - "All tests pass"                       -> tests_pass
  - "I ran the tests"                      -> ran_tests
  - "Lint is clean" / "Build succeeds"     -> lint_clean / build_ok
  - "The bug is fixed"                     -> fixed
  - "I created config/settings.yaml"       -> created_file
  - "I updated README.md"                  -> modified_file
  - "I deleted old/config.yaml"            -> deleted_file
  - "I've read tests/test_auth.py"         -> read_file
  - "I formatted the code"                 -> formatted

Hedges ("tests should pass now"), questions ("does it pass?") and
negations ("not all tests pass yet") are detected and skipped or
downgraded so the tool does not accuse an honest agent.

Patterns cover both English and Chinese phrasing (中文).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# --------------------------------------------------------------------------
# Claim dataclass
# --------------------------------------------------------------------------


@dataclass
class Claim:
    type: str                 # family key, see FAMILIES
    quote: str                # the sentence containing the claim
    index: int                # event index of the assistant text
    final: bool = False       # from the last assistant message
    hedged: bool = False
    detail: dict = field(default_factory=dict)  # path=..., count=..., all=True

    @property
    def label(self) -> str:
        return self.detail.get("path") or self.type


# --------------------------------------------------------------------------
# Hedge / negation / question guards
# --------------------------------------------------------------------------

HEDGE_BEFORE = re.compile(
    r"\b(?:should|would|will|might|may|could|hopefully|expect(?:ed|ing)?|"
    r"supposed|suppose|plan(?:ning|ned)?|intend(?:ing|ed)?|about to|"
    r"going to|let'?s|let me|want to|need to|try(?:ing)? to|'ll|shall|"
    r"next step|about|soon)\W[^.\n]{0,48}$",
    re.I,
)
HEDGE_BEFORE_CN = re.compile(
    r"(?:应该|应当|将会|会通过|希望能|希望|计划|打算|接下来|即将|大概|可能|"
    r"准备|待会|一会儿|之后会)"
)
NEG_BEFORE = re.compile(
    r"\b(?:not|never|no longer|cannot|can't|won't|didn't|don't|wasn't|"
    r"isn't|aren't|n't)\b[^.\n]{0,36}$",
    re.I,
)
NEG_BEFORE_CN = re.compile(r"(?:没有|未|不再|无法|没能)")

QUESTION_END = ("?", "？")
FENCE = re.compile(r"```[\s\S]*?```")
PATH_RE = r"(?P<path>[\w./\\-]+\.[A-Za-z][A-Za-z0-9]{0,9})"

# modals INSIDE a match ("tests should pass") make it a prediction
_MODAL_IN_MATCH = re.compile(
    r"\b(?:should|would|will|might|may|could|hopefully|plan(?:ning)?|"
    r"intend)\b|应该|将会|会|希望", re.I
)


def _is_hedged(sentence: str, pos: int) -> bool:
    seg = sentence[max(0, pos - 64):pos]
    return bool(HEDGE_BEFORE.search(seg) or HEDGE_BEFORE_CN.search(seg))


def _is_negated(sentence: str, pos: int) -> bool:
    seg = sentence[max(0, pos - 56):pos]
    return bool(NEG_BEFORE.search(seg) or NEG_BEFORE_CN.search(seg))


def _is_question(sentence: str) -> bool:
    return sentence.rstrip().endswith(QUESTION_END)


def _split_sentences(text: str) -> List[str]:
    # Break after . ! ? 。！？ when followed by whitespace, a capital
    # letter or a CJK character (Chinese needs no space after punctuation).
    raw = re.split(
        r"(?<=[.!?。！？])(?=\s|[A-Z\u4e00-\u9fff])", text
    )
    out = []
    for s in raw:
        s = s.strip(" \t-*•>·–")
        if not s or len(s) < 5:
            continue
        if s.startswith("#"):  # markdown heading
            continue
        out.append(s)
    return out


# --------------------------------------------------------------------------
# Claim pattern families
# --------------------------------------------------------------------------

FAMILIES = [
    (
        "tests_pass",
        [
            r"\b(?P<count>\d+)\s+tests?\s+passed\b",
            r"\btests?\s+(?:are\s+|were\s+|now\s+|all\s+|already\s+|"
            r"currently\s+|should\s+|would\s+|will\s+|might\s+|may\s+)*"
            r"(?:pass(?:ing|ed)?|green|successful)\b",
            r"\b(?:all|every)\s+(?:\d+\s+)?tests?\s+(?:now\s+)?"
            r"(?:pass|passed|passing|green)\b",
            r"\btest\s+suite\s+(?:is|was)\s+(?:now\s+)?"
            r"(?:passing|passed|green|successful)\b",
            r"\bpassed\s+all\s+tests\b",
            r"(?:所有|全部)\s*测试[^。！？\n]{0,10}通过",
            r"测试(?:套件)?(?:已|都|全部|现在|均|会)+通过",
            r"(?P<count>\d+)\s*个?测试(?:全部)?通过",
            r"通过了?(?:所有|全部)测试",
        ],
    ),
    (
        "ran_tests",
        [
            r"\b(?:just\s+|already\s+|then\s+)?"
            r"(?:have\s+)?(?:ran|run|executed|re-?ran)\s+"
            r"(?:the\s+|all\s+|your\s+|my\s+|our\s+|full\s+|entire\s+|complete\s+)*"
            r"(?:unit\s+|integration\s+)?test(?:s| suite| run)?\b",
            r"\b(?:ran|running)\s+(?:the\s+)?(?:full\s+)?test\s+suite\b",
            r"\b(?:re-?ran|rerun|ran|run)\s+(?:the\s+)?"
            r"(?:full\s+|entire\s+|complete\s+)?suite\b",
            r"(?:运行|跑|执行)了?(?:了|一下|所有|全部|完整)?测试",
        ],
    ),
    (
        "lint_clean",
        [
            r"\blint(?:ing|er)?\b[^.\n]{0,28}?\b(?:clean|clear|passes|passed|"
            r"error[- ]?free|no issues|happy)\b",
            r"\bno\s+lint(?:ing)?\s+(?:errors|issues|problems|warnings)\b",
            r"\b(?:ruff|eslint|flake8|pylint|mypy|prettier|biome|"
            r"clang[- ]format|gofmt|golangci)\b[^.\n]{0,36}?\b"
            r"(?:clean|no issues|passes|passed|found nothing|reports? no)\b",
            r"lint(?:检查)?(?:已|都|现在)?通过",
            r"没有\s*lint\s*(?:错误|问题)",
        ],
    ),
    (
        "build_ok",
        [
            r"\b(?:the\s+)?build\s+(?:is\s+|was\s+|now\s+|has\s+)*"
            r"(?:succeed(?:s|ed)?|successful|pass(?:es|ed)?|green|works)\b",
            r"\bcompiles?\s+(?:successfully|cleanly|fine)?\b",
            r"编译通过|构建成功",
        ],
    ),
    (
        "fixed",
        [
            r"\b(?:fix(?:ed|es)?|resolv(?:ed|es)?|patch(?:ed|es)?|"
            r"correct(?:ed|s)?)\s+(?:the\s+|a\s+|an\s+|this\s+|that\s+|it\s+)?"
            r"(?:\S+\s+){0,2}"
            r"(?:bug|error|issue|problem|failure|test|regression|crash|typo)\b",
            r"\b(?:the\s+)?(?:bug|error|issue|problem|failure|regression)\s+"
            r"(?:\S+\s+){0,4}"
            r"(?:is|was|is\s+now|has\s+been)\s+"
            r"(?:fixed|resolved|patched|corrected|gone)\b",
            r"(?:已|已经)?(?:修复|解决)了?(?:这个|该|一个)?"
            r"(?:bug|错误|问题|故障|缺陷|失败|测试)?",
            r"(?:bug|错误|问题|故障)(?:已|现在)?(?:被)?(?:修复|解决)",
        ],
    ),
    (
        "formatted",
        [
            r"\b(?:i'?ve\s+)?(?:formatted|re-?formatted)\s+"
            r"(?:the\s+|all\s+|everything|it|the\s+code|both)\b",
            r"\bran\s+(?:the\s+)?formatter\b",
            r"\b(?:black|prettier|gofmt|ruff\s+format|clang-format|isort)\s+"
            r"(?:has\s+been\s+)?(?:run|applied|used)\b",
            r"已(?:经)?格式化|格式化(?:了|完成)",
        ],
    ),
    (
        "created_file",
        [
            r"\b(?:created|added|wrote|made|generated)\s+"
            r"(?:a\s+|an\s+|the\s+|new\s+|two\s+|\d+\s+)*(?:new\s+)?"
            r"(?:file|files|module|script)s?\s+(?:called\s+|named\s+)?"
            r"[`\"''\"]?" + PATH_RE + r"[`\"''\"]?",
            r"\b(?:created|added|wrote|generated)\s+[`\"']" + PATH_RE + r"[`\"']",
            r"\b(?:created|added|wrote|generated)\s+"
            r"(?:a\s+|the\s+|new\s+)?"
            r"(?P<path>[\w./\\-]+\.[A-Za-z][A-Za-z0-9]{0,9})\b",
            r"\b(?:created|added|wrote|generated)\s+"
            r"(?P<path>[\w./\\-]+/[\w./\\-]+\.[A-Za-z0-9]{1,10})\b",
            r"创建(?:了|一个|了一个)?(?:新)?(?:文件|脚本)\s*[`\"':：]?\s*" + PATH_RE,
            r"新增(?:了|一个)?(?:文件|脚本)\s*[`\"':：]?\s*" + PATH_RE,
        ],
    ),
    (
        "modified_file",
        [
            r"\b(?:updated|modified|edited|changed|adjusted|tweaked)\s+"
            r"(?:the\s+|a\s+|both\s+)?[`\"']?" + PATH_RE + r"[`\"']?",
            r"\bmade\s+(?:an?\s+)?edits?\s+to\s+[`\"']?" + PATH_RE,
            r"(?:更新|修改)了?(?:这个|该|一下)?(?:文件)?\s*[`\"':：]?\s*" + PATH_RE,
        ],
    ),
    (
        "deleted_file",
        [
            r"\b(?:deleted|removed)\s+(?:the\s+|a\s+)?[`\"']?" + PATH_RE + r"[`\"']?",
            r"删除了?(?:这个|该|文件)?\s*[`\"':：]?\s*" + PATH_RE,
        ],
    ),
    (
        "read_file",
        [
            r"\b(?:read|reviewed|looked\s+at|checked|inspected)\s+"
            r"(?:through\s+|the\s+|your\s+)?[`\"']?" + PATH_RE + r"[`\"']?",
            r"(?:阅读|看了|查看了|检查了)(?:一下|过)?(?:这个|该)?(?:文件)?\s*"
            r"[`\"':：]?\s*" + PATH_RE,
        ],
    ),
]

_FAMILY_COMPILED = [
    (name, [re.compile(p, re.I) for p in pats]) for name, pats in FAMILIES
]

_ALL_WORDS = re.compile(r"\b(?:all|every)\b|所有|全部", re.I)
_AMBIG = re.compile(
    r"\b(?:except|excluding|but not|apart from|aside from)\b|除了", re.I
)
_IN_MATCH_NEG = re.compile(r"\b(?:not|never|n't|cannot)\b|\b没有|\b未(?!来)", re.I)

MAX_CLAIMS = 200


def extract_claims(text: str, index: int, final: bool = False) -> List[Claim]:
    """Extract claims from one assistant text message."""
    text = FENCE.sub(" ", text)
    claims: List[Claim] = []
    seen = set()
    for sentence in _split_sentences(text):
        if _is_question(sentence):
            continue
        if _AMBIG.search(sentence):
            continue  # "tests pass except test_foo" — not a clean claim
        for name, patterns in _FAMILY_COMPILED:
            for pat in patterns:
                m = pat.search(sentence)
                if not m:
                    continue
                if _IN_MATCH_NEG.search(m.group(0)):
                    continue  # "the bug is not fixed" — a confession, not a claim
                if _is_negated(sentence, m.start()):
                    continue  # "not all tests pass" — negated, not a claim
                key = (name, sentence)
                if key in seen:
                    break
                seen.add(key)
                detail: dict = {}
                if m.groupdict().get("path"):
                    detail["path"] = m.group("path").strip("`\"' ").rstrip(".,;:!?")
                if m.groupdict().get("count"):
                    try:
                        detail["count"] = int(m.group("count"))
                    except (TypeError, ValueError):
                        pass
                detail["all"] = bool(_ALL_WORDS.search(sentence))
                claims.append(
                    Claim(
                        type=name,
                        quote=sentence[:110],
                        index=index,
                        final=final,
                        hedged=(
                            _is_hedged(sentence, m.start())
                            or bool(_MODAL_IN_MATCH.search(m.group(0)))
                        ),
                        detail=detail,
                    )
                )
                break  # one claim per family per sentence
        if len(claims) >= MAX_CLAIMS:
            break
    return claims
