"""Offline tests. Run: python3 -m pytest -q ~/.claude/skills/comet-template/tests  (or python3 this file)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from comet_tpl import dsl, render, spec as S  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "basic_template.json")
EXAMPLE = os.path.join(ROOT, "examples", "vn-dialogue", "template.json")


def raises(fn, needle):
    try:
        fn()
    except dsl.ScriptError as e:
        assert needle in str(e), str(e)
        return
    raise AssertionError(f"no error, wanted {needle!r}")


def test_official_template_survives_pull_and_build():
    # fails if layer order is not reversed, or a default/legacy field (if, func, assignedValue) drifts
    post = json.load(open(FIX, encoding="utf-8"))
    sp = S.payload_to_spec(post)
    sp.update(description="d", prompt="대사 이미지")
    payload, rep = S.build_payload(sp)
    assert rep.ok, rep.text()
    for k in ("sprites", "triggers", "variables"):
        assert payload["rawTemplate"][k] == post["rawTemplate"][k], k


def test_decompile_keeps_needed_parentheses():
    # fails if the right operand of a same-precedence operator loses its parentheses
    for src in ("1 - (2 - 3)", "-$a * (2 + 3) - -4", "(1 - 2) - (3 - 4) .. \"x\"", "$a / ($b * 2)", "sin(timer - ${열린 시간}) * 90"):
        node, ret = dsl.compile_value(src)
        again = dsl.decompile(node, ret)
        assert dsl.compile_value(again) == (node, ret), (src, again)
    cond = "if not ($a == 1 or $b == 2) and is_last:\n    show"
    node, _ = dsl.compile_script(cond, "state")
    assert dsl.compile_script(dsl.decompile(node), "state")[0] == node


def test_elif_becomes_nested_if_else_and_back():
    src = "if $a == 1:\n    x = 1\nelif $a == 2:\n    x = 2\nelse:\n    hide"
    node, _ = dsl.compile_script(src, "state")
    assert node["type"] == "IF_ELSE" and node["else"]["type"] == "IF_ELSE" and node["else"]["cont"]["type"] == "NULL"
    assert dsl.decompile(node) == src


def test_context_rules_match_the_editor_sanitizer():
    raises(lambda: dsl.compile_script("$열림 = true", "state"), "SET_GLOBAL")
    raises(lambda: dsl.compile_script("show", "action"), "SHOW")
    raises(lambda: dsl.compile_script("return 1", "action"), "return")
    raises(lambda: dsl.compile_value("if $a == 1:\n    return 1"), "맨 바깥")
    raises(lambda: dsl.compile_script("if $a:\n    show", "state"), "비교식")


def _spec(**over):
    sp = {"title": "t", "description": "d", "prompt": "대사", "thumbnail": "https://image.comet-talk.ai/x",
          "variables": [{"name": "대사"}, {"name": "열림", "kind": "system"}],
          "layers": [{"type": "text", "value": "$대사"}]}
    sp.update(over)
    return sp


def test_spec_catches_what_the_server_rejects_or_silently_rewrites():
    cases = {
        "시스템 변수만": _spec(layers=[{"type": "image", "src": "https://image.comet-talk.ai/b", "action": "$대사 = 1"}]),
        "없는 변수": _spec(layers=[{"type": "text", "value": "$없음"}]),
        "외부 URL": _spec(layers=[{"type": "image", "src": "https://example.com/a.png"}]),
        "KeyboardEvent.code": _spec(triggers=[{"on": "key:a", "do": "next"}]),
        "전역(global) 변수가 최소": _spec(variables=[{"name": "열림", "kind": "system"}], layers=[]),
        "20자": _spec(title="가" * 21),
    }
    for needle, sp in cases.items():
        _, rep = S.build_payload(sp)
        assert any(needle in e for e in rep.errors), (needle, rep.errors)


def test_js_value_semantics():
    # fails if '+' concatenates strings onto numbers (the site returns the left number) or DIV skips its 2-decimal rounding
    assert render.calc("ADD", 1, "a") == 1
    assert render.calc("ADD", "a", "b") == "ab"
    assert render.calc("DIV", 1, 3) == 0.33
    assert render.calc("MUL", "ab", 2) == "abab"
    assert render.loose_eq("1", 1) and render.loose_eq("", 0) and render.loose_eq(True, 1)
    assert render.relate("10", "9", "LESS")          # both strings: lexical
    assert not render.relate(10, 9, "LESS")
    assert render.coerce("true") is True and render.coerce("007") == 7


def test_modal_clicks_route_like_the_site():
    # fails if hit testing ignores layer order, visibility, or empty actions
    pytest_skip_without_pil()
    sp = json.load(open(EXAMPLE, encoding="utf-8"))
    payload, rep = S.build_payload(sp, None, os.path.dirname(EXAMPLE))
    assert rep.ok, rep.text()
    raw = payload["rawTemplate"]
    n = len(raw["sprites"])
    idx = {s["objectName"]: i for i, s in enumerate(raw["sprites"])}
    open_ = ["load", f"click:{idx['상태 버튼']}"]
    res = render.render(raw, {}, events=open_ + [f"click:{idx['상태 내용']}"], after=1000)
    assert res["values"]["창열림"] == "true"            # window body absorbs the click
    res = render.render(raw, {}, events=open_ + ["tap:640,650"], after=1000)
    assert res["values"]["창열림"] == "false"           # backdrop closes it
    res = render.render(raw, {}, events=["load", "tap:640,300"])
    assert "next" in res["events"][-1][1]              # closed: the full-screen tap zone advances
    assert n == len(sp["layers"])


def pytest_skip_without_pil():
    try:
        import PIL  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("Pillow 없음")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
