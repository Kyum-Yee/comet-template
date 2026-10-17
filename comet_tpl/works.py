"""Novel-chat and cinema-chat specs (agent-facing JSON) <-> upload payloads, plus validation.

The platform calls a cinema chat a "character": POST /upload/newcharacter, body.rawCharacter.
A cinema chat stores a full copy of its template (`sessionTemplate`) taken when the template was
picked, so a later template upload does not reach it until the template is picked again.
Limits and pro-mode thresholds are the editor's own (BuildNovelView / BuildCinemaView, 2026-10).
"""
from __future__ import annotations

import json
import os
import re

from . import regex as R
from .spec import Report, _is_url, js_len

SITE = "https://comet-talk.ai"
KINDS = {
    "novel": dict(label="노벨챗", raw="rawNovel", new="/upload/newnovel", edit="/upload/novel/{id}",
                  post="/get/postNovel/{id}", post_key="postNovel", mine="/get/mynovel", mine_key="mynovels",
                  id_key="novelId", delete="/delete/novel/{id}", route="novel"),
    "cinema": dict(label="시네마챗", raw="rawCharacter", new="/upload/newcharacter", edit="/upload/character/{id}",
                   post="/get/postCharacter/{id}", post_key="postCharacter", mine="/get/mycharacter",
                   mine_key="mycharacters", id_key="characterId", delete="/delete/character/{id}", route="cinema"),
}
VISIBILITY = ["public", "link", "private"]
DEFAULT_MODEL = "gemini-2.5-pro"   # the editor's factory default; `upload` checks it against /get/modeldata
# basic-mode prompt pool (non-space chars, shared with the lorebook budget) and the pro-mode ceiling
POOL = {"novel": 12500, "cinema": 7500}
PRO_PROMPT = {"novel": 250000, "cinema": 75000}
BUDGET_MAX = {"novel": 7500, "cinema": 5000}
COMMON_KEYS = {"kind", "title", "description", "visibility", "thumbnail", "tags", "defaultModel", "prompt",
               "lorebookBudget", "startSessions", "lorebooks", "images", "note"}
KIND_KEYS = {"novel": {"allowLog", "playGuide", "macros"}, "cinema": {"template"}}
FILE_REF = re.compile(r"^@[^\s@][^\n]*\.(md|txt|json)$")


def links(kind: str, cid: str) -> dict:
    r = KINDS[kind]["route"]
    return {"edit": f"{SITE}/build/{r}/{cid}", "detail": f"{SITE}/detail/{r}/{cid}"}


def nonspace(s: str) -> int:
    return len(re.sub(r"\s", "", s))


def load_ref(value, base_dir: str, where: str, rep: Report, want_json=False):
    """'@file.md' / '@file.json' -> file contents (JSON parsed when wanted). Other values pass through."""
    if isinstance(value, str) and FILE_REF.match(value):
        path = os.path.join(base_dir, value[1:])
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            rep.err(where, f"파일이 없습니다: {value[1:]}")
            return [] if want_json else ""
        if not want_json:
            return text
        try:
            return json.loads(text)
        except ValueError as e:
            rep.err(where, f"{value[1:]} JSON 문법 오류: {e}")
            return []
    return value


# ---------------------------------------------------------------- shared fields

def _common(spec: dict, kind: str, rep: Report, resolve, base_dir: str) -> dict:
    unknown = set(spec) - COMMON_KEYS - KIND_KEYS[kind]
    if unknown:
        rep.warn("spec", f"모르는 키 {sorted(unknown)}는 무시합니다")
    title = spec.get("title", "")
    if not isinstance(title, str) or not title.strip() or js_len(title) > 20:
        rep.err("title", "1~20자")
    desc = load_ref(spec.get("description", ""), base_dir, "description", rep)
    if not isinstance(desc, str) or not desc.strip() or js_len(desc) > 2500:
        rep.err("description", "1~2500자")
    vis = spec.get("visibility", "private")
    if vis not in VISIBILITY:
        rep.err("visibility", f"public|link|private ('{vis}')")
    tags = spec.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 10 or any(not isinstance(t, str) or not t or js_len(t) > 10 for t in tags):
        rep.err("tags", "키워드는 10개까지, 하나에 1~10자")
        tags = []
    if len(set(tags)) != len(tags):
        rep.err("tags", "같은 키워드가 두 번 있습니다")
    model = spec.get("defaultModel", DEFAULT_MODEL)
    if not isinstance(model, str) or not model:
        rep.err("defaultModel", "모델 ID 문자열(`comet models`로 목록)")
    thumb = spec.get("thumbnail", "")
    if not thumb:
        rep.err("thumbnail", "필수입니다(로컬 파일 또는 image.comet-talk.ai URL)")
    else:
        thumb = _media(thumb, "thumbnail", "thumbnail", rep, resolve, base_dir)
    return {"thumbnail": thumb, "visibility": vis, "title": title, "description": desc, "tags": list(tags),
            "defaultModel": model}


