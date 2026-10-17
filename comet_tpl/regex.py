"""Comet regex toolkit: the exact rules the site applies to macro and lorebook regexes.

Facts taken from the site bundle (2026-10-16):
- Regexes run in the browser as JavaScript `new RegExp(pattern, "g")`: no i/m/s/u flags, ever.
- Before a regex macro runs, the site asks safe-regex v2 (davisjam) `safeRegex(new RegExp(p, "g"))`.
  Unsafe means: a quantifier nested inside a quantified part (star height > 1), more than 25
  quantifiers in total, or a pattern its parser (regexp-tree) cannot read. An unsafe macro is
  skipped silently at render time; the editor and the JSON upload refuse it
  ("정규식에서 공격 위험이 감지되었습니다").
- Macros run top to bottom on the AI output (vo()):
    str_str      text.replace(new RegExp(escape(from), "g"), to)   -- `to` still expands $&, $1, $$
    regex_str    text.replace(new RegExp(from, "g"), to)           -- JS replacement: $1 $& $` $' $<name> $$
    regex_regex  for every match, the first capture group (or the whole match when there is none)
                 is handed to the NEXT macro only, then wrapped as prefix + result + suffix;
                 consecutive regex_regex macros chain; the first non-chain macro after them is the
                 receiver and is consumed by the chain (the main pass resumes after it).
- Render order after macros: {user} (with Korean particle fix) → {turn} → {rand} → markdown-it
  (breaks, html) → **b** / *i* → DOMPurify (no style/script/iframe/object/embed).
- Lorebook regex triggers: the editor only checks `new RegExp(p)` compiles; the server matches them
  with RE2 (Comet, official, 2026-10-17): linear time, so no ReDoS, but no lookaround, no
  backreference, repeats up to 1000, and a pattern RE2 cannot parse is ignored (the trigger never fires).

This module ports the safety heuristic (verified against safe-regex 2.1.1 in tests), validates and
runs patterns with node when available (exact JS), and generates range / alternation regexes.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field

REP_LIMIT = 25


# ---------------------------------------------------------------- structure & safety (safe-regex v2 port)

@dataclass
class Structure:
    ok: bool
    error: str = ""
    repetitions: int = 0
    star_height: int = 0
    groups: int = 0
    named: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unparsed: bool = False  # V8 accepts it but safe-regex's parser (regexp-tree) does not

    @property
    def safe(self) -> bool:
        # safe-regex swallows its own parser's exception as "not vulnerable": such patterns pass unchecked
        return self.ok and (self.unparsed or self.star_height <= 1 and self.repetitions <= REP_LIMIT)

    def verdict(self) -> str:
        if not self.ok:
            return f"✗ 문법 오류: {self.error}"
        if self.unparsed:
            return "✓ 통과(사이트 판정기가 이 문법을 못 읽어 검사 없이 통과시킵니다)"
        why = []
        if self.star_height > 1:
            why.append(f"반복 안에 반복(중첩 깊이 {self.star_height})")
        if self.repetitions > REP_LIMIT:
            why.append(f"반복 기호 {self.repetitions}개 > {REP_LIMIT}")
        return "✓ 안전" if not why else "✗ 위험 판정: " + ", ".join(why)


QUANT_RE = re.compile(r"\{(\d+)(,(\d*))?\}")


def analyze(p: str) -> Structure:
    """Parse a JS (non-unicode, Annex B) regex enough to count quantifiers and their nesting."""
    st = Structure(ok=True)
    # stack of frames: each frame = [max_height_inside_this_group, last_atom_height or None]
    stack = [[0, None, None, False]]  # inner max height, height of last atom, group kind, last atom is a lookahead
    i, n = 0, len(p)
    classes = []  # (start, end) of each [...] class

    def atom(h=0):
        fr = stack[-1]
        fr[1], fr[3] = h, False

    def quantify(lazy_at):
        fr = stack[-1]
        if fr[1] is None:
            raise ValueError("반복할 대상이 없습니다(Nothing to repeat)")
        if fr[3]:
            st.unparsed = True  # regexp-tree rejects a quantified lookahead
        h = fr[1] + 1
        st.repetitions += 1
        fr[0] = max(fr[0], h)
        fr[1] = None  # a quantified atom cannot be quantified again
        return lazy_at + 1 if lazy_at < n and p[lazy_at] == "?" else lazy_at

    try:
        while i < n:
            c = p[i]
            if c == "\\":
                if i + 1 >= n:
                    raise ValueError("\\로 끝납니다")
                nx = p[i + 1]
                if nx in "bB":
                    stack[-1][1] = None
                    i += 2
                    continue
                if nx == "u" and re.match(r"[0-9a-fA-F]{4}", p[i + 2:i + 6]):
                    i += 6
                elif nx == "x" and re.match(r"[0-9a-fA-F]{2}", p[i + 2:i + 4]):
                    i += 4
                elif nx == "c" and i + 2 < n and p[i + 2].isalpha():
                    i += 3
                elif nx.isdigit() and nx != "0":
                    m = re.match(r"\d+", p[i + 1:])
                    i += 1 + len(m.group())
                elif nx == "k" and p[i + 2:i + 3] == "<":
                    j = p.find(">", i)
                    i = j + 1 if j > 0 else i + 2
                else:
                    i += 2
                atom()
                continue
            if c == "[":
                j = _class_end(p, i)
                classes.append((i, j))
                if re.search(r"(?<!\\)(?:\\\\)*\\B", p[i + 1:j - 1]):
                    st.unparsed = True  # regexp-tree rejects \B inside a class
                i = j
                atom()
                continue
            if c == "(":
                kind = "cap"
                if p.startswith("(?:", i):
                    kind, i = "nc", i + 3
                elif p.startswith("(?=", i) or p.startswith("(?!", i):
                    kind, i = "look", i + 3
                elif p.startswith("(?<=", i) or p.startswith("(?<!", i):
                    kind, i = "lookbehind", i + 4
                elif p.startswith("(?<", i):
                    j = p.find(">", i)
                    name = p[i + 3:j] if j > 0 else ""
                    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
                        raise ValueError(f"이름 있는 그룹 이름이 올바르지 않습니다: {name!r}")
                    if name in st.named:
                        raise ValueError(f"그룹 이름 중복: {name}")
                    st.named.append(name)
                    st.groups += 1
                    kind, i = "cap", j + 1
                elif p.startswith("(?", i):
                    raise ValueError(f"JS가 모르는 그룹 문법 {p[i:i + 4]!r}(인라인 플래그 (?i) 등은 JS에 없음)")
                else:
                    st.groups += 1
                    i += 1
                stack.append([0, None, kind, False])
                continue
            if c == ")":
                if len(stack) == 1:
                    raise ValueError(") 짝이 없습니다")
                inner, _, kind, _ = stack.pop()
                outer = stack[-1]
                outer[0] = max(outer[0], inner)
                outer[1] = None if kind == "lookbehind" else inner
                outer[3] = kind == "look"
                i += 1
                continue
            if c == "|":
                stack[-1][1] = None
                i += 1
                continue
            if c in "^$":
                stack[-1][1] = None
                i += 1
                continue
            if c in "*+?":
                i = quantify(i + 1)
                continue
            if c == "{":
                m = QUANT_RE.match(p, i)
                if m:
                    lo, hi = int(m.group(1)), m.group(3)
                    if m.group(2) and hi and int(hi) < lo:
                        raise ValueError(f"{{{lo},{hi}}} 범위가 거꾸로입니다")
                    i = quantify(m.end())
                    continue
                st.notes.append("짝 안 맞는 { 는 글자로 취급됩니다(JS Annex B). 의도라면 \\{ 로 쓰는 편이 읽기 쉽습니다")
                atom()
                i += 1
                continue
            if c == "}" or c == "]":
                atom()
                i += 1
                continue
            atom()
            i += 1
        if len(stack) != 1:
            raise ValueError("( 가 닫히지 않았습니다")
        if st.named:  # with any named group, \k must name a defined group (V8)
            for m in re.finditer(r"(?<!\\)(?:\\\\)*\\k(<([^>]*)>)?", p):
                if any(a < m.start() < b for a, b in classes):
                    raise ValueError("이름 있는 그룹이 있으면 [ ] 안에 \\k를 쓸 수 없습니다")
                if m.group(2) not in st.named:
                    raise ValueError(f"\\k 뒤에 정의된 그룹 이름이 필요합니다: {m.group()}")
        st.star_height = stack[0][0]
    except ValueError as e:
        st.ok, st.error = False, str(e)
    if st.ok:
        st.notes += lint(p)
    return st


CLASS_ESC = {"d", "D", "w", "W", "s", "S"}


def _class_end(p: str, i: int) -> int:
    """Index after the ']' closing the class at p[i]; checks range order like V8."""
    n, j = len(p), i + 1
    if j < n and p[j] == "^":
        j += 1
    while j < n and p[j] != "]":
        j += 2 if p[j] == "\\" else 1
    if j >= n:
        raise ValueError("[ 문자 집합이 닫히지 않았습니다")
    _check_ranges(p[i + 1:j])
    return j + 1


def _check_ranges(body: str) -> None:
    toks, k = [], 0
    if body.startswith("^"):
        k = 1
    while k < len(body):
        if body[k] == "\\" and k + 1 < len(body):
            e = body[k + 1]
            m = re.match(r"u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}", body[k + 1:])
            if m:
                toks.append(("c", int(m.group()[1:], 16))); k += 1 + len(m.group())
            elif e in CLASS_ESC:
                toks.append(("e", None)); k += 2
            else:
                toks.append(("c", {"n": 10, "t": 9, "r": 13, "f": 12, "v": 11, "b": 8, "0": 0}.get(e, ord(e)))); k += 2
        else:
            toks.append(("-" if body[k] == "-" else "c", ord(body[k]))); k += 1
    t = 0
    while t < len(toks):
        if t + 2 < len(toks) and toks[t + 1][0] == "-" and toks[t][0] != "-":
            a, b = toks[t], toks[t + 2]
            if a[0] == "c" and b[0] in "c-" and a[1] > b[1]:
                raise ValueError(f"문자 범위가 거꾸로입니다: {chr(a[1])}-{chr(b[1])}")
            t += 3
        else:
            t += 1


RE2_MAX_REPEAT = 1000


def re2_issues(p: str, groups: int = 9) -> list[str]:
    """JS syntax the server's RE2 engine does not accept. Lorebook regex triggers run there, and a
    pattern RE2 cannot parse is ignored, so the trigger never fires (Comet, 2026-10-17)."""
    out, n, i = [], len(p), 0
    while i < n:
        c = p[i]
        if c == "\\" and i + 1 < n:
            e = p[i + 1]
            if e in "123456789" and int(e) <= groups:
                out.append(f"역참조 \\{e}")
            elif e == "k" and p.startswith("<", i + 2):
                out.append("이름 역참조 \\k<…>")
            i += 2
            continue
        if c == "[":
            # JS reads `[^]` (any char) and `[]` (nothing); RE2 reads the `]` as a literal and finds no close
            for tok in ("[^]", "[]"):
                if p.startswith(tok, i):
                    out.append(f"{tok} (RE2는 첫 ] 를 글자로 읽어 집합이 닫히지 않음. 아무 글자는 [\\s\\S])")
                    i += len(tok)
                    break
            else:
                try:
                    i = _class_end(p, i)
                except ValueError:
                    i = n
            continue
        if c == "(":
            for tok, name in (("(?=", "앞보기 (?=…)"), ("(?!", "부정 앞보기 (?!…)"),
                              ("(?<=", "뒤보기 (?<=…)"), ("(?<!", "부정 뒤보기 (?<!…)")):
                if p.startswith(tok, i):
                    out.append(name)
                    break
        if c == "{":
            m = re.match(r"\{(\d+)(?:,(\d*))?\}", p[i:])
            if m and max(int(m.group(1)), int(m.group(2) or 0)) > RE2_MAX_REPEAT:
                out.append(f"반복 횟수 {m.group()} (RE2 최대 {RE2_MAX_REPEAT})")
        i += 1
    return list(dict.fromkeys(out))


def lint(p: str) -> list[str]:
    """Flavor traps: the pattern means something other than a Python/PCRE reader expects."""
    out = []
    if re.search(r"(?<!\\)\\w", p):
        out.append("\\w는 JS에서 [A-Za-z0-9_]뿐이라 한글을 못 잡습니다. 한글은 [가-힣]")
    if re.search(r"(?<![\\\[])\.(\*|\+)(?!\?)", p):
        out.append(".* / .+ 는 욕심쟁이라 한 줄 안의 마지막 짝까지 먹습니다. 태그 하나씩이면 .*? (게으른)로."
                   " 또 . 은 줄바꿈을 못 넘습니다(s 플래그 없음): 여러 줄이면 [\\s\\S]*?")
    if re.search(r"\(\?P<", p):
        out.append("(?P<이름>…)은 Python 문법. JS는 (?<이름>…)")
    if re.search(r"\\[AZz]", p):
        out.append("\\A \\Z \\z는 JS에 없습니다(글자 A·Z로 취급). 문자열 처음·끝은 ^ $")
    if re.search(r"\\p\{", p):
        out.append("\\p{…}는 u 플래그가 있어야 하는데 코멧은 u를 안 붙입니다(글자 p로 취급)")
    if p.startswith("^") or p.endswith("$") and not p.endswith("\\$"):
        out.append("^ $는 m 플래그가 없어 '텍스트 전체'의 처음·끝입니다(줄마다 아님)")
    if re.search(r"\s\|\S|\S\|\s", p):
        out.append("| 앞뒤 공백도 글자입니다(가이드북: 파이프 앞뒤로 띄어쓰지 말 것)")
    return out


# ---------------------------------------------------------------- exact JS execution via node

JS_RUNNER = r"""
const inp = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const steps = [], step = (from, to, text, error) => steps.push({from, to, text, error});
const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function vo(e, t, safe, r = 0, a = false) {          // site's macro engine, same control flow
  if (r >= t.length) return e;
  const n = t[r];
  if (n.type === "str_str") { const o = e.replace(new RegExp(esc(n.from), "g"), n.to); return a ? o : (step(r, r, o), vo(o, t, safe, r + 1, false)); }
  try {
    const o = new RegExp(n.from, "g");
    if (n.type === "regex_str") { const l = safe[r] ? e.replace(o, n.to) : e; return a ? l : (step(r, r, l), vo(l, t, safe, r + 1, false)); }
    let i = r + 1; while (i < t.length && t[i].type === "regex_regex") i++;
    const s = safe[r] ? e.replace(o, (l, ...c) => { const d = c.slice(0, -2);
      return d.length == 0 ? `${n.prefix}${vo(l, t, safe, r + 1, true)}${n.suffix}` : `${n.prefix}${vo(d[0], t, safe, r + 1, true)}${n.suffix}`; }) : e;
    return a ? s : (step(r, Math.min(i, t.length - 1), s), vo(s, t, safe, i + 1, false));
  } catch (err) { return a ? e : (step(r, r, e, String(err.message)), vo(e, t, safe, r + 1, false)); }
}
function Z3(e, t) {                                // site's {user} substitution with particle fix
  if (e.length < 6 || t === undefined) return e;
  const r = (o, i) => { if (o.length == 0) return false; const s = o.slice(-1);
    if (/[0-9]/.test(s)) return [1, 1, 0, 1, 0, 0, 1, 1, 1, 0][parseInt(s)] == 1;
    if (/[가-힣]/.test(s)) { const l = s.charCodeAt(0) - 44032; return l < 0 || 11172 <= l ? false : i ? l % 28 != 0 && l % 28 != 8 : l % 28 > 0; }
    return false; };
  const a = [["으로","로"],["이라","라"],["이랑","랑"],["이다","다"],["이여","여"],["이","가"],["과","와"],["은","는"],["을","를"],["아","야"],["으로","로"]];
  let n = "";
  for (let o = 0; o < e.length; o++) {
    if (e[o] != "{" || e.slice(o, o + 6) != "{user}") { n += e[o]; continue; }
    n += t; o += 6;
    for (const i of a) { const s = i[0] == "으로";
      if (e.slice(o, o + i[0].length) == i[0] || e.slice(o, o + i[1].length) == i[1]) { r(t, s) ? (n += i[0], o += i[0].length) : (n += i[1], o += i[1].length); break; } }
    o -= 1;
  }
  return n;
}
const out = {};
if (inp.op === "compile") {
  out.results = inp.patterns.map(p => { try { new RegExp(p, "g"); return {ok: true, groups: new RegExp(p + "|").exec("").length - 1}; } catch (e) { return {ok: false, error: e.message}; } });
} else if (inp.op === "match") {
  const r = new RegExp(inp.pattern, "g"); out.matches = [...inp.text.matchAll(r)].map(m => ({index: m.index, match: m[0], groups: m.slice(1)}));
  out.test = new RegExp(inp.pattern).test(inp.text);
  if (inp.to !== undefined) out.replaced = inp.text.replace(r, inp.to);
} else if (inp.op === "macros") {
  out.macros = vo(inp.text, inp.macros, inp.safe);
  let c = Z3(out.macros, inp.user);
  if (inp.turn !== undefined) c = c.replace(/\{turn\}/g, `${inp.turn}`);
  out.result = c; out.steps = steps;
}
process.stdout.write(JSON.stringify(out));
"""


def node_bin() -> str | None:
    return shutil.which("node")


def run_js(payload: dict) -> dict:
    nb = node_bin()
    if not nb:
        raise RuntimeError("node가 없어 JS로 정확히 실행할 수 없습니다")
    r = subprocess.run([nb, "-e", JS_RUNNER], input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                       text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "node 실행 실패")
    return json.loads(r.stdout)


def js_compile(patterns: list[str]) -> list[dict] | None:
    try:
        return run_js({"op": "compile", "patterns": patterns})["results"]
    except RuntimeError:
        return None


def run_macros(text: str, macros: list[dict], user: str | None = None, turn: int | None = None) -> dict:
    """Exactly what the chat shows before markdown: the site's vo() + {user} + {turn}."""
    safe = [m.get("type") == "str_str" or analyze(m.get("from", "")).safe for m in macros]
    return run_js({"op": "macros", "text": text, "macros": macros, "safe": safe, "user": user, "turn": turn})


