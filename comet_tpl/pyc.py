"""Python subset -> Comet template blocks, and blocks -> Python.

Agents write familiar Python; this compiles it to the platform's block nodes (the same nodes the
block editor shows). Parsing is Python's own `ast`, so the grammar is exactly Python's; this module
decides which constructs map to blocks, and lowers the rest (ternaries, f-strings, min/max, %,
multiple returns, small for-loops, helper functions) into blocks the platform does have.

Names
    template variables      by their own name: 대사, 창열림 (spaces etc. -> '_': 유저_입력), or var("유저 입력")
    locals                  any other assigned name: t = timer - 창열린시각
    sprite (state scripts)  self.x self.y self.width self.height self.opacity, self.visible = <cond>, show(), hide()
    actions                 next() prev() play(url) stop_audio() volume(n), assignment to a system variable
    builtins                timer mouse_x mouse_y is_first is_last, len sin cos round sqrt img audio str
                            floor ceil abs min max clamp join var
"""
from __future__ import annotations

import ast
import keyword
import math
import re

from .dsl import ALLOWED, CONTEXT_KO, ScriptError, _chain

ALIASES_FIXED = {"user_input": "유저 입력"}
BUILTIN_VALUES = {"timer": "TIMER", "mouse_x": "MOUSE_X", "mouse_y": "MOUSE_Y"}
COND_NAMES = {"is_first": "IS_FIRST", "is_last": "IS_LAST"}
FUNCS = {"sin": "SIN", "cos": "COS", "round": "ROUND", "sqrt": "SQRT"}
SPRITE_PROPS = {"x": "SET_X", "y": "SET_Y", "width": "SET_WIDTH", "height": "SET_HEIGHT", "opacity": "SET_OPACITY"}
CMP = {ast.Eq: "EQUAL", ast.NotEq: "NOT_EQUAL", ast.Lt: "LESS", ast.LtE: "LESS_OR_EQUAL", ast.Gt: "GREATER",
       ast.GtE: "GREATER_OR_EQUAL", ast.Is: "EQUAL", ast.IsNot: "NOT_EQUAL"}
ARITH = {ast.Add: "ADD", ast.Sub: "SUB", ast.Mult: "MUL", ast.Div: "DIV"}
STMT_CALLS = {"next": "GO_NEXT", "go_next": "GO_NEXT", "prev": "GO_PREV", "go_prev": "GO_PREV", "show": "SHOW",
              "hide": "HIDE", "stop_audio": "STOP_AUDIO"}
RESERVED = (set(BUILTIN_VALUES) | set(COND_NAMES) | set(FUNCS) | set(STMT_CALLS) |
            {"len", "img", "audio", "str", "floor", "ceil", "abs", "min", "max", "clamp", "join", "var", "local",
             "play", "volume", "setvar", "setlocal", "add", "self", "True", "False", "None", "range", "int", "float"})
DECORATORS = ("state", "action", "value", "on")
MAX_UNROLL = 100


def alias_of(name: str) -> str:
    a = re.sub(r"\W", "_", name)
    return "_" + a if a[:1].isdigit() else a


def const(s) -> dict:
    return {"type": "CONST", "value": s}


def num_text(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            raise ScriptError("NaN/무한대 상수는 쓸 수 없습니다")
        return str(int(v)) if v == int(v) and abs(v) < 1e15 else repr(v)
    return str(v)


# ---------------------------------------------------------------- module (logic.py)

class Module:
    """Parsed logic file: constants, helper functions, decorated script entries."""

    def __init__(self, src: str = "", filename: str = "<logic>"):
        self.consts: dict[str, ast.expr] = {}
        self.macros: dict[str, ast.FunctionDef] = {}
        self.entries: list[tuple[str, object, ast.FunctionDef]] = []  # (kind, target, func)
        self.filename = filename
        if src:
            self._parse(src)

    def _parse(self, src: str):
        try:
            tree = ast.parse(src, self.filename)
        except SyntaxError as e:
            raise ScriptError(f"Python 문법 오류: {e.msg}", e.lineno) from None
        for st in tree.body:
            if isinstance(st, (ast.Import, ast.ImportFrom)) or _is_docstring(st):
                continue
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                if not _is_literal(st.value):
                    raise ScriptError("모듈 맨 위 대입은 상수(숫자·문자열·True/False)만 됩니다", st.lineno)
                self.consts[st.targets[0].id] = st.value
            elif isinstance(st, ast.FunctionDef):
                decos = [d for d in st.decorator_list]
                if not decos:
                    self.macros[st.name] = st
                    continue
                for d in decos:
                    if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in DECORATORS
                            and len(d.args) == 1 and isinstance(d.args[0], ast.Constant)):
                        raise ScriptError('데코레이터는 @state("레이어") @action("레이어") @value("레이어") @on("load"|"key:Code")', d.lineno)
                    self.entries.append((d.func.id, d.args[0].value, st))
            else:
                raise ScriptError("모듈 맨 위에는 상수, 도우미 함수, 데코레이터 달린 함수만 둡니다", st.lineno)


def _is_docstring(st) -> bool:
    return isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str)


def _is_literal(e) -> bool:
    if isinstance(e, ast.Constant) and isinstance(e.value, (int, float, str, bool)):
        return True
    return isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub) and _is_literal(e.operand)


# ---------------------------------------------------------------- compiler