def _media(p: str, kind: str, where: str, rep: Report, resolve, base_dir: str) -> str:
    if _is_url(p):
        if not p.startswith("https://image.comet-talk.ai/"):
            rep.err(where, f"외부 URL은 서버가 받지 않습니다. 로컬 파일을 주면 도구가 올립니다: {p}")
        return resolve(p, kind, where)
    lp = os.path.join(base_dir, p)
    if not os.path.isfile(lp):
        rep.err(where, f"파일이 없습니다: {p}")
        return p
    if os.path.getsize(lp) > 5 * 1024 * 1024:
        rep.err(where, f"5MB 초과: {p}")
    return resolve(lp, kind, where)


def _budget(spec: dict, kind: str, rep: Report) -> int:
    b = spec.get("lorebookBudget", 2500)
    if not isinstance(b, int) or isinstance(b, bool) or not 0 <= b <= BUDGET_MAX[kind] or b % 250:
        rep.err("lorebookBudget", f"0~{BUDGET_MAX[kind]}, 250 단위 ({b!r})")
        return 2500
    return b


def _prompt(spec: dict, kind: str, budget: int, rep: Report, base_dir: str, pro: list) -> str:
    p = load_ref(spec.get("prompt", ""), base_dir, "prompt", rep)
    if not isinstance(p, str) or not p.strip():
        rep.err("prompt", "비어 있습니다")
        return p if isinstance(p, str) else ""
    pool = POOL[kind]
    if nonspace(p) + budget > pool or len(p) + 1.5 * budget > pool * 1.5:
        pro.append(f"프롬프트 공백 제외 {nonspace(p)}자 + 로어북 예산 {budget} > {pool}")
    if nonspace(p) > PRO_PROMPT[kind]:
        rep.err("prompt", f"공백 제외 {nonspace(p)}자 > 프로 모드 한도 {PRO_PROMPT[kind]}")
    return p


def _lorebooks(spec: dict, budget: int, rep: Report, base_dir: str, pro: list) -> list:
    data = load_ref(spec.get("lorebooks", []), base_dir, "lorebooks", rep, want_json=True)
    if data == []:
        return []
    errs, warns = R.check_lorebooks(data)
    for e in errs:
        rep.err("lorebooks", e)
    for w in warns:
        rep.warn("lorebooks", w)
    if errs:
        return data if isinstance(data, list) else []
    for i, lb in enumerate(data):
        c = lb["content"]
        if nonspace(c) > budget or len(c) > 1.5 * budget:
            pro.append(f"로어북[{i}] '{lb['title']}' 공백 제외 {nonspace(c)}자 > 예산 {budget}")
    return [lorebook_to_stored(lb) for lb in data]


# Import JSON (what people write and what `lorebook check` reads) differs from the stored form:
# every stored trigger carries all nine fields, and a regex trigger keeps its pattern in `keyword`.
# This mirrors the editor's own import converter.
_TRIGGER_FILL = {"keyword": "", "watchPrevChat": True, "watchUserChat": True, "diceIndex": 20, "from": 1, "to": 5,
                 "remainder": 0}


def lorebook_to_stored(lb: dict) -> dict:
    out = []
    for t in lb["triggers"]:
        st = {"type": t["type"], "action": t["action"], **_TRIGGER_FILL}
        if t["type"] in ("keyword", "regex"):
            st.update(keyword=t[t["type"]], watchPrevChat=bool(t.get("watchPrevChat")),
                      watchUserChat=bool(t.get("watchUserChat")))
        else:
            st.update({k: t[k] for k in ("from", "to", "remainder")})
            if t["type"] == "dice":
                st["diceIndex"] = t["diceIndex"]
        out.append(st)
    return {"title": lb["title"], "content": lb["content"], "triggers": out, "useDefault": lb["useDefault"]}


