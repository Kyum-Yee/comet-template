"""Template spec (agent-facing JSON) <-> upload payload (platform JSON), plus validation.

Spec layers run bottom -> top (background first). The platform stores sprites top -> bottom
(index 0 is drawn last and receives clicks first), so build/pull reverse the list.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Callable

import ast

from . import dsl, pyc

USER_INPUT = "유저 입력"
FONTS = ["Pretendard", "나눔스퀘어_ac", "경기천년제목", "에스코어드림", "나눔바른고딕", "이순신체", "D2Coding",
         "검은고딕", "잉크립퀴드", "조선굴림체", "경기천년바탕", "Neo둥근모"]
FONT_DEFAULT = "Pretendard Variable"  # what the editor puts on a new text object
VAR_TYPES = ["string", "integer", "number", "boolean"]
KIND_TO_MODE = {"global": "default", "system": "none"}
MODE_TO_KIND = {"default": "global", "none": "system"}
IMAGE_HOST = "https://image.comet-talk.ai/"
AUDIO_HOST = "https://audio.comet-talk.ai/"
LIMITS = dict(title=20, description=2500, prompt=1000, var_name=10, globals=20, systems=200,
              sprites=200, triggers=200, storage=1000, file_bytes=5 * 1024 * 1024)
ENUMS = {
    "textAlign": ["left", "center", "right"],
    "easing": ["none", "typing", "fade"],
    "overflow": ["hidden", "scroll"],
    "whiteSpace": ["normal", "pre-wrap"],
    "objectFit": ["contain", "cover"],
    "moping": ["none", "fade", "cross-fade"],
}
DEFAULTS = {
    "text": dict(name="텍스트", x=512, y=288, width=256, height=144, fontSize=32, color="#FFFFFF", textAlign="center",
                 easing="none", fontFamily=FONT_DEFAULT, fontWeight=500, overflow="hidden", whiteSpace="normal"),
    "image": dict(name="이미지", x=620, y=260, width=200, height=200),
    "frame": dict(name="프레임", x=320, y=180, width=640, height=360, borderRadius=0, objectFit="contain",
                  moping="none", duration=300),
}
LAYER_KEYS = {
    "text": {"value", "fontSize", "color", "textAlign", "easing", "fontFamily", "fontWeight", "overflow", "whiteSpace"},
    "image": {"src", "action"},
    "frame": {"value", "borderRadius", "objectFit", "moping", "duration"},
}
COMMON_KEYS = {"type", "name", "x", "y", "width", "height", "state", "note"}
KEY_CODE_RE = re.compile(r"^(Key[A-Z]|Digit\d|Numpad(\d|Add|Subtract|Multiply|Divide|Decimal|Enter|Equal)|F([1-9]|1\d|2[0-4])|"
                         r"Arrow(Up|Down|Left|Right)|Space|Enter|Escape|Backspace|Tab|Delete|Insert|Home|End|PageUp|PageDown|"
                         r"(Shift|Control|Alt|Meta)(Left|Right)|CapsLock|Backquote|Minus|Equal|BracketLeft|BracketRight|"
                         r"Backslash|Semicolon|Quote|Comma|Period|Slash|IntlBackslash|IntlRo|IntlYen|ContextMenu)$")
DEFAULT_TRIGGERS = [{"on": "key:ArrowLeft", "do": "prev"}, {"on": "key:ArrowRight", "do": "next"}]


def js_len(s: str) -> int:
    """String length as the site's JS counts it (UTF-16 code units)."""
    return len(s.encode("utf-16-le")) // 2


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def err(self, where: str, msg: str):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str):
        self.warnings.append(f"{where}: {msg}")

    @property
    def ok(self) -> bool:
        return not self.errors

    def text(self) -> str:
        lines = [f"✗ {e}" for e in self.errors] + [f"△ {w}" for w in self.warnings]
        return "\n".join(lines) if lines else "✓ 문제 없음"


def _is_url(s: str) -> bool:
    return isinstance(s, str) and s.startswith(("http://", "https://"))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ---------------------------------------------------------------- variables

