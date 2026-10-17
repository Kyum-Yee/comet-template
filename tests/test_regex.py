"""Regex toolkit against the site's own rules. Node-backed tests skip themselves without node."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from comet_tpl import regex as R  # noqa: E402

# safe-regex 2.1.1 verdicts, recorded with node (pattern, safe-regex says safe, V8 compiles)
REFERENCE = [
    [r"\{\{img::(.*?)/(.*?)\}\}", True, True], ["(a+)+", False, True], [r"(\d+)?", False, True],
    ["(ab?)+", False, True], ["a{2}b{3}", True, True], ["{{img::(.*?)}}", True, True], ["(?:a|b)*c", True, True],
    ["(?=a+)*b", True, True], ["(?<=a+)b", True, True], ["[(+*]+", True, True], [r"\(a+\)+", True, True],
    ["(x?){26}", False, True], ["a?" * 25, True, True], ["a?" * 26, False, True], ["((a)|b)*", True, True],
    ["(a|b+)*", False, True], ["a{2,}{3}", False, False], ["[]a]+", True, True], [r"(?<n>x+)\k<n>+", True, True],
    ["a{,5}+", True, True], ["x(?:y(?:z+))+", False, True], ["(?:)*", True, True], ["a|b*|c+", True, True],
    [r"[\B]a+(b+)+", True, True], ["[z-a]", False, False], [r"(?<m>a)[\k<m>]", False, False],
]


def test_safety_port_matches_safe_regex_2_1_1():
    # fails if nesting is not carried through groups, the 25-repetition cap is off by one,
    # or the parser-failure pass-through ((?=…)*, \B in a class) is treated as unsafe
    for p, safe, ok in REFERENCE:
        st = R.analyze(p)
        assert st.ok == ok and st.safe == safe, (p, st)


def test_int_range_is_exact_and_longest_alternative_first():
    # fails if a range splits wrongly at a digit boundary, or '1' is listed before '10' so an unanchored match stops early
    for lo, hi in ((0, 0), (1, 10), (5, 123), (99, 1000), (37, 4210)):
        rx = re.compile(R.int_range(lo, hi))
        assert all(bool(rx.fullmatch(str(k))) == (lo <= k <= hi) for k in range(0, hi + 200)), (lo, hi)
        assert re.search(R.int_range(lo, hi) + "/", f"{hi}/").group(1) == str(hi)
        assert R.analyze(R.int_range(lo, hi)).repetitions == 0


def test_macro_file_check_rejects_what_the_site_refuses():
    # fails if an unsafe regex or a missing field slips through to an upload the server rejects
    errs, _ = R.check_macros([{"type": "regex_str", "from": "(a+)+", "to": "x"}])
    assert any("위험" in e for e in errs)
    errs, _ = R.check_macros([{"type": "regex_regex", "from": "a", "to": "x"}])
    assert any("prefix" in e for e in errs)
    errs, warns = R.check_macros([{"type": "regex_str", "from": "(a)", "to": r"\1"}])
    assert not errs and any("$1" in w for w in warns)


def test_lorebook_check_needs_a_watch_flag_and_a_valid_regex():
    # fails if a trigger watching neither chat, or an uncompilable regex, passes
    lb = [{"title": "t", "content": "c", "useDefault": False, "triggers": [
        {"type": "regex", "action": "apply", "regex": "(", "watchPrevChat": False, "watchUserChat": False}]}]
    errs, _ = R.check_lorebooks(lb)
    assert any("watch" in e for e in errs) and any("정규식" in e for e in errs)


def _node():
    if not R.node_bin():
        import pytest
        pytest.skip("node 없음")


def test_regex_chain_consumes_its_receiver_like_the_site():
    # fails if the receiver (the macro right after the chain) also runs over the whole text: it only sees each capture,
    # and the main pass resumes after it
    _node()
    ms = [{"type": "regex_regex", "from": r"<b>(.*?)</b>", "prefix": "[", "suffix": "]"},
          {"type": "str_str", "from": "a", "to": "A"},
          {"type": "str_str", "from": "x", "to": "X"}]
    out = R.run_macros("a <b>a x</b> x", ms)
    assert out["result"] == "a [A X] X", out


def test_unsafe_regex_macro_is_skipped_at_render():
    # fails if the runner applies a macro the site's safety check would silently skip
    _node()
    out = R.run_macros("aaa", [{"type": "regex_str", "from": "(a+)+", "to": "b"}])
    assert out["result"] == "aaa"


def test_user_particle_fix_reproduces_site_quirks():
    # fails if the {user} port drifts from the site: 받침 이름 + '로' eats the next character
    _node()
    assert R.run_macros("{user}가 {user}로 가자", [], user="민준")["result"] == "민준이 민준으로가자"
    assert R.run_macros("{user}가 {user}로 가자", [], user="철수")["result"] == "철수가 철수로 가자"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