def lorebook_from_stored(lb: dict) -> dict:
    out = []
    for t in lb.get("triggers", []):
        typ = t.get("type")
        it = {"type": typ, "action": t.get("action")}
        if typ in ("keyword", "regex"):
            it.update({typ: t.get("keyword", ""), "watchPrevChat": t.get("watchPrevChat", True),
                       "watchUserChat": t.get("watchUserChat", True)})
        else:
            if typ == "dice":
                it["diceIndex"] = t.get("diceIndex", 20)
            it.update({k: t.get(k) for k in ("from", "to", "remainder")})
        out.append(it)
    return {"title": lb.get("title", ""), "content": lb.get("content", ""), "useDefault": lb.get("useDefault", False),
            "triggers": out}


def _session_text(s: dict, key: str, where: str, rep: Report, base_dir: str, limit_ns: int | None, limit_len: int,
                  pro: list, pro_limit: int) -> str:
    v = load_ref(s.get(key, ""), base_dir, f"{where}.{key}", rep)
    if not isinstance(v, str):
        rep.err(f"{where}.{key}", "문자열")
        return ""
    over = (limit_ns is not None and nonspace(v) > limit_ns) or len(v) > limit_len
    if over:
        pro.append(f"{where}.{key} {len(v)}자(공백 제외 {nonspace(v)}) > 기본 모드 한도")
    if len(v) > pro_limit:
        rep.err(f"{where}.{key}", f"{len(v)}자 > 프로 모드 한도 {pro_limit}")
    return v


def _sessions(spec: dict, rep: Report) -> list:
    ss = spec.get("startSessions")
    if not isinstance(ss, list) or not 1 <= len(ss) <= 10:
        rep.err("startSessions", "시작 세션 1~10개")
        return []
    for i, s in enumerate(ss):
        t = s.get("sessionTitle", "") if isinstance(s, dict) else ""
        if not isinstance(t, str) or not t.strip() or js_len(t) > 15:
            rep.err(f"startSessions[{i}].sessionTitle", "1~15자")
    return ss


def _finish(rep: Report, pro: list):
    if pro:
        rep.warn("프로 모드", "기본 모드 한도를 넘어 이 작품은 프로 모드 전용이 됩니다: " + "; ".join(pro))


# ---------------------------------------------------------------- novel

def build_novel(spec: dict, resolve=None, base_dir: str = ".") -> tuple[dict, Report]:
    rep, pro = Report(), []
    resolve = resolve or (lambda p, kind, where: p)
    post = _common(spec, "novel", rep, resolve, base_dir)
    budget = _budget(spec, "novel", rep)
    prompt = _prompt(spec, "novel", budget, rep, base_dir, pro)
    sessions = []
    for i, s in enumerate(_sessions(spec, rep)):
        if not isinstance(s, dict):
            continue
        where = f"startSessions[{i}]"
        unknown = set(s) - {"sessionTitle", "initDesc", "initChat"}
        if unknown:
            rep.warn(where, f"모르는 키 {sorted(unknown)}(노벨챗 시작 세션은 sessionTitle·initDesc·initChat)")
        sessions.append({
            "sessionTitle": s.get("sessionTitle", ""),
            "initChat": _session_text(s, "initChat", where, rep, base_dir, None, 1000, pro, 10000),
            "initDesc": _session_text(s, "initDesc", where, rep, base_dir, 1000, 1500, pro, 15000),
        })
        if "{user}" in sessions[-1]["initChat"] and re.search(r"\{user\}\s*[:：]", sessions[-1]["initChat"]):
            rep.warn(f"{where}.initChat", "{user}의 대사를 시작 대화에 쓰면 AI가 유저를 사칭하기 쉽습니다")
    macros = _novel_macros(spec, rep, resolve, base_dir)
    guide = load_ref(spec.get("playGuide", ""), base_dir, "playGuide", rep)
    if not isinstance(guide, str) or len(guide) > 2000:
        rep.err("playGuide", "2000자 이하 문자열")
    allow = spec.get("allowLog", False)
    if not isinstance(allow, bool):
        rep.err("allowLog", "true|false")
    raw = {"prompt": prompt, "lorebookBudget": budget, "startSessions": sessions, "macros": macros,
           "lorebooks": _lorebooks(spec, budget, rep, base_dir, pro), "playGuide": guide if isinstance(guide, str) else ""}
    _finish(rep, pro)
    return {**post, "rawNovel": raw, "allowLog": bool(allow)}, rep


