"""Comet template block scripts <-> a small indentation-based text language.

The platform stores a script as a linked list of statement nodes ({"type": ..., "cont": next})
with value and condition sub-trees. Agents write the text form; this module compiles it to the
exact node shapes the editor's zod schema accepts, and decompiles stored nodes back to text.

Text form, one statement per line (or several joined by ';'):

    show | hide                      state scripts
    x = e | y = e | width = e | height = e | opacity = e
    @local = e                       any script
    $system = e                      action scripts, system variables only
    prev | next | play e | stop_audio | volume e      action scripts
    if c: / elif c: / else:          blocks by indentation
    pass                             empty statement
    return e                         value scripts, last top-level line

Values:  12  -3.5  "text"  true  false  $var  ${var with space}  @local  timer  mouse_x  mouse_y
         a + b  a - b  a * b  a / b  a .. b (join)  -a
         len(a) sin(a) cos(a) round(a) sqrt(a) img("key") audio("key") join(a, b)
Conditions: a == b  a != b  a < b  a <= b  a > b  a >= b  c and c  c or c  not c
            true  false  is_first  is_last
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CALC_OPS = {"+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV"}
CALC_SYM = {v: k for k, v in CALC_OPS.items()}
CMP_OPS = {"==": "EQUAL", "!=": "NOT_EQUAL", "<": "LESS", "<=": "LESS_OR_EQUAL", ">": "GREATER", ">=": "GREATER_OR_EQUAL"}
CMP_SYM = {v: k for k, v in CMP_OPS.items()}
FUNCS = {"sin": "SIN", "cos": "COS", "round": "ROUND", "sqrt": "SQRT"}
FUNC_SYM = {v: k for k, v in FUNCS.items()}
SET_STATE = {"x": "SET_X", "y": "SET_Y", "width": "SET_WIDTH", "height": "SET_HEIGHT", "opacity": "SET_OPACITY"}
STATE_SYM = {v: k for k, v in SET_STATE.items()}

# Statement node types each script context keeps (mirrors the editor's sanitizer).
ALLOWED = {
    "state": {"SET_LOCAL", "IF", "IF_ELSE", "SHOW", "HIDE", "SET_X", "SET_Y", "SET_WIDTH", "SET_HEIGHT", "SET_OPACITY"},
    "action": {"SET_LOCAL", "IF", "IF_ELSE", "SET_GLOBAL", "GO_NEXT", "GO_PREV", "PLAY_AUDIO", "STOP_AUDIO", "SET_VOLUME"},
    "value": {"SET_LOCAL", "IF", "IF_ELSE"},
}
CONTEXT_KO = {"state": "state(상태)", "action": "action(클릭·트리거)", "value": "value(표시값)"}
VALUE_TYPES = {"CONST", "VAR", "LOCAL_VAR", "LENGTH", "CALC", "JOIN", "FUNC", "TIMER", "GET_IMAGE_STORAGE", "GET_AUDIO_STORAGE", "MOUSE_X", "MOUSE_Y"}
COND_TYPES = {"TRUE", "FALSE", "COMPARE", "LOGIC_OP", "NOT", "IS_FIRST", "IS_LAST"}
STMT_TYPES = set().union(*ALLOWED.values()) | {"NULL"}

NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
NAME_CHARS = r"[^\s()\[\]{}+\-*/=<>!,:;.\"'#$@]"
SIMPLE_NAME_RE = re.compile(rf"^{NAME_CHARS}+$")
KEYWORDS = {"if", "elif", "else", "and", "or", "not", "true", "false", "timer", "mouse_x", "mouse_y",
            "is_first", "is_last", "return", "show", "hide", "prev", "next", "stop_audio", "play",
            "volume", "pass", "len", "join", "img", "audio", *FUNCS, *SET_STATE}


class ScriptError(ValueError):
    """Compile error with a 1-based line number inside the script text."""

    def __init__(self, msg: str, line: int | None = None):
        self.line = line
        super().__init__(f"{line}행: {msg}" if line else msg)


# ---------------------------------------------------------------- lexer

@dataclass
class Tok:
    kind: str  # num str var local name op eof
    text: str
    line: int


TOKEN_RE = re.compile(
    r"""
    (?P<ws>[ \t]+)
  | (?P<comment>\#[^\n]*)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
  | (?P<var>\$\{[^}\n]+\}|\$""" + NAME_CHARS + r"""+)
  | (?P<local>@\{[^}\n]+\}|@""" + NAME_CHARS + r"""+)
  | (?P<op>\.\.|==|!=|<=|>=|[-+*/<>(),=:])
  | (?P<name>""" + NAME_CHARS + r"""+)
    """,
    re.X,
)


def _unquote(s: str) -> str:
    body = s[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


def lex_line(text: str, line: int) -> list[Tok]:
    toks, pos = [], 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise ScriptError(f"알 수 없는 글자 {text[pos]!r}", line)
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        val = m.group(kind)
        if kind == "str":
            val = _unquote(val)
        elif kind in ("var", "local"):
            val = val[2:-1] if val[1] == "{" else val[1:]
        toks.append(Tok(kind, val, line))
    toks.append(Tok("eof", "", line))
    return toks


# ---------------------------------------------------------------- expression parser (generic tree)

class ExprParser:
    """Pratt parser into a generic tree; typing into value/condition happens afterwards."""

    BIN = {"or": 1, "and": 2, "==": 4, "!=": 4, "<": 4, "<=": 4, ">": 4, ">=": 4, "..": 5, "+": 6, "-": 6, "*": 7, "/": 7}

    def __init__(self, toks: list[Tok]):
        self.toks, self.i = toks, 0

    def peek(self) -> Tok:
        return self.toks[self.i]

    def take(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, text: str) -> Tok:
        t = self.take()
        if t.text != text or t.kind not in ("op", "name"):
            raise ScriptError(f"'{text}'이(가) 필요한데 '{t.text or '줄 끝'}'이(가) 왔습니다", t.line)
        return t

    def _binop(self, t: Tok) -> str | None:
        if t.kind == "op" and t.text in self.BIN:
            return t.text
        if t.kind == "name" and t.text in ("and", "or"):
            return t.text
        return None

    def expr(self, min_bp: int = 0):
        left = self.prefix()
        while True:
            t = self.peek()
            op = self._binop(t)
            if op is None or self.BIN[op] < min_bp:
                return left
            self.take()
            bp = self.BIN[op]
            if bp == 4:  # comparisons do not chain
                right = self.expr(bp + 1)
                left = ("cmp", op, left, right, t.line)
                if self._binop(self.peek()) in CMP_OPS:
                    raise ScriptError("비교식은 이어 붙일 수 없습니다. and로 나누세요", t.line)
            else:
                left = ("bin", op, left, self.expr(bp + 1), t.line)

    def prefix(self):
        t = self.take()
        if t.kind == "num":
            return ("num", t.text, t.line)
        if t.kind == "str":
            return ("str", t.text, t.line)
        if t.kind == "var":
            return ("var", t.text, t.line)
        if t.kind == "local":
            return ("local", t.text, t.line)
        if t.kind == "op" and t.text == "(":
            e = self.expr()
            self.expect(")")
            return ("paren", e, t.line)
        if t.kind == "op" and t.text == "-":
            operand = self.expr(8)
            if operand[0] == "num":
                return ("num", "-" + operand[1], t.line)
            return ("neg", operand, t.line)
        if t.kind == "name":
            w = t.text
            if w == "not":
                return ("not", self.expr(3), t.line)
            if w in ("true", "false", "timer", "mouse_x", "mouse_y", "is_first", "is_last"):
                return ("word", w, t.line)
            if w in ("len", "img", "audio", "join", *FUNCS):
                self.expect("(")
                args = [self.expr()]
                while self.peek().text == "," and self.peek().kind == "op":
                    self.take()
                    args.append(self.expr())
                self.expect(")")
                want = 2 if w == "join" else 1
                if len(args) != want:
                    raise ScriptError(f"{w}()는 인자 {want}개를 받습니다", t.line)
                return ("call", w, args, t.line)
            raise ScriptError(f"모르는 이름 '{w}'. 변수는 ${w}(템플릿 변수) 또는 @{w}(지역 변수)로 씁니다", t.line)
        raise ScriptError(f"값이 와야 할 자리에 '{t.text or '줄 끝'}'", t.line)


def _strip(e):
    while e[0] == "paren":
        e = e[1]
    return e


def to_value(e) -> dict:
    e = _strip(e)
    k = e[0]
    if k == "num":
        return {"type": "CONST", "value": e[1]}
    if k == "str":
        return {"type": "CONST", "value": e[1]}
    if k == "var":
        return {"type": "VAR", "varName": e[1]}
    if k == "local":
        return {"type": "LOCAL_VAR", "localVarName": e[1]}
    if k == "word":
        w = e[1]
        if w in ("true", "false"):
            return {"type": "CONST", "value": w}
        if w == "timer":
            return {"type": "TIMER"}
        if w in ("mouse_x", "mouse_y"):
            return {"type": w.upper()}
        raise ScriptError(f"{w}는 조건이라 값 자리에 올 수 없습니다", e[2])
    if k == "neg":
        return {"type": "CALC", "left": {"type": "CONST", "value": "0"}, "right": to_value(e[1]), "op": "SUB"}
    if k == "bin":
        op = e[1]
        if op in CALC_OPS:
            return {"type": "CALC", "left": to_value(e[2]), "right": to_value(e[3]), "op": CALC_OPS[op]}
        if op == "..":
            return {"type": "JOIN", "left": to_value(e[2]), "right": to_value(e[3])}
        raise ScriptError(f"'{op}'는 조건 연산자라 값을 만들 수 없습니다", e[4])
    if k == "call":
        w, args = e[1], e[2]
        if w == "len":
            return {"type": "LENGTH", "value": to_value(args[0])}
        if w == "img":
            return {"type": "GET_IMAGE_STORAGE", "value": to_value(args[0])}
        if w == "audio":
            return {"type": "GET_AUDIO_STORAGE", "value": to_value(args[0])}
        if w == "join":
            return {"type": "JOIN", "left": to_value(args[0]), "right": to_value(args[1])}
        return {"type": "FUNC", "funcType": FUNCS[w], "value": to_value(args[0])}
    if k in ("cmp", "not"):
        raise ScriptError("조건식은 값 자리에 올 수 없습니다(플랫폼에 조건→값 변환 블록이 없음)", e[-1])
    raise ScriptError(f"해석할 수 없는 값 {k}")


def to_cond(e) -> dict:
    e = _strip(e)
    k = e[0]
    if k == "word" and e[1] in ("true", "false", "is_first", "is_last"):
        return {"type": e[1].upper()}
    if k == "not":
        return {"type": "NOT", "cond": to_cond(e[1])}
    if k == "bin" and e[1] in ("and", "or"):
        return {"type": "LOGIC_OP", "op": e[1].upper(), "left": to_cond(e[2]), "right": to_cond(e[3])}
    if k == "cmp":
        return {"type": "COMPARE", "op": CMP_OPS[e[1]], "left": to_value(e[2]), "right": to_value(e[3])}
    line = e[-1] if isinstance(e[-1], int) else None
    raise ScriptError("조건 자리에는 비교식(예: $열림 == true)이나 true/false/is_first/is_last가 와야 합니다", line)


# ---------------------------------------------------------------- statements

@dataclass
class Line:
    indent: int
    toks: list[Tok]
    no: int


def _split_lines(src: str) -> list[Line]:
    out = []
    for no, raw in enumerate(src.replace("\r\n", "\n").split("\n"), 1):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ScriptError("들여쓰기에 탭 대신 공백을 쓰세요", no)
        indent = len(raw) - len(raw.lstrip(" "))
        # ';' joins simple statements on one line; split outside strings before lexing
        for part in _split_semicolons(raw.strip()):
            if part.strip():
                out.append(Line(indent, lex_line(part.strip(), no), no))
    return out


def _split_semicolons(s: str) -> list[str]:
    parts, cur, q = [], "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q and not cur.endswith("\\" + q):
                q = None
        elif ch in "\"'":
            q = ch
            cur += ch
        elif ch == ";":
            parts.append(cur)
            cur = ""
        elif ch == "#":
            break
        else:
            cur += ch
    parts.append(cur)
    return parts


class Compiler:
    def __init__(self, src: str, context: str):
        if context not in ALLOWED:
            raise ValueError(context)
        self.lines = _split_lines(src)
        self.i = 0
        self.context = context
        self.ret = None

    def compile(self) -> tuple[dict, dict | None]:
        stmts = self.block(self.lines[0].indent if self.lines else 0, top=True)
        if self.i < len(self.lines):
            ln = self.lines[self.i]
            raise ScriptError("들여쓰기가 맞지 않습니다", ln.no)
        return _chain(stmts), self.ret

    def block(self, indent: int, top: bool = False) -> list[dict]:
        out = []
        while self.i < len(self.lines):
            ln = self.lines[self.i]
            if ln.indent < indent:
                break
            if ln.indent > indent:
                raise ScriptError("예상하지 못한 들여쓰기", ln.no)
            if self.ret is not None and top:
                raise ScriptError("return 뒤에는 문장을 둘 수 없습니다", ln.no)
            node = self.statement(ln, top)
            if node is not None:
                out.append(node)
        return out

    def _sub_block(self, header: Line) -> list[dict]:
        self.i += 1
        if self.i >= len(self.lines) or self.lines[self.i].indent <= header.indent:
            raise ScriptError("':' 뒤에 들여쓴 블록이 필요합니다(비우려면 pass)", header.no)
        return self.block(self.lines[self.i].indent)

    def _allow(self, typ: str, ln: Line):
        if typ not in ALLOWED[self.context]:
            where = [CONTEXT_KO[c] for c, s in ALLOWED.items() if typ in s]
            raise ScriptError(f"{typ}는 {CONTEXT_KO[self.context]} 스크립트에서 쓸 수 없습니다(쓸 수 있는 곳: {', '.join(where)})", ln.no)

    def _expr_rest(self, toks: list[Tok], start: int, ln: Line):
        p = ExprParser(toks[start:])
        e = p.expr()
        t = p.peek()
        if t.kind != "eof" and not (t.kind == "op" and t.text == ":"):
            raise ScriptError(f"식 뒤에 남은 '{t.text}'", ln.no)
        return e, p

    def statement(self, ln: Line, top: bool):
        t = ln.toks
        first = t[0]
        w = first.text if first.kind == "name" else None

        if w == "if":
            return self._if(ln)
        if w in ("elif", "else"):
            raise ScriptError(f"{w}에 짝이 되는 if가 없습니다", ln.no)

        self.i += 1
        if w == "pass":
            return None
        if w == "return":
            if self.context != "value":
                raise ScriptError("return은 표시값(value) 스크립트에서만 씁니다", ln.no)
            if not top:
                raise ScriptError("return은 맨 바깥 마지막 줄에만 둘 수 있습니다(블록 안에서는 @지역변수에 담고 마지막에 return)", ln.no)
            e, _ = self._expr_rest(t, 1, ln)
            self.ret = to_value(e)
            return None
        if w in ("show", "hide", "prev", "next", "stop_audio") and t[1].kind == "eof":
            typ = {"show": "SHOW", "hide": "HIDE", "prev": "GO_PREV", "next": "GO_NEXT", "stop_audio": "STOP_AUDIO"}[w]
            self._allow(typ, ln)
            return {"type": typ}
        if w in ("play", "volume"):
            typ = "PLAY_AUDIO" if w == "play" else "SET_VOLUME"
            self._allow(typ, ln)
            e, _ = self._expr_rest(t, 1, ln)
            return {"type": typ, "value": to_value(e)}
        # assignments
        if len(t) > 2 and t[1].kind == "op" and t[1].text == "=":
            e, _ = self._expr_rest(t, 2, ln)
            val = to_value(e)
            if first.kind == "local":
                return {"type": "SET_LOCAL", "localVarName": first.text, "value": val}
            if first.kind == "var":
                self._allow("SET_GLOBAL", ln)
                return {"type": "SET_GLOBAL", "varName": first.text, "value": val}
            if w in SET_STATE:
                self._allow(SET_STATE[w], ln)
                return {"type": SET_STATE[w], "value": val}
            raise ScriptError(f"'{first.text}'에는 대입할 수 없습니다(x/y/width/height/opacity, @지역, $시스템 변수만)", ln.no)
        if self.context == "value" and top:
            # bare expression as the last line == return
            e, _ = self._expr_rest(t, 0, ln)
            self.ret = to_value(e)
            return None
        raise ScriptError(f"문장을 해석할 수 없습니다: {' '.join(x.text for x in t[:-1])}", ln.no)

    def _if(self, ln: Line):
        cond_e, p = self._expr_rest(ln.toks, 1, ln)
        if not (p.peek().kind == "op" and p.peek().text == ":") or p.toks[p.i + 1].kind != "eof":
            raise ScriptError("if 줄은 ':'로 끝나고, 본문은 다음 줄에 들여써서 씁니다", ln.no)
        cond = to_cond(cond_e)
        body = self._sub_block(ln)
        nxt = self.lines[self.i] if self.i < len(self.lines) else None
        if nxt and nxt.indent == ln.indent and nxt.toks[0].kind == "name" and nxt.toks[0].text in ("elif", "else"):
            if nxt.toks[0].text == "elif":
                else_body = [self._if(nxt)]
            else:
                if not (nxt.toks[1].kind == "op" and nxt.toks[1].text == ":"):
                    raise ScriptError("else 뒤에는 ':'만 옵니다", nxt.no)
                else_body = self._sub_block(nxt)
            self._allow("IF_ELSE", ln)
            return {"type": "IF_ELSE", "cond": cond, "if": _chain(body), "else": _chain(else_body)}
        self._allow("IF", ln)
        return {"type": "IF", "cond": cond, "if": _chain(body)}


def _chain(stmts: list[dict]) -> dict:
    node = {"type": "NULL"}
    for s in reversed(stmts):
        s = dict(s)
        s["cont"] = node
        node = s
    return node


def compile_script(src: str | None, context: str) -> tuple[dict, dict | None]:
    """Return (statement chain, return value or None)."""
    if not src or not src.strip():
        return {"type": "NULL"}, None
    return Compiler(src, context).compile()


def compile_value(src: str | None) -> tuple[dict, dict]:
    """Value script: either a bare expression or statements ending in `return e`."""
    node, ret = compile_script(src, "value")
    if ret is None:
        raise ScriptError("표시값 스크립트에 return(또는 마지막 줄의 식)이 없습니다")
    return node, ret


# ---------------------------------------------------------------- walking & checks

def iter_values(node: dict):
    """Yield every value sub-tree (incl. nested) under a statement chain / cond / value."""
    t = node.get("type")
    if t in VALUE_TYPES:
        yield node
        for k in ("value", "left", "right"):
            if isinstance(node.get(k), dict):
                yield from iter_values(node[k])
        return
    if t in COND_TYPES:
        for k in ("left", "right", "cond"):
            if isinstance(node.get(k), dict):
                yield from iter_values(node[k])
        return
    for k in ("value", "cond", "if", "else", "cont"):
        if isinstance(node.get(k), dict):
            yield from iter_values(node[k])


def iter_stmts(node: dict):
    while node and node.get("type") != "NULL":
        yield node
        for k in ("if", "else"):
            if isinstance(node.get(k), dict):
                yield from iter_stmts(node[k])
        node = node.get("cont")


def validate_tree(kind: str, node, path: str = "") -> list[str]:
    """Structural check of a raw node against the editor schema. kind: stmt | value | cond."""
    errs: list[str] = []
    if not isinstance(node, dict) or "type" not in node:
        return [f"{path}: 노드가 객체가 아님"]
    t = node["type"]
    if kind == "value":
        if t not in VALUE_TYPES:
            return [f"{path}: 값 노드가 아닌 {t}"]
        need = {"CONST": ["value"], "VAR": ["varName"], "LOCAL_VAR": ["localVarName"], "LENGTH": ["value*"],
                "CALC": ["left*", "right*", "op"], "JOIN": ["left*", "right*"], "FUNC": ["funcType", "value*"],
                "GET_IMAGE_STORAGE": ["value*"], "GET_AUDIO_STORAGE": ["value*"]}.get(t, [])
    elif kind == "cond":
        if t not in COND_TYPES:
            return [f"{path}: 조건 노드가 아닌 {t}"]
        need = {"COMPARE": ["op", "left*", "right*"], "LOGIC_OP": ["op", "left#", "right#"], "NOT": ["cond#"]}.get(t, [])
    else:
        if t not in STMT_TYPES:
            return [f"{path}: 문장 노드가 아닌 {t}"]
        if t == "NULL":
            return []
        need = ["cont!"]
        need += {"SET_LOCAL": ["localVarName", "value*"], "SET_GLOBAL": ["varName", "value*"],
                 "IF": ["cond#", "if!"], "IF_ELSE": ["cond#", "if!", "else!"],
                 "PLAY_AUDIO": ["value*"], "SET_VOLUME": ["value*"]}.get(t, [])
        if t in STATE_SYM:
            need.append("value*")
    for f in need:
        name, sub = f.rstrip("*#!"), {"*": "value", "#": "cond", "!": "stmt"}.get(f[-1])
        if name not in node:
            errs.append(f"{path}.{name}: {t}에 필요한 필드가 없음")
        elif sub:
            errs += validate_tree(sub, node[name], f"{path}.{name}")
    if t == "CALC" and node.get("op") not in CALC_SYM:
        errs.append(f"{path}.op: {node.get('op')}")
    if t == "COMPARE" and node.get("op") not in CMP_SYM:
        errs.append(f"{path}.op: {node.get('op')}")
    if t == "LOGIC_OP" and node.get("op") not in ("AND", "OR"):
        errs.append(f"{path}.op: {node.get('op')}")
    if t == "FUNC" and node.get("funcType") not in FUNC_SYM:
        errs.append(f"{path}.funcType: {node.get('funcType')}")
    for k in ("value", "varName", "localVarName"):
        if k in node and isinstance(node[k], (int, float, bool)) and t in ("CONST", "VAR", "LOCAL_VAR", "SET_LOCAL", "SET_GLOBAL"):
            errs.append(f"{path}.{k}: 문자열이어야 함")
    return errs


# ---------------------------------------------------------------- decompiler

def _name(prefix: str, n: str) -> str:
    return f"{prefix}{n}" if SIMPLE_NAME_RE.match(n) and n not in KEYWORDS else f"{prefix}{{{n}}}"


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t") + '"'


VPREC = {"JOIN": 5, "ADD": 6, "SUB": 6, "MUL": 7, "DIV": 7}


def show_value(v: dict, parent_bp: int = 0) -> str:
    t = v["type"]
    if t == "CONST":
        s = v["value"]
        if NUM_RE.match(s) and not (s.startswith("-") and parent_bp >= 8):
            return s
        if s in ("true", "false"):
            return s
        return _quote(s)
    if t == "VAR":
        return _name("$", v["varName"])
    if t == "LOCAL_VAR":
        return _name("@", v["localVarName"])
    if t in ("TIMER", "MOUSE_X", "MOUSE_Y"):
        return t.lower()
    if t == "LENGTH":
        return f"len({show_value(v['value'])})"
    if t == "FUNC":
        return f"{FUNC_SYM[v['funcType']]}({show_value(v['value'])})"
    if t == "GET_IMAGE_STORAGE":
        return f"img({show_value(v['value'])})"
    if t == "GET_AUDIO_STORAGE":
        return f"audio({show_value(v['value'])})"
    if t in ("CALC", "JOIN"):
        key = v["op"] if t == "CALC" else "JOIN"
        if t == "CALC" and key == "SUB" and v["left"] == {"type": "CONST", "value": "0"}:
            s = "-" + show_value(v["right"], 8)
            return f"({s})" if parent_bp >= 8 else s
        bp = VPREC[key]
        sym = ".." if t == "JOIN" else CALC_SYM[key]
        s = f"{show_value(v['left'], bp)} {sym} {show_value(v["right"], bp + 1)}"
        return f"({s})" if bp < parent_bp else s
    raise ScriptError(f"알 수 없는 값 노드 {t}")


def show_cond(c: dict, parent_bp: int = 0) -> str:
    t = c["type"]
    if t in ("TRUE", "FALSE", "IS_FIRST", "IS_LAST"):
        return t.lower()
    if t == "NOT":
        s = f"not {show_cond(c['cond'], 3)}"
        return f"({s})" if parent_bp > 3 else s
    if t == "COMPARE":
        s = f"{show_value(c['left'], 5)} {CMP_SYM[c['op']]} {show_value(c['right'], 5)}"
        return f"({s})" if parent_bp > 4 else s
    if t == "LOGIC_OP":
        bp = 2 if c["op"] == "AND" else 1
        s = f"{show_cond(c['left'], bp)} {c['op'].lower()} {show_cond(c['right'], bp + 1)}"
        return f"({s})" if bp < parent_bp else s
    raise ScriptError(f"알 수 없는 조건 노드 {t}")


def show_stmts(node: dict, indent: int = 0) -> list[str]:
    pad = "    " * indent
    out = []
    while node and node["type"] != "NULL":
        t = node["type"]
        if t in ("SHOW", "HIDE", "STOP_AUDIO"):
            out.append(pad + t.lower())
        elif t == "GO_PREV":
            out.append(pad + "prev")
        elif t == "GO_NEXT":
            out.append(pad + "next")
        elif t == "PLAY_AUDIO":
            out.append(f"{pad}play {show_value(node['value'])}")
        elif t == "SET_VOLUME":
            out.append(f"{pad}volume {show_value(node['value'])}")
        elif t in STATE_SYM:
            out.append(f"{pad}{STATE_SYM[t]} = {show_value(node['value'])}")
        elif t == "SET_LOCAL":
            out.append(f"{pad}{_name('@', node['localVarName'])} = {show_value(node['value'])}")
        elif t == "SET_GLOBAL":
            out.append(f"{pad}{_name('$', node['varName'])} = {show_value(node['value'])}")
        elif t in ("IF", "IF_ELSE"):
            out.append(f"{pad}if {show_cond(node['cond'])}:")
            out += show_stmts(node["if"], indent + 1) or [pad + "    pass"]
            if t == "IF_ELSE":
                els = node["else"]
                if els["type"] in ("IF", "IF_ELSE") and els["cont"]["type"] == "NULL":
                    sub = show_stmts(els, indent)
                    out.append(pad + "el" + sub[0].lstrip())
                    out += sub[1:]
                else:
                    out.append(pad + "else:")
                    out += show_stmts(els, indent + 1) or [pad + "    pass"]
        else:
            raise ScriptError(f"알 수 없는 문장 노드 {t}")
        node = node["cont"]
    return out


def decompile(node: dict, ret: dict | None = None) -> str:
    lines = show_stmts(node)
    if ret is not None:
        lines.append(("return " if lines else "") + show_value(ret))
    return "\n".join(lines)