def variable_table(spec: dict, rep: Report) -> list[dict]:
    """Platform variable list; '유저 입력' always first."""
    out = [{"varName": USER_INPUT, "isAuto": True, "mode": "refer", "type": "string"}]
    seen = {USER_INPUT}
    for i, v in enumerate(spec.get("variables", [])):
        where = f"variables[{i}]"
        if isinstance(v, str):
            v = {"name": v}
        name = v.get("name", "")
        if name == USER_INPUT:
            continue  # built in; ignore a restatement
        kind = v.get("kind", "global")
        typ = v.get("type", "string")
        if not name:
            rep.err(where, "name이 비었습니다")
            continue
        if js_len(name) > LIMITS["var_name"]:
            rep.err(where, f"변수명 '{name}'이 {LIMITS['var_name']}자를 넘습니다")
        if "}" in name or "\n" in name:
            rep.err(where, f"변수명 '{name}'에 '}}'나 줄바꿈을 쓸 수 없습니다")
        if name in seen:
            rep.err(where, f"변수명 '{name}' 중복")
        if kind not in KIND_TO_MODE:
            rep.err(where, f"kind는 global|system ('{kind}')")
            continue
        if typ not in VAR_TYPES:
            rep.err(where, f"type은 {'|'.join(VAR_TYPES)} ('{typ}')")
        unknown = set(v) - {"name", "kind", "type", "auto", "note"}
        if unknown:
            rep.warn(where, f"모르는 키 {sorted(unknown)}는 무시합니다")
        seen.add(name)
        out.append({"varName": name, "isAuto": bool(v.get("auto", False)), "mode": KIND_TO_MODE[kind], "type": typ})
    n_glob = sum(1 for v in out if v["mode"] == "default")
    n_sys = sum(1 for v in out if v["mode"] == "none")
    if n_glob == 0:
        rep.err("variables", "전역(global) 변수가 최소 1개 필요합니다(서버 규칙: '전역변수가 필요합니다')")
    if n_glob > LIMITS["globals"]:
        rep.err("variables", f"전역 변수 {n_glob}개 > {LIMITS['globals']}")
    if n_sys > LIMITS["systems"]:
        rep.err("variables", f"시스템 변수 {n_sys}개 > {LIMITS['systems']}")
    return out


# ---------------------------------------------------------------- scripts with reference checks

class ScriptChecker:
    def __init__(self, variables: list[dict], images: dict, audio: dict, rep: Report, lang: str = "cs",
                 module: "pyc.Module | None" = None):
        self.modes = {v["varName"]: v["mode"] for v in variables}
        self.lang = lang
        self.py = pyc.PyCompiler(variables, module)
        self.images, self.audio, self.rep = images, audio, rep
        self.read_vars: set[str] = set()
        self.next_used = False
        self.prev_used = False

    def compile(self, src, context: str, where: str):
        try:
            if isinstance(src, ast.FunctionDef):
                node, ret = self.py.compile_function(src, context)
            elif self.lang == "py" and src and str(src).strip():
                node, ret = self.py.compile_source(src, context)
            elif context == "value":
                node, ret = dsl.compile_value(src)
            else:
                node, ret = dsl.compile_script(src, context)
        except dsl.ScriptError as e:
            self.rep.err(where, f"스크립트 오류 — {e}")
            return {"type": "NULL"}, {"type": "CONST", "value": ""}
        self.check(node, ret, where)
        return node, ret

    def check(self, node: dict, ret: dict | None, where: str):
        assigned_locals = set()
        for st in dsl.iter_stmts(node):
            t = st["type"]
            if t == "SET_GLOBAL":
                mode = self.modes.get(st["varName"])
                if mode is None:
                    self.rep.err(where, f"없는 변수 ${st['varName']}에 대입")
                elif mode != "none":
                    self.rep.err(where, f"${st['varName']}는 {MODE_TO_KIND.get(mode, '유저 입력')} 변수라 스크립트로 바꿀 수 없습니다(시스템 변수만 가능)")
            elif t == "SET_LOCAL":
                assigned_locals.add(st["localVarName"])
            elif t == "GO_NEXT":
                self.next_used = True
            elif t == "GO_PREV":
                self.prev_used = True
        roots = [node] + ([ret] if ret else [])
        for root in roots:
            for v in dsl.iter_values(root):
                if v["type"] == "VAR":
                    if v["varName"] not in self.modes:
                        self.rep.err(where, f"없는 변수 ${v['varName']}를 읽습니다(서버가 '사용 불가능한 스크립트'로 거부)")
                    self.read_vars.add(v["varName"])
                elif v["type"] == "LOCAL_VAR" and v["localVarName"] not in assigned_locals:
                    self.rep.warn(where, f"@{v['localVarName']}를 이 스크립트에서 대입하기 전에 읽습니다(지역 변수는 스크립트마다 새로 시작, 값은 빈 문자열)")
                elif v["type"] in ("GET_IMAGE_STORAGE", "GET_AUDIO_STORAGE"):
                    arg = v["value"]
                    store, label = (self.images, "images") if v["type"] == "GET_IMAGE_STORAGE" else (self.audio, "audio")
                    if arg["type"] == "CONST" and arg["value"] not in store:
                        self.rep.warn(where, f"{label}에 '{arg['value']}' 키가 없습니다(실행 시 빈 문자열)")