def _novel_macros(spec: dict, rep: Report, resolve, base_dir: str) -> list:
    """`images` become the editor's own image macros ({{img::키}} -> ![](url)), placed before `macros`."""
    out = []
    images = spec.get("images", {})
    if isinstance(images, list):
        images = {str(i + 1): p for i, p in enumerate(images)}
    if not isinstance(images, dict):
        rep.err("images", "{키: 파일} 또는 [파일, …]")
        images = {}
    for key, p in images.items():
        where = f"images.{key}"
        if not key or "}" in key:
            rep.err(where, "키가 비었거나 '}'가 있습니다")
        url = _media(p, "image", where, rep, resolve, base_dir) if isinstance(p, str) else ""
        out.append({"type": "str_str", "from": "{{img::" + key + "}}", "to": f"![]({url})", "prefix": "", "suffix": ""})
    data = load_ref(spec.get("macros", []), base_dir, "macros", rep, want_json=True)
    if not isinstance(data, list):
        rep.err("macros", "배열이어야 합니다")
        data = []
    # the stored form carries prefix/suffix on every macro; the import form omits the unused ones
    keep = {"str_str": ("from", "to"), "regex_str": ("from", "to"), "regex_regex": ("from", "prefix", "suffix")}
    lean = []
    for m in data:
        if isinstance(m, dict) and m.get("type") in keep:
            lean.append({"type": m["type"], **{k: v for k, v in m.items() if k in keep[m["type"]]},
                         **{k: v for k, v in m.items() if k not in keep[m["type"]] and k != "type" and v != ""}})
        else:
            lean.append(m)
    if lean:
        errs, warns = R.check_macros([{"type": m["type"], "from": m["from"], "to": m["to"]} for m in out] + lean)
        for e in errs:
            rep.err("macros", e)
        for w in warns:
            rep.warn("macros", w)
    for m in lean:
        if isinstance(m, dict):
            out.append({"type": m.get("type"), "from": m.get("from", ""), "to": m.get("to", ""),
                        "prefix": m.get("prefix", ""), "suffix": m.get("suffix", "")})
    if len(out) > 1000:
        rep.err("macros", f"이미지 포함 {len(out)}개 > 1000")
    return out


# ---------------------------------------------------------------- cinema

def template_ref(spec: dict, base_dir: str) -> tuple[str, str | None]:
    """('file', template spec path) | ('id', templateId) | ('keep', None)."""
    t = spec.get("template", "keep")
    if t in ("keep", "", None):
        return "keep", None
    if isinstance(t, str) and (t.endswith(".json") or os.sep in t or "/" in t):
        return "file", os.path.normpath(os.path.join(base_dir, t))
    return "id", t


def image_vars(session_template: dict) -> set[str]:
    """Variables a frame layer shows directly (`return $x`): their values must be image names or URLs."""
    out = set()
    for s in session_template.get("sprites", []):
        if s.get("spriteType") == "frame":
            res = (s.get("assignedValueScript") or {}).get("res") or {}
            if res.get("type") == "VAR":
                out.add(res.get("varName"))
    return out