def check(p: str) -> tuple[Structure, dict | None]:
    """Static analysis + (when node exists) the JS engine's own verdict."""
    st = analyze(p)
    js = js_compile([p])
    js = js[0] if js else None
    if js is not None and js["ok"] != st.ok:
        if js["ok"]:
            st.notes.append(f"JS는 이 정규식을 받지만 정적 분석이 거부했습니다({st.error}) — 위험 판정으로 처리됩니다")
        else:
            st.ok, st.error = False, js["error"]
    return st, js


# ---------------------------------------------------------------- generators

def escape(s: str) -> str:
    return re.sub(r"[.*+?^${}()|[\]\\/]", lambda m: "\\" + m.group(), s)


def alternation(words: list[str], capture=True) -> str:
    """(긴말|짧은말) — longest first so a word never loses to its own prefix."""
    ws = sorted(dict.fromkeys(words), key=lambda w: (-len(w), w))
    body = "|".join(escape(w) for w in ws)
    return f"({body})" if capture else f"(?:{body})"


def int_range(lo: int, hi: int, capture=True) -> str:
    """Regex matching exactly the integers lo..hi (no leading zeros), with zero quantifiers."""
    if lo < 0 or hi < lo:
        raise ValueError("0 ≤ 시작 ≤ 끝")
    parts = []
    for ln in range(len(str(lo)), len(str(hi)) + 1):
        a = max(lo, 10 ** (ln - 1) if ln > 1 else 0)
        b = min(hi, 10 ** ln - 1)
        if a <= b:
            parts += _same_len(str(a), str(b))
    # longer alternatives first: JS alternation is ordered and a bare match on '1' would win over '10'
    parts.sort(key=lambda s: -_digits(s))
    body = "|".join(parts)
    return f"({body})" if capture else f"(?:{body})"