class PyCompiler:
    def __init__(self, variables: list[dict] | None = None, module: Module | None = None):
        """variables: platform variable table; None = permissive snippet mode."""
        self.module = module or Module()
        self.vars = None if variables is None else {v["varName"]: v["mode"] for v in variables}
        self.alias = dict(ALIASES_FIXED)
        if self.vars is not None:
            for n in self.vars:
                self.alias.setdefault(alias_of(n), n)
                self.alias.setdefault(n, n)

    # -- entry points
    def compile_source(self, src: str, context: str):
        """Inline script text: a body of statements, or (value scripts) a single expression."""
        if context == "value":
            try:
                body = [ast.Return(value=ast.parse(src.strip(), mode="eval").body, lineno=1, col_offset=0)]
                return self._run(lambda: self._value_body(body), context, body)
            except SyntaxError:
                pass
        try:
            body = ast.parse(_dedent(src)).body
        except SyntaxError as e:
            raise ScriptError(f"Python 문법 오류: {e.msg}", e.lineno) from None
        return self.compile_body(body, context)

    def compile_function(self, fn: ast.FunctionDef, context: str):
        if fn.args.args:
            raise ScriptError("스크립트 함수는 인자를 받지 않습니다(도우미 함수로 빼세요)", fn.lineno)
        body = [s for s in fn.body if not _is_docstring(s)]
        return self.compile_body(body, context)

    def compile_body(self, body: list[ast.stmt], context: str):
        if context == "value":
            return self._run(lambda: self._value_body(body), context, body)
        return self._run(lambda: (_chain(self.block(body)), None), context, body)

    def _typo_guard(self, node, ret):
        """A local that is only written, and is named like a template variable, is almost surely a typo."""
        if self.vars is None:
            return
        import difflib
        from .dsl import iter_stmts, iter_values
        read = {v["localVarName"] for root in [node] + ([ret] if ret else []) for v in iter_values(root) if v["type"] == "LOCAL_VAR"}
        for st in iter_stmts(node):
            if st["type"] == "SET_LOCAL" and st["localVarName"] not in read and not st["localVarName"].startswith("_"):
                near = difflib.get_close_matches(st["localVarName"], list(self.vars), 1, 0.6)
                if near:
                    raise ScriptError(f"'{st['localVarName']}'에 대입하고 읽지 않습니다. 템플릿 변수 '{near[0]}'의 오타가 아닌가요? "
                                      "(정말 지역 변수라면 다른 이름을 쓰세요)")

    def _run(self, fn, context, body):
        node, ret = self._run_inner(fn, context, body)
        self._typo_guard(node, ret)
        return node, ret

    def _run_inner(self, fn, context, body):
        self.assigned = _stored_names(body)
        self.context = context
        self.tmp = 0
        self.binds: list[dict] = [{}]
        self.pre: list[dict] = []
        self.globals_decl: set[str] = set()
        self.depth = 0
        return fn()

    def _value_body(self, body):
        returns = [n for s in body for n in ast.walk(s) if isinstance(n, ast.Return)]
        if not returns:
            raise ScriptError("표시값(value) 스크립트에 return이 없습니다", body[0].lineno if body else None)
        if len(returns) == 1 and body and body[-1] is returns[0]:
            stmts = self.block(body[:-1])
            ret = self.val(body[-1].value, body[-1])
            stmts += self._flush()
            return _chain(stmts), ret
        lowered = _lower_returns(body, "_ret")
        return _chain(self.block(lowered)), {"type": "LOCAL_VAR", "localVarName": "_ret"}

    # -- helpers
    def _err(self, msg, node):
        raise ScriptError(msg, getattr(node, "lineno", None))

    def _flush(self) -> list[dict]:
        out, self.pre = self.pre, []
        return out

    def _temp(self) -> str:
        self.tmp += 1
        return f"_t{self.tmp}"

    def _allow(self, typ, node):
        if typ not in ALLOWED[self.context]:
            where = [CONTEXT_KO[c] for c, s in ALLOWED.items() if typ in s]
            self._err(f"{typ}는 {CONTEXT_KO[self.context]} 스크립트에서 쓸 수 없습니다(쓸 수 있는 곳: {', '.join(where)})", node)

    def _template_var(self, name: str) -> str | None:
        if self.vars is None:
            return None
        return self.alias.get(name)

    def _bound(self, name):
        for b in reversed(self.binds):
            if name in b:
                return b[name]
        return None

    # -- statements
    def block(self, body: list[ast.stmt]) -> list[dict]:
        out = []
        for st in body:
            nodes = self.stmt(st)
            out += self._flush() + nodes
        return out

    def stmt(self, st: ast.stmt) -> list[dict]:
        if isinstance(st, ast.Pass) or _is_docstring(st):
            return []
        if isinstance(st, ast.Global):
            self.globals_decl.update(st.names)
            return []
        if isinstance(st, ast.Assign):
            if len(st.targets) != 1 or isinstance(st.targets[0], (ast.Tuple, ast.List)):
                self._err("대입은 한 번에 하나씩(a = b = 1, a, b = … 불가)", st)
            return self.assign(st.targets[0], st.value, st)
        if isinstance(st, ast.AugAssign):
            if not isinstance(st.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv)):
                self._err("+= -= *= /= %= //=만 됩니다", st)
            tgt = st.target
            read = ast.Attribute(value=tgt.value, attr=tgt.attr) if isinstance(tgt, ast.Attribute) else ast.Name(id=tgt.id)
            if isinstance(tgt, ast.Attribute):
                self._err("self.x += …처럼 오브젝트 속성은 읽을 수 없습니다. 지역 변수에 기준값을 두고 self.x = 식으로 쓰세요", st)
            return self.assign(tgt, ast.BinOp(left=read, op=st.op, right=st.value, lineno=st.lineno), st)
        if isinstance(st, ast.If):
            return self.if_(st)
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
            return self.call_stmt(st.value)
        if isinstance(st, ast.Return):
            self._err("return은 표시값(value) 스크립트에서만, 맨 끝 또는 if 갈래 끝에 둡니다", st)
        if isinstance(st, ast.For):
            return self.for_(st)
        if isinstance(st, ast.While):
            self._err("반복(while)은 블록에 없습니다. state 스크립트는 매 프레임 다시 도니 timer로 시간에 따라 바꾸세요", st)
        self._err(f"{type(st).__name__} 문장은 지원하지 않습니다", st)

    def assign(self, tgt, value_expr, st) -> list[dict]:
        if isinstance(tgt, ast.Attribute):
            if not (isinstance(tgt.value, ast.Name) and tgt.value.id == "self"):
                self._err("속성 대입은 self.x / self.y / self.width / self.height / self.opacity / self.visible만", st)
            if tgt.attr == "visible":
                self._allow("SHOW", st)
                if isinstance(value_expr, ast.Constant) and isinstance(value_expr.value, bool):
                    return [{"type": "SHOW" if value_expr.value else "HIDE"}]
                c = self.cond(value_expr)
                return [{"type": "IF_ELSE", "cond": c, "if": _chain([{"type": "SHOW"}]), "else": _chain([{"type": "HIDE"}])}]
            if tgt.attr not in SPRITE_PROPS:
                self._err(f"self.{tgt.attr}는 없습니다(x, y, width, height, opacity, visible)", st)
            typ = SPRITE_PROPS[tgt.attr]
            self._allow(typ, st)
            return [{"type": typ, "value": self.val(value_expr, st)}]
        if not isinstance(tgt, ast.Name):
            self._err("대입 대상은 이름이나 self.속성이어야 합니다", st)
        name = tgt.id
        if self._bound(name) is not None:
            self._err(f"'{name}'는 도우미 함수 인자라 대입할 수 없습니다", st)
        tv = self._template_var(name)
        if tv is not None or (self.vars is None and name in self.globals_decl):
            tv = tv or name
            mode = self.vars.get(tv) if self.vars is not None else "none"
            if mode != "none":
                kind = {"default": "전역(AI가 쓰는)", "refer": "유저 입력"}.get(mode, "")
                self._err(f"'{tv}'는 {kind} 변수라 스크립트로 바꿀 수 없습니다. 시스템 변수만 대입됩니다", st)
            self._allow("SET_GLOBAL", st)
            return [{"type": "SET_GLOBAL", "varName": tv, "value": self.val(value_expr, st)}]
        if name in RESERVED or name in self.module.consts or name in self.module.macros:
            self._err(f"'{name}'는 예약된 이름이라 지역 변수로 쓸 수 없습니다", st)
        return [{"type": "SET_LOCAL", "localVarName": name, "value": self.val(value_expr, st)}]

    def if_(self, st: ast.If) -> list[dict]:
        c = self.cond(st.test)
        pre = self._flush()
        body = _chain(self.block(st.body))
        if st.orelse:
            els = _chain(self.block(st.orelse))
            self._allow("IF_ELSE", st)
            return pre + [{"type": "IF_ELSE", "cond": c, "if": body, "else": els}]
        self._allow("IF", st)
        return pre + [{"type": "IF", "cond": c, "if": body}]

    def for_(self, st: ast.For) -> list[dict]:
        it = st.iter
        if not (isinstance(st.target, ast.Name) and isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                and it.func.id == "range" and all(_const_num(a, self) is not None for a in it.args) and not st.orelse):
            self._err("for는 상수 range(…)만 됩니다(블록에 반복이 없어 컴파일 때 펼침)", st)
        rng = range(*[int(_const_num(a, self)) for a in it.args])
        if len(rng) > MAX_UNROLL:
            self._err(f"range가 {MAX_UNROLL}번을 넘습니다", st)
        out = []
        for i in rng:
            self.binds.append({st.target.id: const(str(i))})
            out += self.block(st.body)
            self.binds.pop()
        return out

    def call_stmt(self, call: ast.Call) -> list[dict]:
        f = call.func
        if not isinstance(f, ast.Name):
            self._err("함수 호출은 이름으로만(메서드 호출 불가)", call)
        name = f.id
        if name in STMT_CALLS:
            typ = STMT_CALLS[name]
            if call.args:
                self._err(f"{name}()는 인자를 받지 않습니다", call)
            self._allow(typ, call)
            return [{"type": typ}]
        if name in ("play", "volume"):
            typ = "PLAY_AUDIO" if name == "play" else "SET_VOLUME"
            self._need_args(call, 1)
            self._allow(typ, call)
            return [{"type": typ, "value": self.val(call.args[0], call)}]
        if name in ("setvar", "setlocal"):
            self._need_args(call, 2)
            key = call.args[0]
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                self._err(f"{name}의 첫 인자는 이름 문자열", call)
            if name == "setlocal":
                return [{"type": "SET_LOCAL", "localVarName": key.value, "value": self.val(call.args[1], call)}]
            self.globals_decl.add(key.value)
            tv = key.value
            if self.vars is not None and self.vars.get(tv) != "none":
                self._err(f"'{tv}'는 시스템 변수가 아닙니다", call)
            self._allow("SET_GLOBAL", call)
            return [{"type": "SET_GLOBAL", "varName": tv, "value": self.val(call.args[1], call)}]
        if name in self.module.macros:
            return self.inline_stmt(self.module.macros[name], call)
        self._err(f"'{name}(…)'는 문장으로 쓸 수 없습니다(next, prev, show, hide, play, volume, stop_audio, setvar, 도우미 함수)", call)

    def _need_args(self, call, n):
        if len(call.args) != n or call.keywords:
            self._err(f"{call.func.id}()는 인자 {n}개", call)

    def _bind_args(self, fn: ast.FunctionDef, call: ast.Call) -> dict:
        params = [a.arg for a in fn.args.args]
        defaults = fn.args.defaults
        if len(call.args) > len(params) or call.keywords and any(k.arg not in params for k in call.keywords):
            self._err(f"{fn.name}() 인자가 맞지 않습니다({', '.join(params)})", call)
        given = dict(zip(params, call.args))
        given.update({k.arg: k.value for k in call.keywords})
        for p, d in zip(params[len(params) - len(defaults):], defaults):
            given.setdefault(p, d)
        missing = [p for p in params if p not in given]
        if missing:
            self._err(f"{fn.name}()에 인자 {missing}가 빠졌습니다", call)
        return {p: self.val(e, call) for p, e in given.items()}

    def inline_stmt(self, fn: ast.FunctionDef, call: ast.Call) -> list[dict]:
        self.depth += 1
        if self.depth > 20:
            self._err("도우미 함수 재귀는 안 됩니다", call)
        bound = self._bind_args(fn, call)
        self.assigned |= _stored_names(fn.body)
        pre = self._flush()
        self.binds.append(bound)
        body = [s for s in fn.body if not _is_docstring(s)]
        if any(isinstance(n, ast.Return) and n.value is not None for s in body for n in ast.walk(s)):
            self._err(f"{fn.name}()는 값을 돌려주므로 식 안에서 쓰세요", call)
        out = self.block(body)
        self.binds.pop()
        self.depth -= 1
        return pre + out

    # -- expressions
    def is_str(self, e) -> bool:
        if isinstance(e, ast.Constant):
            return isinstance(e.value, str)
        if isinstance(e, ast.JoinedStr):
            return True
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id in ("str", "join"):
            return True
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
            return self.is_str(e.left) or self.is_str(e.right)
        if isinstance(e, ast.Name) and e.id in self.module.consts:
            return self.is_str(self.module.consts[e.id])
        return False

    def val(self, e, at=None) -> dict:
        if isinstance(e, ast.Constant):
            v = e.value
            if v is None:
                return const("")
            if isinstance(v, (bool, int, float)):
                return const(num_text(v))
            if isinstance(v, str):
                return const(v)
            self._err(f"상수 {v!r}는 쓸 수 없습니다", e)
        if isinstance(e, ast.Name):
            return self.name_val(e)
        if isinstance(e, ast.UnaryOp):
            if isinstance(e.op, ast.USub):
                if isinstance(e.operand, ast.Constant) and isinstance(e.operand.value, (int, float)) and not isinstance(e.operand.value, bool):
                    return const(num_text(-e.operand.value))
                return {"type": "CALC", "left": const("0"), "right": self.val(e.operand), "op": "SUB"}
            if isinstance(e.op, ast.UAdd):
                return self.val(e.operand)
            if isinstance(e.op, ast.Not):
                return self.bool_to_val(e)
        if isinstance(e, ast.BinOp):
            return self.binop(e)
        if isinstance(e, ast.JoinedStr):
            return self.fstring(e)
        if isinstance(e, ast.IfExp):
            t = self._temp()
            c = self.cond(e.test)
            a, b = self.val(e.body), self.val(e.orelse)
            self.pre.append({"type": "IF_ELSE", "cond": c, "if": _chain([_setl(t, a)]), "else": _chain([_setl(t, b)])})
            return _getl(t)
        if isinstance(e, (ast.Compare, ast.BoolOp)):
            return self.bool_to_val(e)
        if isinstance(e, ast.Call):
            return self.call_val(e)
        if isinstance(e, ast.Attribute):
            self._err("오브젝트 속성(self.x 등)은 읽을 수 없습니다. 플랫폼에 그런 블록이 없습니다", e)
        if isinstance(e, (ast.List, ast.Tuple, ast.Dict, ast.Subscript)):
            self._err("리스트·딕셔너리·인덱싱은 블록에 없습니다. 값마다 변수를 따로 두세요", e)
        self._err(f"{type(e).__name__} 식은 지원하지 않습니다", e)

    def bool_to_val(self, e) -> dict:
        t = self._temp()
        c = self.cond(e)
        self.pre.append({"type": "IF_ELSE", "cond": c, "if": _chain([_setl(t, const("true"))]),
                         "else": _chain([_setl(t, const("false"))])})
        return _getl(t)

    def name_val(self, e: ast.Name) -> dict:
        n = e.id
        b = self._bound(n)
        if b is not None:
            return b
        tv = self._template_var(n)
        if tv is not None:
            return {"type": "VAR", "varName": tv}
        if n in self.module.consts:
            return self.val(self.module.consts[n])
        if n in BUILTIN_VALUES:
            return {"type": BUILTIN_VALUES[n]}
        if n in ("True", "False"):
            return const(n.lower())
        if n in COND_NAMES:
            return self.bool_to_val(e)
        if n in RESERVED:
            self._err(f"'{n}'는 함수라 ()와 함께 씁니다", e)
        if n in self.assigned:
            return {"type": "LOCAL_VAR", "localVarName": n}
        if self.vars is None:  # snippet mode: an unassigned name is a template variable
            return {"type": "VAR", "varName": n}
        import difflib
        near = list(dict.fromkeys(difflib.get_close_matches(n, list(self.vars) + [alias_of(v) for v in self.vars] + sorted(self.assigned), 3, 0.5)))
        self._err(f"'{n}'는 템플릿 변수도, 이 스크립트에서 대입한 지역 변수도 아닙니다" + (f" (혹시 {', '.join(near)}?)" if near else ""), e)

    def binop(self, e: ast.BinOp) -> dict:
        op = type(e.op)
        if op is ast.Add and (self.is_str(e.left) or self.is_str(e.right)):
            return _fold({"type": "JOIN", "left": self.val(e.left), "right": self.val(e.right)})
        if op in ARITH:
            return _fold({"type": "CALC", "left": self.val(e.left), "right": self.val(e.right), "op": ARITH[op]})
        if op in (ast.Mod, ast.FloorDiv):
            q, r = self.divmod_(e)
            return q if op is ast.FloorDiv else r
        if op is ast.Pow:
            p = _const_num(e.right, self)
            if p == 0.5:
                return {"type": "FUNC", "funcType": "SQRT", "value": self.val(e.left)}
            if p is not None and p == int(p) and 1 <= p <= 8:
                base = self.val(e.left)
                out = base
                for _ in range(int(p) - 1):
                    out = {"type": "CALC", "left": out, "right": base, "op": "MUL"}
                return out
            self._err("**는 상수 지수 1~8과 0.5만 됩니다", e)
        self._err(f"연산자 {type(e.op).__name__}는 블록에 없습니다", e)

    def divmod_(self, e: ast.BinOp) -> tuple[dict, dict]:
        """Python floor division and modulo, exact for integers.

        The quotient estimate (a × 1/c, or the site's 2-decimal DIV) can land one off near an integer
        (49 × (1/49) = 0.999…), so the remainder is checked once in each direction and both are corrected.
        """
        calc = lambda l, r, op: {"type": "CALC", "left": l, "right": r, "op": op}
        cmp = lambda l, r, op: {"type": "COMPARE", "left": l, "right": r, "op": op}
        both = lambda x, y: {"type": "LOGIC_OP", "left": x, "right": y, "op": "AND"}
        either = lambda x, y: {"type": "LOGIC_OP", "left": x, "right": y, "op": "OR"}
        a = self._stable(self.val(e.left))
        c = _const_num(e.right, self)
        if c is not None and c != 0:
            b, est = const(num_text(c)), calc(a, const(num_text(1 / c)), "MUL")
        else:
            b = self._stable(self.val(e.right))
            est = calc(a, b, "DIV")
        q, r = self._temp(), self._temp()
        self.pre += [_setl(q, _floor(est)), _setl(r, calc(a, calc(b, _getl(q), "MUL"), "SUB"))]
        zero, R = const("0"), _getl(r)
        if c is not None and c != 0:
            up = cmp(R, b, "GREATER_OR_EQUAL" if c > 0 else "LESS_OR_EQUAL")
            down = cmp(R, zero, "LESS" if c > 0 else "GREATER")
        else:
            pos, neg = cmp(b, zero, "GREATER"), cmp(b, zero, "LESS")
            up = either(both(pos, cmp(R, b, "GREATER_OR_EQUAL")), both(neg, cmp(R, b, "LESS_OR_EQUAL")))
            down = either(both(pos, cmp(R, zero, "LESS")), both(neg, cmp(R, zero, "GREATER")))
        for cond, step, sign in ((up, "ADD", "SUB"), (down, "SUB", "ADD")):
            self.pre.append({"type": "IF", "cond": cond, "if": _chain([
                _setl(q, calc(_getl(q), const("1"), step)), _setl(r, calc(_getl(r), b, sign))])})
        return _getl(q), _getl(r)

    def _stable(self, v: dict) -> dict:
        """A value read more than once: constants and variables as they are, anything else through a temp."""
        if v["type"] in ("CONST", "VAR", "LOCAL_VAR"):
            return v
        t = self._temp()
        self.pre.append(_setl(t, v))
        return _getl(t)

    def fstring(self, e: ast.JoinedStr) -> dict:
        parts = []
        for p in e.values:
            if isinstance(p, ast.Constant):
                if p.value != "":
                    parts.append(const(p.value))
            else:
                if p.format_spec is not None or p.conversion not in (-1, 115):
                    self._err("f-string 서식 지정(:.2f, !r)은 안 됩니다. round()로 다듬으세요", p)
                parts.append(self.val(p.value))
        if not parts:
            return const("")
        if len(parts) == 1:
            return parts[0]
        node = parts[0]
        for p in parts[1:]:
            node = _fold({"type": "JOIN", "left": node, "right": p})
        return node

    def call_val(self, e: ast.Call) -> dict:
        if not isinstance(e.func, ast.Name):
            self._err("메서드 호출(.upper() 등)은 블록에 없습니다", e)
        n = e.func.id
        args = e.args
        if n in self.module.macros:
            return self.inline_val(self.module.macros[n], e)
        if n in BUILTIN_VALUES and not args:
            return {"type": BUILTIN_VALUES[n]}
        if n in COND_NAMES and not args:
            return self.bool_to_val(e)
        one = lambda: (self._need_args(e, 1), self.val(args[0]))[1]
        if n == "len":
            return {"type": "LENGTH", "value": one()}
        if n in FUNCS:
            return {"type": "FUNC", "funcType": FUNCS[n], "value": one()}
        if n == "img":
            return {"type": "GET_IMAGE_STORAGE", "value": one()}
        if n == "audio":
            return {"type": "GET_AUDIO_STORAGE", "value": one()}
        if n in ("str", "float", "int"):
            v = one()
            return _floor_trunc(v) if n == "int" else v
        if n == "floor":
            return _floor(one())
        if n == "ceil":
            return {"type": "CALC", "left": const("0"), "right": _floor({"type": "CALC", "left": const("0"), "right": one(), "op": "SUB"}), "op": "SUB"}
        if n == "add":
            self._need_args(e, 2)
            return {"type": "CALC", "left": self.val(args[0]), "right": self.val(args[1]), "op": "ADD"}
        if n == "join":
            self._need_args(e, 2)
            return {"type": "JOIN", "left": self.val(args[0]), "right": self.val(args[1])}
        if n in ("var", "local"):
            if not (len(args) == 1 and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)):
                self._err(f'{n}("이름")처럼 문자열 상수 하나', e)
            key = args[0].value
            if n == "var":
                if self.vars is not None and key not in self.vars:
                    self._err(f"템플릿 변수 '{key}'가 없습니다", e)
                return {"type": "VAR", "varName": key}
            return {"type": "LOCAL_VAR", "localVarName": key}
        if n == "abs":
            v = one()
            t = self._temp()
            self.pre.append(_setl(t, v))
            self.pre.append({"type": "IF", "cond": {"type": "COMPARE", "op": "LESS", "left": _getl(t), "right": const("0")},
                             "if": _chain([_setl(t, {"type": "CALC", "left": const("0"), "right": _getl(t), "op": "SUB"})])})
            return _getl(t)
        if n in ("min", "max", "clamp"):
            want = 3 if n == "clamp" else None
            if want and len(args) != 3 or not want and len(args) < 2:
                self._err(f"{n}() 인자 수: {'clamp(x, 낮은값, 높은값)' if want else '2개 이상'}", e)
            vals = [self.val(a) for a in args]
            if n == "clamp":
                return self._minmax("min", [self._minmax("max", [vals[0], vals[1]]), vals[2]])
            return self._minmax(n, vals)
        if n in ("range",):
            self._err("range는 for 안에서만", e)
        self._err(f"'{n}()'는 값 함수가 아닙니다", e)

    def _minmax(self, n, vals) -> dict:
        t = self._temp()
        self.pre.append(_setl(t, vals[0]))
        op = "GREATER" if n == "min" else "LESS"
        for v in vals[1:]:
            self.pre.append({"type": "IF", "cond": {"type": "COMPARE", "op": op, "left": _getl(t), "right": v}, "if": _chain([_setl(t, v)])})
        return _getl(t)

    def inline_val(self, fn: ast.FunctionDef, call: ast.Call) -> dict:
        self.depth += 1
        if self.depth > 20:
            self._err("도우미 함수 재귀는 안 됩니다", call)
        bound = self._bind_args(fn, call)
        self.assigned |= _stored_names(fn.body)
        body = [s for s in fn.body if not _is_docstring(s)]
        self.binds.append(bound)
        try:
            if len(body) == 1 and isinstance(body[0], ast.Return) and body[0].value is not None:
                return self.val(body[0].value)
            if not any(isinstance(n, ast.Return) for s in body for n in ast.walk(s)):
                self._err(f"{fn.name}()는 return이 없어 값으로 쓸 수 없습니다", call)
            r = self._temp()
            saved = self._flush()
            stmts = self.block(_lower_returns(body, r))
            self.pre = saved + stmts
            return _getl(r)
        finally:
            self.binds.pop()
            self.depth -= 1

    # -- conditions
    def cond(self, e) -> dict:
        if isinstance(e, ast.Constant) and isinstance(e.value, bool):
            return {"type": "TRUE" if e.value else "FALSE"}
        if isinstance(e, ast.Name) and e.id in ("True", "False"):
            return {"type": e.id.upper()}
        if isinstance(e, ast.Name) and e.id in COND_NAMES and self._bound(e.id) is None:
            return {"type": COND_NAMES[e.id]}
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id in COND_NAMES and not e.args:
            return {"type": COND_NAMES[e.func.id]}
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not):
            return {"type": "NOT", "cond": self.cond(e.operand)}
        if isinstance(e, ast.BoolOp):
            op = "AND" if isinstance(e.op, ast.And) else "OR"
            node = self.cond(e.values[0])
            for v in e.values[1:]:
                node = {"type": "LOGIC_OP", "op": op, "left": node, "right": self.cond(v)}
            return node
        if isinstance(e, ast.Compare):
            return self.compare(e)
        # truthiness: a value is true unless it coerces to "", 0 or false
        return {"type": "COMPARE", "op": "NOT_EQUAL", "left": self.val(e), "right": const("")}

    def compare(self, e: ast.Compare) -> dict:
        parts = []
        left = e.left
        for op, right in zip(e.ops, e.comparators):
            if isinstance(op, (ast.In, ast.NotIn)):
                if not isinstance(right, (ast.Tuple, ast.List, ast.Set)):
                    self._err("in 뒤에는 (값, 값, …) 목록 상수", e)
                lv = self.val(left)
                eq = "EQUAL" if isinstance(op, ast.In) else "NOT_EQUAL"
                sub = [{"type": "COMPARE", "op": eq, "left": lv, "right": self.val(x)} for x in right.elts]
                node = sub[0]
                for s in sub[1:]:
                    node = {"type": "LOGIC_OP", "op": "OR" if eq == "EQUAL" else "AND", "left": node, "right": s}
                parts.append(node)
            else:
                parts.append({"type": "COMPARE", "op": CMP[type(op)], "left": self.val(left), "right": self.val(right)})
            left = right
        node = parts[0]
        for p in parts[1:]:
            node = {"type": "LOGIC_OP", "op": "AND", "left": node, "right": p}
        return node