# ---------------------------------------------------------------- spec -> payload

Resolver = Callable[[str, str, str], str]  # (path_or_url, kind 'image'|'audio'|'thumbnail', where) -> url


def build_payload(spec: dict, resolve: Resolver | None = None, base_dir: str = ".") -> tuple[dict, Report]:
    rep = Report()
    resolve = resolve or (lambda p, kind, where: p)

    def text_field(key, limit, required=True):
        val = spec.get(key, "")
        if not isinstance(val, str):
            rep.err(key, "문자열이어야 합니다")
            return ""
        if required and not val.strip():
            rep.err(key, "비어 있습니다")
        if js_len(val) > limit:
            rep.err(key, f"{js_len(val)}자 > {limit}자")
        return val

    title = text_field("title", LIMITS["title"])
    description = text_field("description", LIMITS["description"])
    prompt = text_field("prompt", LIMITS["prompt"])
    visibility = spec.get("visibility", "private")
    if visibility not in ("public", "private"):
        rep.err("visibility", "public|private 중 하나(템플릿에는 링크 공개가 없습니다)")

    unknown = set(spec) - {"title", "description", "prompt", "visibility", "thumbnail", "variables", "images", "audio",
                           "layers", "triggers", "note", "$schema", "scripts", "lang"}
    if unknown:
        rep.warn("spec", f"모르는 최상위 키 {sorted(unknown)}는 무시합니다")

    def local(p: str) -> str:
        return p if _is_url(p) or os.path.isabs(p) else os.path.normpath(os.path.join(base_dir, p))

    def media(p, kind, where) -> str:
        if not isinstance(p, str) or not p:
            rep.err(where, "경로나 URL이 필요합니다")
            return ""
        if _is_url(p):
            host = AUDIO_HOST if kind == "audio" else IMAGE_HOST
            if not p.startswith(host):
                rep.err(where, f"외부 URL은 쓸 수 없습니다({host}…만). 로컬 파일 경로를 주면 업로드합니다 ({p})")
            return resolve(p, kind, where)
        lp = local(p)
        if not os.path.isfile(lp):
            rep.err(where, f"파일 없음: {lp}")
            return ""
        size = os.path.getsize(lp)
        if size > LIMITS["file_bytes"]:
            rep.err(where, f"{size/1048576:.1f}MB > 5MB")
        return resolve(lp, kind, where)

    thumb = spec.get("thumbnail")
    thumbnail = media(thumb, "thumbnail", "thumbnail") if thumb else ""
    if not thumb:
        rep.err("thumbnail", "썸네일이 필요합니다(16:9, 5MB 이하)")

    variables = variable_table(spec, rep)

    images_spec = spec.get("images", {}) or {}
    audio_spec = spec.get("audio", {}) or {}
    for label, store in (("images", images_spec), ("audio", audio_spec)):
        if not isinstance(store, dict):
            rep.err(label, "{이름: 경로|URL} 객체여야 합니다")
        elif len(store) > LIMITS["storage"]:
            rep.err(label, f"{len(store)}개 > {LIMITS['storage']}")
    image_storage = [{"imageName": k, "url": media(v, "image", f"images.{k}")} for k, v in images_spec.items()]
    audio_storage = [{"audioName": k, "url": media(v, "audio", f"audio.{k}")} for k, v in audio_spec.items()]
    url_of_image = {d["imageName"]: d["url"] for d in image_storage}

    # script language: "cs" (indent DSL, default) or "py"; a "scripts" logic file implies py
    module = None
    lang = spec.get("lang", "py" if spec.get("scripts") else "cs")
    if lang not in ("cs", "py"):
        rep.err("lang", "cs|py")
    layers = spec.get("layers", [])
    slot_src: dict[tuple[int, str], ast.FunctionDef] = {}
    file_triggers: list[tuple[str, ast.FunctionDef]] = []
    if spec.get("scripts"):
        path = local(spec["scripts"])
        try:
            module = pyc.Module(open(path, encoding="utf-8").read(), path)
        except OSError:
            rep.err("scripts", f"파일 없음: {path}")
        except dsl.ScriptError as e:
            rep.err("scripts", f"{os.path.basename(path)} {e}")
        if module:
            by_name: dict[str, list[int]] = {}
            for i, L in enumerate(layers):
                if isinstance(L, dict):
                    by_name.setdefault(str(L.get("name", DEFAULTS.get(L.get("type"), {}).get("name", ""))), []).append(i)
            for kind, target, fn in module.entries:
                where = f"{os.path.basename(path)}:{fn.lineno} @{kind}({target!r})"
                if kind == "on":
                    file_triggers.append((str(target), fn))
                    continue
                if isinstance(target, int):
                    idx = [target] if 0 <= target < len(layers) else []
                else:
                    idx = by_name.get(str(target), [])
                if len(idx) != 1:
                    rep.err(where, "그런 레이어가 없습니다" if not idx else f"이름이 같은 레이어가 {len(idx)}개입니다. 번호(아래→위 0부터)로 지정하세요")
                    continue
                if (idx[0], kind) in slot_src:
                    rep.err(where, "같은 레이어·같은 스크립트가 두 번 정의됐습니다")
                slot_src[(idx[0], kind)] = fn

    def source(i, L, key, kind):
        if (i, kind) in slot_src:
            if L.get(key):
                rep.err(f"layers[{i}]", f"{key}가 스펙과 {spec.get('scripts')} 양쪽에 있습니다. 한 곳만")
            return slot_src[(i, kind)]
        return L.get(key)

    checker = ScriptChecker(variables, images_spec, audio_spec, rep, lang, module)
    if len(layers) > LIMITS["sprites"]:
        rep.err("layers", f"{len(layers)}개 > {LIMITS['sprites']}")
    sprites = []
    for i, L in enumerate(layers):
        where = f"layers[{i}]" + (f"({L.get('name')})" if isinstance(L, dict) and L.get("name") else "")
        if not isinstance(L, dict) or L.get("type") not in DEFAULTS:
            rep.err(where, "type은 text|image|frame")
            continue
        typ = L["type"]
        unknown = set(L) - COMMON_KEYS - LAYER_KEYS[typ]
        if unknown:
            rep.err(where, f"{typ}에 없는 키 {sorted(unknown)} (오타? 쓸 수 있는 키: {sorted((COMMON_KEYS | LAYER_KEYS[typ]) - {'type'})})")
        d = {**DEFAULTS[typ], **{k: v for k, v in L.items() if v is not None}}
        for k in ("x", "y", "width", "height"):
            if not _num(d[k]):
                rep.err(where, f"{k}는 숫자여야 합니다")
        if _num(d["width"]) and _num(d["height"]) and (d["width"] <= 0 or d["height"] <= 0):
            rep.err(where, "width/height는 0보다 커야 합니다")
        if all(_num(d[k]) for k in ("x", "y", "width", "height")):
            if d["x"] >= 1280 or d["y"] >= 720 or d["x"] + d["width"] <= 0 or d["y"] + d["height"] <= 0:
                rep.warn(where, "처음 위치가 캔버스(1280×720) 밖입니다(스크립트로 들여오는 게 아니라면 확인)")
        for k, allowed in ENUMS.items():
            if k in d and k in LAYER_KEYS[typ] and d[k] not in allowed:
                rep.err(where, f"{k}는 {'|'.join(allowed)} ('{d[k]}')")
        for kind, ok in (("action", typ == "image"), ("value", typ != "image")):
            if (i, kind) in slot_src and not ok:
                rep.err(where, f"{typ} 레이어에는 @{kind} 스크립트가 없습니다")
        state_node, _ = checker.compile(source(i, L, "state", "state"), "state", f"{where}.state")
        base = {"spriteType": typ, "objectName": str(d["name"]), "x": d["x"], "y": d["y"], "width": d["width"],
                "height": d["height"], "if": "", "stateScript": {"node": state_node, "dummies": []}}
        if typ in ("text", "frame"):
            if "value" not in L and (i, "value") not in slot_src:
                rep.err(where, f"{typ}에는 value(표시할 값, 예: \"$대사\")가 필요합니다")
            node, ret = checker.compile(source(i, L, "value", "value") or "", "value", f"{where}.value")
            first_var = next((v["varName"] for v in dsl.iter_values(ret) if v["type"] == "VAR"), USER_INPUT)
            base["assignedValue"] = first_var
            # the player times typing/fade/moping per change of a *global* variable read directly in res
            direct_globals = {v["varName"] for v in dsl.iter_values(ret) if v["type"] == "VAR"} & {
                v["varName"] for v in variables if v["mode"] == "default"}
            effect = d.get("easing") if typ == "text" else d.get("moping")
            if effect and effect != "none" and not direct_globals:
                rep.warn(where, f"{'easing' if typ == 'text' else 'moping'}='{effect}'는 return 식에 전역 변수를 직접 써야 돕니다"
                                "(@지역변수·시스템 변수·유저 입력 경유면 효과 없이 바로 바뀜)")
            base["assignedValueScript"] = {"node": node, "res": ret, "dummies": []}
        if typ == "text":
            for k in ("fontSize", "fontWeight"):
                if not _num(d[k]) or d[k] <= 0:
                    rep.err(where, f"{k}는 양수")
            if d["fontFamily"] not in FONTS and d["fontFamily"] != FONT_DEFAULT:
                rep.warn(where, f"fontFamily '{d['fontFamily']}'는 에디터 목록에 없습니다(서버는 받지만 기본 글꼴로 그려짐). 목록: {', '.join(FONTS)}")
            if not isinstance(d["color"], str) or not d["color"]:
                rep.err(where, "color는 '#RRGGBB'·'#RRGGBBAA'·'white' 같은 CSS 색 문자열")
            base.update({k: d[k] for k in ("fontSize", "color", "textAlign", "easing", "overflow", "whiteSpace", "fontFamily", "fontWeight")})
        elif typ == "frame":
            if not _num(d["borderRadius"]) or not _num(d["duration"]):
                rep.err(where, "borderRadius·duration은 숫자")
            base.update({"borderRadius": d["borderRadius"], "prefix": "", "objectFit": d["objectFit"],
                         "moping": d["moping"], "duration": d["duration"]})
        else:
            src = L.get("src")
            if not src:
                rep.err(where, "image에는 src(images 키, 로컬 파일, image.comet-talk.ai URL)가 필요합니다")
                url = ""
            elif src in url_of_image:
                url = url_of_image[src]
            else:
                url = media(src, "image", f"{where}.src")
            node, _ = checker.compile(source(i, L, "action", "action"), "action", f"{where}.action")
            first = node["type"]
            func = {"GO_PREV": "prev", "GO_NEXT": "next"}.get(first, "none") if node.get("cont", {}).get("type") == "NULL" else "none"
            base.update({"imageURL": url, "func": func, "actionScript": {"node": node, "dummies": []}})
        sprites.append(base)
    sprites.reverse()

    trig_spec = list(spec.get("triggers", [] if file_triggers else DEFAULT_TRIGGERS))
    trig_spec += [{"on": on, "do": fn} for on, fn in file_triggers]
    if len(trig_spec) > LIMITS["triggers"]:
        rep.err("triggers", f"{len(trig_spec)}개 > {LIMITS['triggers']}")
    triggers = []
    for i, tr in enumerate(trig_spec):
        where = f"triggers[{i}]"
        on = str(tr.get("on", ""))
        if on in ("load", "mount", "onmounted"):
            cond = {"type": "ONMOUNTED"}
        elif on.startswith("key:"):
            code = on[4:]
            if not KEY_CODE_RE.match(code):
                rep.err(where, f"키 '{code}'는 KeyboardEvent.code 이름이어야 합니다(예: KeyA, Digit1, Space, Enter, ArrowLeft)")
            cond = {"type": "KEYDOWN", "key": code}
        else:
            rep.err(where, "on은 'load' 또는 'key:<KeyboardEvent.code>'")
            continue
        node, _ = checker.compile(tr.get("do"), "action", f"{where}.do")
        triggers.append({"triggerCond": cond, "node": node, "dummies": []})

    # whole-template sanity
    if not (checker.next_used):
        rep.warn("template", "어디에도 next(진행하기)가 없습니다. 클릭 버튼이나 키 트리거가 없으면 플레이어가 로그를 넘길 수 없습니다")
    globals_ = [v["varName"] for v in variables if v["mode"] == "default"]
    for g in globals_:
        if g not in checker.read_vars:
            rep.warn("variables", f"전역 변수 ${g}를 읽는 레이어가 없습니다(AI가 값을 써도 화면에 안 보임)")
        if prompt and g not in prompt:
            rep.warn("prompt", f"프롬프트에 전역 변수 '{g}'의 설명이 없습니다(AI는 프롬프트로 변수 쓰임을 배웁니다)")
    for v in variables:
        if v["mode"] == "none" and v["varName"] not in checker.read_vars:
            rep.warn("variables", f"시스템 변수 ${v['varName']}를 읽는 스크립트가 없습니다")

    payload = {
        "thumbnail": thumbnail,
        "visibility": visibility,
        "price": 0,
        "title": title,
        "description": description,
        "rawTemplate": {
            "prompt": prompt,
            "variables": variables,
            "sprites": sprites,
            "imageStorage": image_storage,
            "audioStorage": audio_storage,
            "triggers": triggers,
        },
    }
    return payload, rep


