"""Python → block compiler. Each test names the failure it is there to catch."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from comet_tpl import dsl, pyc, render, spec as S  # noqa: E402

VARS = [{"varName": n, "mode": m, "type": "string", "isAuto": False}
        for n, m in (("n", "default"), ("이름", "default"), ("유저 입력", "refer"), ("창", "none"))]


def run_value(src: str, **values) -> str:
    node, ret = pyc.PyCompiler(VARS).compile_source(src, "value")
    it = render.Interp({k: str(v) for k, v in values.items()}, ["창"], {"images": {}, "audio": {}},
                       {"now": 0, "mouse": (0, 0), "first": False, "last": True})
    return it.get_value(node, ret)


def num(s: str) -> float:
    return float(s)


def test_python_example_compiles_to_the_same_blocks_as_the_dsl_example():
    # fails if any lowering (macros, @on triggers, slot mapping, constants) drifts from the hand-written DSL template
    def build(d):
        path = os.path.join(ROOT, "examples", d, "template.json")
        payload, rep = S.build_payload(json.load(open(path, encoding="utf-8")), None, os.path.dirname(path))
        assert rep.ok, rep.text()
        raw = payload["rawTemplate"]
        for sp in raw["sprites"]:  # local asset paths differ only by example folder
            if sp.get("imageURL"):
                sp["imageURL"] = os.path.basename(sp["imageURL"])
        return raw
    a, b = build("vn-dialogue"), build("vn-dialogue-py")
    assert a["sprites"] == b["sprites"] and a["triggers"] == b["triggers"] and a["variables"] == b["variables"]


def test_floor_division_and_modulo_follow_python_for_negatives():
    # fails if % truncates toward zero, if 49 × (1/49) = 0.999… floors to 0 uncorrected,
    # or if the site's 2-decimal DIV (1999 / 2000 → 1.0) leaks into a variable divisor
    for k in list(range(-60, 61)) + [-19125, 1999, 4900]:
        for d in (3, 7, 49, 75, -4):
            assert num(run_value(f"n // {d}", n=k)) == k // d, (k, d)
            assert num(run_value(f"n % {d}", n=k)) == k % d, (k, d)
    for k, m in ((1999, 2000), (-1999, 2000), (2000, 2000), (7, -2), (-7, -2), (98, 98)):
        it = render.Interp({"n": str(k), "m": str(m)}, [], {"images": {}, "audio": {}},
                           {"now": 0, "mouse": (0, 0), "first": False, "last": True})
        node, ret = pyc.PyCompiler(VARS + [{"varName": "m", "mode": "default", "type": "string", "isAuto": False}]
                                   ).compile_source("f'{n // m},{n % m}'", "value")
        assert it.get_value(node, ret) == f"{k // m},{k % m}", (k, m)


def test_ternary_min_max_and_chained_compare_lower_to_equivalent_blocks():
    # fails if hoisted temp locals collide or a chain a < n <= b loses its AND
    for k in range(-3, 14):
        assert num(run_value("max(0, min(n, 10))", n=k)) == max(0, min(k, 10))
        assert run_value('"안" if 0 < n <= 5 else "밖"', n=k) == ("안" if 0 < k <= 5 else "밖")
        assert num(run_value("abs(n - 4) * 2", n=k)) == abs(k - 4) * 2


def test_string_plus_becomes_join_but_number_plus_stays_calc():
    # fails if every + becomes CALC ADD: the site returns the left number for 1 + "a", dropping the text
    assert run_value('f"{이름}님 {n}번"', 이름="렌", n=3) == "렌님 3번"
    assert run_value('이름 + "님"', 이름="렌") == "렌님"
    assert num(run_value("n + 1", n=4)) == 5


def test_round_trip_through_the_printer_is_stable():
    # fails if PyPrinter emits text that compiles to different blocks (precedence, f-string escaping, aliases)
    srcs = ['f"{이름}: {유저_입력}"', "round(n * 1.5) if n > 2 else 0", "len(이름) >= 3 and not n == 1"]
    for src in srcs[:2]:
        node, ret = pyc.PyCompiler(VARS).compile_source(src, "value")
        again = pyc.PyPrinter([v["varName"] for v in VARS]).inline(node, ret)
        assert pyc.PyCompiler(VARS).compile_source(again, "value") == (node, ret), again
    node, _ = pyc.PyCompiler(VARS).compile_source(f"if {srcs[2]}:\n    show()\nelse:\n    hide()", "state")
    text = pyc.PyPrinter([v["varName"] for v in VARS]).inline(node)
    assert pyc.PyCompiler(VARS).compile_source(text, "state")[0] == node, text


def test_typo_of_a_template_variable_is_an_error_not_a_silent_local():
    # fails if '이룸 = 1' quietly becomes a local that nothing reads, so the action never changes the variable
    try:
        pyc.PyCompiler(VARS).compile_source("창2 = 1", "action")
    except dsl.ScriptError as e:
        assert "창" in str(e)
        return
    raise AssertionError("typo was accepted")


def test_actions_respect_the_context_whitelist():
    # fails if next() compiles inside a state script, where the server would rewrite it to NULL
    try:
        pyc.PyCompiler(VARS).compile_source("next()", "state")
    except dsl.ScriptError:
        return
    raise AssertionError("next() accepted in state")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
