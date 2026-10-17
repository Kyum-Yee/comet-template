"""Offline tests for novel / cinema specs. Run: python3 tests/test_works.py  (or pytest)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from comet_tpl import works as W  # noqa: E402

EX = os.path.join(ROOT, "examples")
LB = [{"title": "t", "content": "c", "useDefault": False, "triggers": [
    {"type": "regex", "action": "apply", "regex": "(a|b)", "watchPrevChat": True, "watchUserChat": False},
    {"type": "dice", "action": "pass", "diceIndex": 6, "from": 1, "to": 3, "remainder": 0}]}]


def test_lorebooks_go_up_in_the_stored_shape_and_come_back():
    # fails if the import form is sent as is: the server answers "응답 형식이 올바르지 않습니다" (seen live)
    st = W.lorebook_to_stored(LB[0])
    rx, dice = st["triggers"]
    assert rx["keyword"] == "(a|b)" and "regex" not in rx and rx["watchUserChat"] is False
    assert all(set(t) == {"type", "action", "keyword", "watchPrevChat", "watchUserChat", "diceIndex", "from", "to",
                          "remainder"} for t in st["triggers"])
    assert dice["diceIndex"] == 6 and dice["keyword"] == ""
    assert W.lorebook_from_stored(st) == LB[0]


def _cinema(**over):
    sp = json.load(open(os.path.join(EX, "cinema-basic", "cinema.json"), encoding="utf-8"))
    sp.update(over)
    return sp


ST = {"templateId": "t1", "variables": [
    {"varName": "유저 입력", "isAuto": True, "mode": "refer", "type": "string"},
    {"varName": "대사", "isAuto": False, "mode": "default", "type": "string"},
    {"varName": "배경", "isAuto": True, "mode": "default", "type": "string"},
    {"varName": "창열림", "isAuto": False, "mode": "none", "type": "string"}],
    "sprites": [{"spriteType": "frame", "assignedValueScript": {"res": {"type": "VAR", "varName": "배경"}}}],
    "imageStorage": [], "audioStorage": [], "triggers": []}


def test_cinema_defaults_follow_template_order_and_catch_wrong_names():
    # fails if defaults are stored in spec order (server: "변수 개수가 일치하지 않습니다") or a typo slips through
    ss = [{"sessionTitle": "s", "initDesc": "", "defaults": {"배경": "도서관_밤", "대사": "안녕"},
           "logs": [["배경", "없는그림"], ["창열림", "true"], ["대삿", "x"]]}]
    p, rep = W.build_cinema(_cinema(startSessions=ss), ST, None, os.path.join(EX, "cinema-basic"))
    assert p["rawCharacter"]["startSessions"][0]["initSession"] == ["", "안녕", "도서관_밤", ""]
    text = rep.text()
    assert "템플릿에 없는 변수 '대삿'" in text and "시스템 변수" in text and "'없는그림'은 등록된 이미지 이름이 아닙니다" in text
    assert "rawCharacter.tags" not in json.dumps(p) and "tags" not in p["rawCharacter"]


def test_novel_image_macros_come_first_in_stored_form():
    # fails if image macros land after a cleanup macro that deletes unknown {{img::…}} codes, or miss prefix/suffix
    base = os.path.join(EX, "novel-basic")
    sp = json.load(open(os.path.join(base, "novel.json"), encoding="utf-8"))
    p, rep = W.build_novel(sp, lambda path, kind, where: "https://image.comet-talk.ai/" + os.path.basename(path), base)
    assert rep.ok, rep.text()
    ms = p["rawNovel"]["macros"]
    assert ms[0] == {"type": "str_str", "from": "{{img::1}}", "to": "![](https://image.comet-talk.ai/1.png)",
                     "prefix": "", "suffix": ""}
    assert all(set(m) == {"type", "from", "to", "prefix", "suffix"} for m in ms)
    sp["startSessions"] = []
    assert not W.build_novel(sp, None, base)[1].ok      # the server would accept zero sessions; the tool must not


def test_diff_ignores_only_the_template_id_the_server_resets():
    sent = {"title": "a", "rawCharacter": {"sessionTemplate": {**ST}, "prompt": "p"}}
    stored = json.loads(json.dumps(sent))
    stored["rawCharacter"]["sessionTemplate"]["templateId"] = "none"
    assert W.diff("cinema", sent, stored) == []
    stored["rawCharacter"]["sessionTemplate"]["sprites"] = []
    assert W.diff("cinema", sent, stored) == ["rawCharacter.sessionTemplate"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