def build_cinema(spec: dict, session_template: dict | None, resolve=None, base_dir: str = ".") -> tuple[dict, Report]:
    """session_template: the template copy to store (from /get/ownTemplate, the stored post, or a local spec).
    None means it could not be resolved offline: variable checks are skipped."""
    rep, pro = Report(), []
    resolve = resolve or (lambda p, kind, where: p)
    post = _common(spec, "cinema", rep, resolve, base_dir)
    budget = _budget(spec, "cinema", rep)
    prompt = _prompt(spec, "cinema", budget, rep, base_dir, pro)

    names, urls, descs = [], [], []
    images = spec.get("images", {})
    if not isinstance(images, dict):
        rep.err("images", "{이미지 이름: 파일 | {src, desc}}")
        images = {}
    for name, v in images.items():
        where = f"images.{name}"
        src, desc = (v, "") if isinstance(v, str) else ((v or {}).get("src", ""), (v or {}).get("desc", ""))
        if not name or js_len(name) > 15:
            rep.err(where, "이미지 이름 1~15자")
        if not isinstance(desc, str) or js_len(desc) > 30:
            rep.err(where, "설명 30자 이하")
        names.append(name)
        urls.append(_media(src, "image", where, rep, resolve, base_dir) if src else "")
        descs.append(desc if isinstance(desc, str) else "")
        if not src:
            rep.err(where, "src(파일 또는 URL)가 비었습니다")
    if len(names) > 100:
        pro.append(f"이미지 {len(names)}장 > 100")
    if len(names) > 1000:
        rep.err("images", f"{len(names)}장 > 1000")

    variables = (session_template or {}).get("variables")
    by_name = {v["varName"]: v for v in variables} if variables else None
    frames = image_vars(session_template) if session_template else set()
    sessions = []
    for i, s in enumerate(_sessions(spec, rep)):
        if not isinstance(s, dict):
            continue
        where = f"startSessions[{i}]"
        unknown = set(s) - {"sessionTitle", "initDesc", "defaults", "logs"}
        if unknown:
            rep.warn(where, f"모르는 키 {sorted(unknown)}(시네마챗 시작 세션은 sessionTitle·initDesc·defaults·logs)")
        defaults = s.get("defaults", {})
        if not isinstance(defaults, dict):
            rep.err(f"{where}.defaults", "{변수: 값}")
            defaults = {}
        defaults = {k: ("" if v is None else str(v).lower() if isinstance(v, bool) else str(v)) for k, v in defaults.items()}
        logs = []
        for j, lg in enumerate(s.get("logs", [])):
            lw = f"{where}.logs[{j}]"
            if isinstance(lg, (list, tuple)) and len(lg) == 2:
                var, val = lg
            elif isinstance(lg, dict) and set(lg) <= {"varName", "value"}:
                var, val = lg.get("varName"), lg.get("value", "")
            else:
                rep.err(lw, '["변수", "값"] 또는 {"varName", "value"}')
                continue
            val = str(val).lower() if isinstance(val, bool) else str(val)
            logs.append({"varName": var, "value": val})
        if len(logs) > 40 or sum(len(x["value"]) for x in logs) > 1000:
            pro.append(f"{where} 인트로 {len(logs)}개·{sum(len(x['value']) for x in logs)}자 > 40개·1000자")
        if len(logs) > 400 or sum(len(x["value"]) for x in logs) > 10000:
            rep.err(f"{where}.logs", "프로 모드 한도 400개·10000자 초과")
        if sum(len(v) for v in defaults.values()) > 250:
            pro.append(f"{where} 기본값 글자 합 {sum(len(v) for v in defaults.values())} > 250")
        if by_name is not None:
            for k in defaults:
                if k not in by_name:
                    rep.err(f"{where}.defaults", f"템플릿에 없는 변수 '{k}' (있는 변수: {', '.join(by_name)})")
            for j, lg in enumerate(logs):
                v = by_name.get(lg["varName"])
                if v is None:
                    rep.err(f"{where}.logs[{j}]", f"템플릿에 없는 변수 '{lg['varName']}'")
                elif v["mode"] == "none":
                    rep.warn(f"{where}.logs[{j}]", f"'{lg['varName']}'은 시스템 변수입니다(AI 로그가 아니라 스크립트가 바꾸는 값)")
            for var, val in list(defaults.items()) + [(x["varName"], x["value"]) for x in logs]:
                if var in frames and val and not _is_url(val) and val not in names:
                    rep.warn(where, f"'{var}' 값 '{val}'은 등록된 이미지 이름이 아닙니다(프레임이 빈 화면이 됨)")
            init = [defaults.get(v["varName"], "") for v in variables]
        else:
            init = defaults  # resolved at upload time
        sessions.append({"sessionTitle": s.get("sessionTitle", ""),
                         "initDesc": _session_text(s, "initDesc", where, rep, base_dir, 1000, 1500, pro, 15000),
                         "initSession": init, "logs": logs})
    raw = {"sessionTemplate": session_template, "prompt": prompt, "lorebookBudget": budget, "startSessions": sessions,
           "imageNames": names, "imageUrls": urls, "imageDescriptions": descs,
           "lorebooks": _lorebooks(spec, budget, rep, base_dir, pro)}
    if session_template is None:
        rep.warn("template", "템플릿을 오프라인으로 풀지 못해 변수 검사를 건너뜁니다(업로드 때 서버 목록으로 검사)")
    _finish(rep, pro)
    return {**post, "rawCharacter": raw}, rep