def _digits(s: str) -> int:
    return len(re.findall(r"\[[^\]]*\]|\d", s))


def _same_len(a: str, b: str) -> list[str]:
    if a == b:
        return [a]
    if len(a) == 1:
        return [f"[{a}-{b}]" if int(b) - int(a) > 1 else f"[{a}{b}]"]
    if a[0] == b[0]:
        return [a[0] + x for x in _same_len(a[1:], b[1:])]
    out = []
    rest = len(a) - 1
    if a[1:] != "0" * rest:
        out += [a[0] + x for x in _same_len(a[1:], "9" * rest)]
        first = int(a[0]) + 1
    else:
        first = int(a[0])
    if b[1:] != "9" * rest:
        last = int(b[0]) - 1
        tail = [b[0] + x for x in _same_len("0" * rest, b[1:])]
    else:
        last = int(b[0])
        tail = []
    if first <= last:
        d = str(first) if first == last else f"[{first}-{last}]" if last - first > 1 else f"[{first}{last}]"
        out.append(d + "[0-9]" * rest)
    return out + tail


def image_macros(tag: str, names: list[str], lo: int, hi: int, url: str, default_no: int | None = None,
                 anonymous_url: str | None = None, spaces=True) -> list[dict]:
    """The guide's four-step image macro set: exact → fix number → anonymous → hide.
    url uses $1 (name) and $2 (number)."""
    s = r"\s*" if spaces else ""
    open_, close = r"\{\{" + s + escape(tag) + "::", s + r"\}\}"
    nm, no = alternation(names), int_range(lo, hi)
    md = lambda u: f"![]({u})"
    out = [{"type": "regex_str", "from": f"{open_}{nm}/{no}{close}", "to": md(url)}]
    if default_no is not None:
        out.append({"type": "regex_str", "from": f"{open_}{nm}/(.*?){close}", "to": md(url.replace("$2", str(default_no)))})
    if anonymous_url:
        out.append({"type": "regex_str", "from": f"{open_}(.*?)/{no}{close}", "to": md(anonymous_url)})
    out.append({"type": "regex_str", "from": f"{open_}(.*?){close}", "to": ""})
    return out