def _fold(v: dict) -> dict:
    """Constant-fold with the runtime's own semantics (render.calc / JOIN stringification)."""
    from .render import calc, coerce, js_str
    l, r = v["left"], v["right"]
    if l["type"] == "CONST" and r["type"] == "CONST":
        a, b = coerce(l["value"]), coerce(r["value"])
        res = js_str(a) + js_str(b) if v["type"] == "JOIN" else calc(v["op"], a, b)
        if isinstance(res, float) and (math.isnan(res) or math.isinf(res)):
            return v
        return const(js_str(res))
    return v


def _stored_names(body) -> set[str]:
    out = set()
    for st in body:
        for n in ast.walk(st):
            if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Store):
                out.add(n.id)
    return out


def _setl(name, v):
    return {"type": "SET_LOCAL", "localVarName": name, "value": v}


def _getl(name):
    return {"type": "LOCAL_VAR", "localVarName": name}


def _floor(v) -> dict:
    # JS Math.round(x - 0.5) == floor(x) (round is half-up)
    return {"type": "FUNC", "funcType": "ROUND", "value": {"type": "CALC", "left": v, "right": const("0.5"), "op": "SUB"}}


def _floor_trunc(v) -> dict:
    return _floor(v)  # int(): documented as floor (agents use it on non-negative values)


