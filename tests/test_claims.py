"""Unit tests for claim extraction (hedges, negation, CN, counts)."""
from agentpolygraph.claims import extract_claims


def types(text):
    return [c.type for c in extract_claims(text, index=0, final=True)]


def test_plain_tests_pass():
    assert "tests_pass" in types("All tests pass.")


def test_count_claim():
    cs = extract_claims("The suite is green — 5 tests passed.", 0, True)
    c = [x for x in cs if x.type == "tests_pass"][0]
    assert c.detail.get("count") == 5


def test_hedged_prediction_not_counted():
    cs = extract_claims("Tests should pass now.", 0, True)
    assert all(c.hedged for c in cs)
    assert len(cs) >= 1


def test_hedged_future_plan():
    cs = extract_claims("I will run the tests after this.", 0, True)
    assert cs and all(c.hedged for c in cs)


def test_negated_not_counted():
    assert types("Not all tests pass yet.") == []
    assert types("The bug is not fixed.") == []


def test_question_not_counted():
    assert types("Does the build work? All tests pass?") == []


def test_ambiguous_not_counted():
    assert types("All tests pass except test_login.") == []


def test_chinese_claims():
    assert "tests_pass" in types("所有测试已通过！")
    cs = extract_claims("我创建了文件 config/settings.yaml，并修复了该 bug。", 0, True)
    t = [c.type for c in cs]
    assert "created_file" in t
    assert "fixed" in t
    created = [c for c in cs if c.type == "created_file"][0]
    assert created.detail["path"] == "config/settings.yaml"


def test_fixed_with_intermediate_words():
    assert "fixed" in types("I fixed the failing login bug.")
    assert "fixed" in types("The bug in the auth check is fixed.")


def test_created_file_paths():
    cs = extract_claims("I created config/settings.yaml with defaults.", 0, True)
    assert cs[0].detail["path"] == "config/settings.yaml"
    cs = extract_claims("I wrote a new file called src/util.py.", 0, True)
    assert cs[0].detail["path"] == "src/util.py"


def test_ran_tests():
    assert "ran_tests" in types("I ran the tests and everything looks fine.")
    assert "ran_tests" in types("I re-ran the full suite.")
    assert "ran_tests" in types("我先运行一下测试看看情况。")


def test_code_fences_stripped():
    text = "Here is the output:\n```\n5 tests passed\n```\nGreat."
    assert extract_claims(text, 0, True) == []


def test_inline_code_kept():
    # inline ticks are fine; the claim still counts
    assert "tests_pass" in types("All tests pass (`pytest -q`).")


def test_lint_and_build():
    assert "lint_clean" in types("Lint is clean.")
    assert "lint_clean" in types("I ran the linter and it's clean.")
    assert "build_ok" in types("The build succeeds.")
    assert "build_ok" in types("编译通过，构建成功。")


def test_deleted_file():
    cs = extract_claims("I deleted old/config.yaml.", 0, True)
    assert cs[0].type == "deleted_file"
    assert cs[0].detail["path"] == "old/config.yaml"


def test_read_file():
    cs = extract_claims("I've read through tests/test_auth.py carefully.", 0, True)
    assert cs[0].type == "read_file"


def test_todo_style_summary_not_matched():
    # "All done" is not a checkable claim family
    assert types("All done!") == []