# ---------------------------------------------------------------- JSON file validators (appendix: JSON 파일 규격)

def _nonspace_len(s: str) -> int:
    return len(re.sub(r"\s", "", s))


def check_macros(data) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    if not isinstance(data, list) or not 1 <= len(data) <= 1000:
        return ["매크로 파일은 길이 1~1000의 배열"], warns
    pats = [m.get("from", "") for m in data if isinstance(m, dict) and m.get("type") in ("regex_str", "regex_regex")]
    js = js_compile(pats) if pats else []
    k = 0
    for i, m in enumerate(data):
        w = f"[{i}]"
        if not isinstance(m, dict) or m.get("type") not in ("str_str", "regex_str", "regex_regex"):
            errs.append(f"{w} type은 str_str|regex_str|regex_regex")
            continue
        t = m["type"]
        need = {"str_str": ("from", "to"), "regex_str": ("from", "to"), "regex_regex": ("from", "prefix", "suffix")}[t]
        extra = set(m) - {"type", *need}
        if extra:
            errs.append(f"{w} {t}에 없는 키 {sorted(extra)}")
        for key in need:
            if not isinstance(m.get(key), str):
                errs.append(f"{w} {key}는 문자열")
        if errs and errs[-1].startswith(w):
            continue
        lim = {"from": 10000, "to": 10000 if t == "str_str" else 30000, "prefix": 30000, "suffix": 30000}
        for key in need:
            if len(m[key]) > lim[key]:
                errs.append(f"{w} {key} {len(m[key])}자 > {lim[key]}")
        if not m["from"]:
            errs.append(f"{w} from이 비었습니다")
        if t != "str_str":
            st = analyze(m["from"])
            j = js[k] if js else None
            k += 1
            if j is not None and not j["ok"]:
                errs.append(f"{w} 정규식 오류(JS): {j['error']}")
            elif not st.safe:
                errs.append(f"{w} {st.verdict()} — 사이트가 거부하고, 실행 때도 건너뜁니다: {m['from']}")
            for note in st.notes:
                warns.append(f"{w} {note}")
            if t == "regex_regex":
                if i + 1 >= len(data):
                    warns.append(f"{w} 체인을 받을 매크로가 없어 매치가 prefix+캡처+suffix로만 바뀝니다")
                elif data[i + 1].get("type") != "regex_regex":
                    warns.append(f"{w} 다음 매크로 [{i + 1}]은 이 체인의 캡처 안에서만 돌고, 전체 글에는 적용되지 않습니다(의도한 구성이면 무시)")
                if st.ok and st.groups == 0:
                    warns.append(f"{w} 캡처 그룹이 없어 매치 전체가 다음 매크로로 넘어갑니다")
            if t == "regex_str" and st.ok:
                refs = [int(x) for x in re.findall(r"\$(\d{1,2})", m["to"])]
                bad = [r for r in refs if r > st.groups]
                if bad:
                    warns.append(f"{w} 대체식의 ${bad[0]}에 해당하는 캡처 그룹이 없습니다(글자 그대로 남음)")
                if re.search(r"\\\d", m["to"]):
                    warns.append(f"{w} 대체식의 \\1은 JS에서 글자 그대로입니다. $1을 쓰세요")
        if t == "str_str" and re.search(r"\$[&`'$\d<]", m["to"]):
            warns.append(f"{w} 일대일 치환의 대체 키워드도 $&·$1·$$를 해석합니다. 글자 $를 원하면 $$")
    return errs, warns