# ---------------------------------------------------------------- payload -> spec

def payload_to_spec(post: dict) -> dict:
    """Decompile a stored template (postTemplate or sessionTemplate shape) into an editable spec."""
    raw = post.get("rawTemplate") or post.get("sessionTemplate") or post
    spec: dict = {}
    for k in ("title", "description", "thumbnail", "visibility"):
        if k in post:
            spec[k] = post[k]
    spec["prompt"] = raw.get("prompt", "")
    spec["variables"] = [
        {"name": v["varName"], "kind": MODE_TO_KIND[v["mode"]], "type": v.get("type", "string"), "auto": bool(v.get("isAuto"))}
        for v in raw.get("variables", []) if v.get("mode") != "refer"
    ]
    spec["images"] = {d["imageName"]: d["url"] for d in raw.get("imageStorage", [])}
    spec["audio"] = {d["audioName"]: d["url"] for d in raw.get("audioStorage", [])}
    by_url = {u: n for n, u in spec["images"].items()}
    layers = []
    for s in reversed(raw.get("sprites", [])):
        typ = s["spriteType"]
        L = {"type": typ, "name": s.get("objectName", DEFAULTS[typ]["name"])}
        for k in ("x", "y", "width", "height"):
            L[k] = s[k]
        state = dsl.decompile(s["stateScript"]["node"])
        if state:
            L["state"] = state
        if typ in ("text", "frame"):
            a = s["assignedValueScript"]
            L["value"] = dsl.decompile(a["node"], a["res"])
        if typ == "text":
            for k in ("fontSize", "color", "textAlign", "easing", "fontFamily", "fontWeight", "overflow", "whiteSpace"):
                if k in s:
                    L[k] = s[k]
        elif typ == "frame":
            for k in ("borderRadius", "objectFit", "moping", "duration"):
                if k in s:
                    L[k] = s[k]
        else:
            L["src"] = by_url.get(s.get("imageURL", ""), s.get("imageURL", ""))
            act = dsl.decompile(s["actionScript"]["node"])
            if act:
                L["action"] = act
        layers.append(L)
    spec["layers"] = layers
    trig = []
    for t in raw.get("triggers", []):
        c = t["triggerCond"]
        on = "load" if c["type"] == "ONMOUNTED" else f"key:{c.get('key', '')}"
        trig.append({"on": on, "do": dsl.decompile(t["node"])})
    spec["triggers"] = trig
    return spec


