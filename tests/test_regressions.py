"""Adversarial regression tests: bugs found during self-use (dogfooding).

Each test encodes a real flaw discovered while using the tool on a
recorded real session, to make sure it never comes back.
"""
from agentpolygraph.claims import extract_claims


def find(text, ctype):
    return [c for c in extract_claims(text, 0, True) if c.type == ctype]


def test_fixed_claim_with_dotted_path():
    # bug: "\w+" filler could not cross "calc.py" -> claim was missed
    cs = find("The bug in calc.py is fixed, and I created settings.yaml.", "fixed")
    assert cs and not cs[0].hedged


def test_created_file_bare_filename():
    # bug: path without "/" and without the word "file" was missed
    cs = find("I created settings.yaml with sensible defaults.", "created_file")
    assert cs and cs[0].detail["path"] == "settings.yaml"


def test_version_number_is_not_a_file():
    assert find("I created v1.2 of the parser.", "created_file") == []


def test_modal_inside_match_is_hedged():
    # bug: "tests should pass" was not even matched (nor hedged)
    cs = find("Tests should pass now.", "tests_pass")
    assert cs and cs[0].hedged


def test_future_run_plan_is_hedged():
    cs = find("I will run the tests after this.", "ran_tests")
    assert cs and cs[0].hedged


def test_negation_before_claim_skipped():
    # bug: _is_negated existed but was never called
    assert find("Not all tests pass yet.", "tests_pass") == []


def test_double_counted_stdout():
    # bug: toolUseResult.stdout duplicating tool_result content doubled
    # the parsed "N passed" count -> honest agent falsely contradicted
    from agentpolygraph.evidence import _combined_text
    from agentpolygraph.session import ToolResult
    r = ToolResult(0, "t", False, "5 passed", "5 passed", "", 0)
    text = _combined_text(r)
    assert text.count("passed") == 1


def test_chinese_sentence_split():
    # bug: no whitespace after ！ -> sentences never split
    cs = extract_claims("所有测试已通过！我创建了文件 config/settings.yaml。", 0, True)
    quotes = [c.quote for c in cs]
    assert any("已通过" in q and "创建" not in q for q in quotes)


def test_honest_phrasings_no_false_claims():
    # these must NOT produce claims (avoid accusing honest agents)
    for text in [
        "All green!",
        "Testing complete.",
        "Fixed.",
        "I've committed the changes.",
        "Looks good to me.",
        "The CI pipeline will handle it.",
    ]:
        assert extract_claims(text, 0, True) == [], text