# ---------------------------------------------------------------- payload -> spec (pull)

def post_to_spec(kind: str, post: dict, out_dir: str | None = None) -> dict:
    """Stored post -> spec. With out_dir, long text and JSON lists go to side files referenced with '@'."""
    raw = post[KINDS[kind]["raw"]]

    def side(name: str, value, as_json=False):
        if out_dir is None or (not as_json and len(value) < 300) or (as_json and not value):
            return value
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(json.dumps(value, ensure_ascii=False, indent=1) + "\n" if as_json else value)
        return "@" + name

    sp = {"kind": kind, "title": post.get("title", ""), "description": side("description.md", post.get("description", "")),
          "visibility": post.get("visibility", "private"), "thumbnail": post.get("thumbnail", ""),
          "tags": post.get("tags", []), "defaultModel": post.get("defaultModel", DEFAULT_MODEL),
          "prompt": side("prompt.md", raw.get("prompt", "")), "lorebookBudget": raw.get("lorebookBudget", 2500)}
    if kind == "novel":
        sp["allowLog"] = post.get("allowLog", False)
        sp["playGuide"] = raw.get("playGuide", "")
        sp["startSessions"] = [{"sessionTitle": s.get("sessionTitle", ""), "initDesc": s.get("initDesc", ""),
                                "initChat": s.get("initChat", "")} for s in raw.get("startSessions", [])]
        lean = []
        for m in raw.get("macros", []):
            need = ("from", "prefix", "suffix") if m.get("type") == "regex_regex" else ("from", "to")
            lean.append({"type": m.get("type"), **{k: m.get(k, "") for k in need}})
        sp["macros"] = side("macros.json", lean, as_json=True)
    else:
        st = raw.get("sessionTemplate") or {}
        sp["template"] = "keep"
        sp["note"] = "template keep = 저장된 템플릿 사본을 그대로 둠. 템플릿을 새 버전으로 바꾸려면 템플릿 스펙 경로나 ID"
        sp["images"] = {n: ({"src": u, "desc": d} if d else u) for n, u, d in
                        zip(raw.get("imageNames", []), raw.get("imageUrls", []), raw.get("imageDescriptions", []))}
        names = [v["varName"] for v in st.get("variables", [])]
        sp["startSessions"] = [{"sessionTitle": s.get("sessionTitle", ""), "initDesc": s.get("initDesc", ""),
                                "defaults": {n: v for n, v in zip(names, s.get("initSession", [])) if v != ""},
                                "logs": [[x.get("varName"), x.get("value", "")] for x in s.get("logs", [])]}
                               for s in raw.get("startSessions", [])]
    sp["lorebooks"] = side("lorebooks.json", [lorebook_from_stored(lb) for lb in raw.get("lorebooks", [])], as_json=True)
    return sp


def diff(kind: str, sent: dict, stored: dict) -> list[str]:
    """Fields the server stored differently from what was sent. The server always stores a cinema
    chat's template copy with templateId "none", so that one field is not a difference."""
    rk = KINDS[kind]["raw"]
    out = [k for k in sent if k != rk and stored.get(k) != sent[k]]
    for k in sent[rk]:
        a, b = sent[rk][k], stored.get(rk, {}).get(k)
        if k == "sessionTemplate" and isinstance(a, dict) and isinstance(b, dict):
            a, b = {**a, "templateId": None}, {**b, "templateId": None}
        if a != b:
            out.append(f"{rk}.{k}")
    return out