def check_lorebooks(data) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    if not isinstance(data, list) or not 1 <= len(data) <= 500:
        return ["로어북 파일은 길이 1~500의 배열"], warns
    for i, lb in enumerate(data):
        w = f"[{i}]"
        if not isinstance(lb, dict):
            errs.append(f"{w} 객체여야 합니다")
            continue
        title, content = lb.get("title"), lb.get("content")
        if not isinstance(title, str) or not 1 <= len(title) <= 20:
            errs.append(f"{w} title 1~20자")
        if not isinstance(content, str) or not 1 <= len(content) <= 7500 or _nonspace_len(content or "") > 5000:
            errs.append(f"{w} content 1~7500자, 공백 제외 5000자 이내")
        if not isinstance(lb.get("useDefault"), bool):
            errs.append(f"{w} useDefault는 true|false")
        trs = lb.get("triggers")
        if not isinstance(trs, list) or len(trs) > 30:
            errs.append(f"{w} triggers는 길이 0~30 배열")
            continue
        if lb.get("useDefault") is False and not trs:
            errs.append(f"{w} useDefault가 false면 트리거가 1개 이상 필요")
        for j, t in enumerate(trs):
            tw = f"{w}.triggers[{j}]"
            typ = t.get("type") if isinstance(t, dict) else None
            if typ not in ("keyword", "regex", "turn", "dice"):
                errs.append(f"{tw} type은 keyword|regex|turn|dice")
                continue
            if t.get("action") not in ("apply", "pass", "block"):
                errs.append(f"{tw} action은 apply(발동)|pass(통과)|block(차단)")
            if typ in ("keyword", "regex"):
                key = "keyword" if typ == "keyword" else "regex"
                v = t.get(key)
                if not isinstance(v, str) or not 1 <= len(v) <= 300:
                    errs.append(f"{tw} {key} 1~300자")
                if not (t.get("watchPrevChat") is True or t.get("watchUserChat") is True):
                    errs.append(f"{tw} watchPrevChat·watchUserChat 중 하나는 true")
                if typ == "regex" and isinstance(v, str) and v:
                    st, js = check(v)
                    if not st.ok:
                        errs.append(f"{tw} 정규식 오류: {st.error}")
                    else:
                        errs += [f"{tw} 서버 정규식 엔진 RE2가 못 읽는 문법: {x} — 편집기는 받지만 서버가 이 정규식을 무시해"
                                 " 트리거가 발동하지 않습니다" for x in re2_issues(v, st.groups)]
                    warns += [f"{tw} {x}" for x in st.notes if "욕심" not in x]
            else:
                for key, lo in (("from", 1), ("to", None), ("remainder", 0)) + ((("diceIndex", 1),) if typ == "dice" else ()):
                    v = t.get(key)
                    lo_ = t.get("from", 1) if lo is None else lo
                    if not isinstance(v, int) or isinstance(v, bool) or not (lo_ <= v < 10 ** 9):
                        errs.append(f"{tw} {key}는 {lo_} 이상 10억 미만 정수")
    return errs, warns