def _const_num(e, comp: PyCompiler):
    if isinstance(e, ast.Constant) and isinstance(e.value, (int, float)) and not isinstance(e.value, bool):
        return e.value
    if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub):
        v = _const_num(e.operand, comp)
        return -v if v is not None else None
    if isinstance(e, ast.Name) and e.id in comp.module.consts:
        return _const_num(comp.module.consts[e.id], comp)
    if isinstance(e, ast.Name):
        b = comp._bound(e.id)
        if b and b["type"] == "CONST":
            try:
                return float(b["value"])
            except ValueError:
                return None
    return None


def _lower_returns(body: list[ast.stmt], var: str) -> list[ast.stmt]:
    """Rewrite returns into `var = e` with the rest of the block moved into each non-returning branch."""
    out = []
    for i, st in enumerate(body):
        if isinstance(st, ast.Return):
            out.append(ast.Assign(targets=[ast.Name(id=var)], value=st.value or ast.Constant(value=""), lineno=st.lineno))
            return out
        if isinstance(st, ast.If) and any(isinstance(n, ast.Return) for n in ast.walk(st)):
            rest = body[i + 1:]
            out.append(ast.If(test=st.test, body=_lower_returns(st.body + rest, var),
                              orelse=_lower_returns(st.orelse + rest, var), lineno=st.lineno))
            return out
        out.append(st)
    return out