def check_raw(raw: dict) -> list[str]:
    """Structural check of a platform rawTemplate (e.g. a hand-edited payload)."""
    errs = []
    for i, s in enumerate(raw.get("sprites", [])):
        p = f"sprites[{i}]"
        errs += dsl.validate_tree("stmt", s.get("stateScript", {}).get("node"), p + ".stateScript")
        if s.get("spriteType") in ("text", "frame"):
            a = s.get("assignedValueScript", {})
            errs += dsl.validate_tree("stmt", a.get("node"), p + ".assignedValueScript.node")
            errs += dsl.validate_tree("value", a.get("res"), p + ".assignedValueScript.res")
        elif s.get("spriteType") == "image":
            errs += dsl.validate_tree("stmt", s.get("actionScript", {}).get("node"), p + ".actionScript")
        for k in ("if", "objectName"):
            if k not in s:
                errs.append(f"{p}.{k}: 서버 스키마 필수 필드")
        if s.get("spriteType") == "image" and "func" not in s:
            errs.append(f"{p}.func: 서버 스키마 필수 필드")
    for i, t in enumerate(raw.get("triggers", [])):
        errs += dsl.validate_tree("stmt", t.get("node"), f"triggers[{i}].node")
        if t.get("triggerCond", {}).get("type") not in ("ONMOUNTED", "KEYDOWN"):
            errs.append(f"triggers[{i}].triggerCond: ONMOUNTED|KEYDOWN (그 밖이면 서버가 트리거 전체를 기본값으로 바꿔 버림)")
    return errs