def _dedent(src: str) -> str:
    lines = [l for l in src.replace("\r\n", "\n").split("\n")]
    ind = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
    return "\n".join(l[ind:] for l in lines)


# ---------------------------------------------------------------- blocks -> Python

PY_PREC = {"OR": 1, "AND": 2, "NOT": 3, "CMP": 4, "ADD": 6, "SUB": 6, "MUL": 7, "DIV": 7, "NEG": 8}
OP_SYM = {"ADD": "+", "SUB": "-", "MUL": "*", "DIV": "/"}
CMP_SYM = {"EQUAL": "==", "NOT_EQUAL": "!=", "LESS": "<", "LESS_OR_EQUAL": "<=", "GREATER": ">", "GREATER_OR_EQUAL": ">="}


class PyPrinter:
    def __init__(self, var_names: list[str] | None = None):
        self.vars = list(var_names or [])
        self.alias = {}
        seen = {}
        for n in self.vars:
            a = alias_of(n)
            seen.setdefault(a, []).append(n)
        for a, ns in seen.items():
            if len(ns) == 1 and a.isidentifier() and not keyword.iskeyword(a) and a not in RESERVED:
                self.alias[ns[0]] = a

    def _str(self, s: str, q='"') -> str:
        body = s.replace("\\", "\\\\").replace(q, "\\" + q).replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
        return q + body + q

    def var(self, n):
        return self.alias.get(n) or f"var({self._str(n)})"

    def local(self, n):
        ok = n.isidentifier() and not keyword.iskeyword(n) and n not in RESERVED and n not in self.alias.values() and n not in self.vars
        return n if ok else f"local({self._str(n)})"

    def const(self, s: str, q='"') -> str:
        from .render import coerce, js_str
        if s in ("true", "false"):
            return "True" if s == "true" else "False"
        if re.fullmatch(r"-?\d+(\.\d+)?", s) and js_str(coerce(s)) == s:
            return s
        return self._str(s, q)

    def value(self, v: dict, bp: int = 0, q='"') -> str:
        t = v["type"]
        if t == "CONST":
            s = self.const(v["value"], q)
            return f"({s})" if s.startswith("-") and bp >= PY_PREC["NEG"] else s
        if t == "VAR":
            return self.var(v["varName"])
        if t == "LOCAL_VAR":
            return self.local(v["localVarName"])
        if t in ("TIMER", "MOUSE_X", "MOUSE_Y"):
            return t.lower()
        if t == "LENGTH":
            return f"len({self.value(v['value'], q=q)})"
        if t == "FUNC":
            return f"{v['funcType'].lower()}({self.value(v['value'], q=q)})"
        if t == "GET_IMAGE_STORAGE":
            return f"img({self.value(v['value'], q=q)})"
        if t == "GET_AUDIO_STORAGE":
            return f"audio({self.value(v['value'], q=q)})"
        if t == "JOIN":
            return self.join(v, q)
        if t == "CALC":
            op = v["op"]
            if op == "SUB" and v["left"] == const("0") and v["right"]["type"] != "CONST":
                s = "-" + self.value(v["right"], PY_PREC["NEG"], q)
                return f"({s})" if bp > PY_PREC["NEG"] else s
            p = PY_PREC[op]
            right_needs_str = op == "ADD" and _static_str(v["right"])
            left_needs_str = op == "ADD" and _static_str(v["left"])
            if left_needs_str or right_needs_str:
                # Python `+` with a string literal compiles to JOIN; keep CALC ADD explicit
                return f"add({self.value(v['left'], q=q)}, {self.value(v['right'], q=q)})"
            s = f"{self.value(v['left'], p, q)} {OP_SYM[op]} {self.value(v['right'], p + 1, q)}"
            return f"({s})" if p < bp else s
        raise ScriptError(f"알 수 없는 값 노드 {t}")

    def join(self, v: dict, q='"') -> str:
        parts = []
        node = v
        while node["type"] == "JOIN":
            parts.insert(0, node["right"])
            node = node["left"]
        parts.insert(0, node)
        consts = [p["type"] == "CONST" for p in parts]
        clean = all(not (consts[i] and consts[i + 1]) for i in range(len(parts) - 1)) and \
            all(not (p["type"] == "CONST" and p["value"] == "") for p in parts) and \
            not any(p["type"] == "JOIN" for p in parts) and q == '"'
        if not clean:
            return f"join({self.value(v['left'], q=q)}, {self.value(v['right'], q=q)})"
        out = ""
        for p in parts:
            if p["type"] == "CONST":
                out += p["value"].replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("{", "{{").replace("}", "}}")
            else:
                out += "{" + self.value(p, q="'") + "}"
        return 'f"' + out + '"'

    def cond(self, c: dict, bp: int = 0) -> str:
        t = c["type"]
        if t in ("TRUE", "FALSE"):
            return "True" if t == "TRUE" else "False"
        if t in ("IS_FIRST", "IS_LAST"):
            return t.lower()
        if t == "NOT":
            s = f"not {self.cond(c['cond'], PY_PREC['NOT'])}"
            return f"({s})" if bp > PY_PREC["NOT"] else s
        if t == "COMPARE":
            s = f"{self.value(c['left'], 5)} {CMP_SYM[c['op']]} {self.value(c['right'], 5)}"
            return f"({s})" if bp >= PY_PREC["CMP"] else s
        if t == "LOGIC_OP":
            p = PY_PREC[c["op"]]
            s = f"{self.cond(c['left'], p)} {c['op'].lower()} {self.cond(c['right'], p + 1)}"
            return f"({s})" if p < bp else s
        raise ScriptError(f"알 수 없는 조건 노드 {t}")

    def stmts(self, node: dict, ind: int = 1) -> list[str]:
        pad = "    " * ind
        out = []
        while node and node["type"] != "NULL":
            t = node["type"]
            if t in ("SHOW", "HIDE", "STOP_AUDIO"):
                out.append(f"{pad}{t.lower()}()")
            elif t in ("GO_NEXT", "GO_PREV"):
                out.append(f"{pad}{'next' if t == 'GO_NEXT' else 'prev'}()")
            elif t in ("PLAY_AUDIO", "SET_VOLUME"):
                out.append(f"{pad}{'play' if t == 'PLAY_AUDIO' else 'volume'}({self.value(node['value'])})")
            elif t in ("SET_X", "SET_Y", "SET_WIDTH", "SET_HEIGHT", "SET_OPACITY"):
                out.append(f"{pad}self.{t[4:].lower()} = {self.value(node['value'])}")
            elif t == "SET_LOCAL":
                n = node["localVarName"]
                tgt = self.local(n)
                out.append(f"{pad}{tgt} = {self.value(node['value'])}" if tgt == n else f"{pad}setlocal({self._str(n)}, {self.value(node['value'])})")
            elif t == "SET_GLOBAL":
                n = node["varName"]
                out.append(f"{pad}{self.alias[n]} = {self.value(node['value'])}" if n in self.alias else f"{pad}setvar({self._str(n)}, {self.value(node['value'])})")
            elif t in ("IF", "IF_ELSE"):
                out.append(f"{pad}if {self.cond(node['cond'])}:")
                out += self.stmts(node["if"], ind + 1) or [pad + "    pass"]
                if t == "IF_ELSE":
                    e = node["else"]
                    if e["type"] in ("IF", "IF_ELSE") and e["cont"]["type"] == "NULL":
                        sub = self.stmts(e, ind)
                        out.append(pad + "el" + sub[0].lstrip())
                        out += sub[1:]
                    else:
                        out.append(pad + "else:")
                        out += self.stmts(e, ind + 1) or [pad + "    pass"]
            else:
                raise ScriptError(f"알 수 없는 문장 노드 {t}")
            node = node["cont"]
        return out

    def body(self, node: dict, ret: dict | None = None, ind: int = 1) -> str:
        lines = self.stmts(node, ind)
        if ret is not None:
            lines.append("    " * ind + f"return {self.value(ret)}")
        return "\n".join(lines or ["    " * ind + "pass"])

    def inline(self, node: dict, ret: dict | None = None) -> str:
        """Text for a spec string (no indentation); a value script with no statements is just the expression."""
        if ret is not None and node["type"] == "NULL":
            return self.value(ret)
        return "\n".join(l[4:] for l in self.body(node, ret).split("\n"))


def _static_str(v: dict) -> bool:
    return v["type"] == "JOIN" or (v["type"] == "CONST" and not re.fullmatch(r"-?\d+(\.\d+)?|true|false", v["value"]))


def module_text(raw: dict, layer_names: list[str]) -> str:
    """logic.py for a rawTemplate. layer_names: spec order (bottom->top)."""
    pr = PyPrinter([v["varName"] for v in raw["variables"]])
    sprites = list(reversed(raw["sprites"]))
    counts = {}
    for n in layer_names:
        counts[n] = counts.get(n, 0) + 1
    systems = [v["varName"] for v in raw["variables"] if v["mode"] == "none"]
    globals_ = [v["varName"] for v in raw["variables"] if v["mode"] == "default"]
    out = ['"""템플릿 로직 (comet-template Python). 규칙: ~/.claude/skills/comet-template/RULES.md', "",
           f"전역(AI) 변수: {', '.join(globals_) or '-'}", f"시스템 변수: {', '.join(systems) or '-'}", '"""', ""]
    for i, s in enumerate(sprites):
        tgt = pr._str(layer_names[i]) if counts[layer_names[i]] == 1 else str(i)
        slots = [("state", s["stateScript"]["node"], None)]
        if s["spriteType"] == "image":
            slots.append(("action", s["actionScript"]["node"], None))
        else:
            a = s["assignedValueScript"]
            slots.append(("value", a["node"], a["res"]))
        for kind, node, ret in slots:
            if kind != "value" and node["type"] == "NULL":
                continue
            out += [f"@{kind}({tgt})", f"def layer{i}_{kind}():", pr.body(node, ret), "", ""]
    for j, t in enumerate(raw.get("triggers", [])):
        c = t["triggerCond"]
        on = "load" if c["type"] == "ONMOUNTED" else f"key:{c.get('key', '')}"
        out += [f"@on({pr._str(on)})", f"def trigger{j}():", pr.body(t["node"]), "", ""]
    return "\n".join(out).rstrip() + "\n"
