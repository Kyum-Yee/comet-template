"""Offline preview: a port of the site's template interpreter and canvas renderer.

Semantics follow the bundle (TemplatePreview / ws()) line for line where it matters:
value coercion, CALC on mixed types, JS loose comparison, state scripts run every frame,
sprites drawn from the last index to index 0, text wrapping, line height 1.4 x fontSize,
top padding 0.2 x fontSize, '*' toggling 70 % alpha, object-fit contain/cover.
"""
from __future__ import annotations

import hashlib
import math
import os
import platform
import re
import urllib.parse
import urllib.request


CACHE = os.path.expanduser("~/.cache/comet-template/img")


# ---------------------------------------------------------------- JS value helpers

def js_str(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        if v == int(v) and abs(v) < 1e21:
            return str(int(v))
        return repr(v)
    return str(v)


def is_numeric(s: str) -> bool:
    t = s.strip()
    if not t or t.lower() in ("nan", "inf", "-inf", "+inf", "infinity", "+infinity", "-infinity") and t not in ("Infinity", "-Infinity", "+Infinity"):
        return False
    try:
        float(t)
        return True
    except ValueError:
        return t in ("Infinity", "-Infinity", "+Infinity")


def coerce(s: str):
    if s == "true":
        return True
    if s == "false":
        return False
    if is_numeric(s):
        f = float(s.strip().replace("Infinity", "inf"))
        return int(f) if f == int(f) and abs(f) < 1e15 else f
    return s


def to_number(v) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    t = v.strip()
    if t == "":
        return 0.0
    return float(t.replace("Infinity", "inf")) if is_numeric(t) else math.nan


def loose_eq(a, b) -> bool:
    if type(a) is type(b) or (isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool)):
        return a == b
    if isinstance(a, bool):
        return loose_eq(1 if a else 0, b)
    if isinstance(b, bool):
        return loose_eq(a, 1 if b else 0)
    return to_number(a) == to_number(b)


def relate(a, b, op: str) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        x, y = a.encode("utf-16-be"), b.encode("utf-16-be")
    else:
        x, y = to_number(a), to_number(b)
        if math.isnan(x) or math.isnan(y):
            return False
    return {"LESS": x < y, "LESS_OR_EQUAL": x <= y, "GREATER": x > y, "GREATER_OR_EQUAL": x >= y}[op]


def js_round(x: float) -> float:
    return math.floor(x + 0.5)


class Interp:
    def __init__(self, values: dict, system: list[str], storage: dict, host: dict):
        self.e, self.t, self.storage, self.host = values, system, storage, host
        self.state: dict = {}
        self.events: list[str] = []

    def val(self, v: dict, loc: dict):
        t = v["type"]
        if t == "CONST":
            return coerce(v["value"])
        if t == "LOCAL_VAR":
            return coerce(loc.get(v["localVarName"], ""))
        if t == "VAR":
            return coerce(self.e.get(v["varName"], ""))
        if t == "LENGTH":
            x = self.val(v["value"], loc)
            return len(x.encode("utf-16-le")) // 2 if isinstance(x, str) else 1
        if t == "CALC":
            a, b = self.val(v["left"], loc), self.val(v["right"], loc)
            return calc(v["op"], a, b)
        if t == "JOIN":
            return js_str(self.val(v["left"], loc)) + js_str(self.val(v["right"], loc))
        if t == "FUNC":
            x = self.val(v["value"], loc)
            if not is_numeric(js_str(x)):
                return x
            k = to_number(js_str(x))
            f = v["funcType"]
            return {"SIN": lambda: math.sin(k * math.pi / 180), "COS": lambda: math.cos(k * math.pi / 180),
                    "ROUND": lambda: js_round(k), "SQRT": lambda: math.sqrt(abs(k))}.get(f, lambda: k)()
        if t == "TIMER":
            return self.host["now"]
        if t == "GET_IMAGE_STORAGE":
            return self.storage["images"].get(js_str(self.val(v["value"], loc)), "")
        if t == "GET_AUDIO_STORAGE":
            return self.storage["audio"].get(js_str(self.val(v["value"], loc)), "")
        if t == "MOUSE_X":
            return self.host["mouse"][0]
        if t == "MOUSE_Y":
            return self.host["mouse"][1]
        return 0

    def cond(self, c: dict, loc: dict) -> bool:
        t = c["type"]
        if t in ("TRUE", "FALSE"):
            return t == "TRUE"
        if t == "IS_FIRST":
            return self.host["first"]
        if t == "IS_LAST":
            return self.host["last"]
        if t == "COMPARE":
            a, b = self.val(c["left"], loc), self.val(c["right"], loc)
            op = c["op"]
            if op == "EQUAL":
                return loose_eq(a, b)
            if op == "NOT_EQUAL":
                return not loose_eq(a, b)
            return relate(a, b, op)
        if t == "LOGIC_OP":
            return (self.cond(c["left"], loc) and self.cond(c["right"], loc)) if c["op"] == "AND" else (self.cond(c["left"], loc) or self.cond(c["right"], loc))
        if t == "NOT":
            return not self.cond(c["cond"], loc)
        return False

    def run(self, n: dict, loc: dict, live: bool) -> dict:
        loc = dict(loc)
        while n and n["type"] != "NULL":
            t = n["type"]
            if t == "SET_LOCAL":
                loc[n["localVarName"]] = js_str(self.val(n["value"], loc))
            elif t == "SET_GLOBAL":
                if live and n["varName"] in self.t and n["varName"] in self.e:
                    self.e[n["varName"]] = js_str(self.val(n["value"], loc))
            elif t in ("GO_PREV", "GO_NEXT", "STOP_AUDIO"):
                if live:
                    self.events.append({"GO_PREV": "prev", "GO_NEXT": "next", "STOP_AUDIO": "stop_audio"}[t])
            elif t in ("PLAY_AUDIO", "SET_VOLUME"):
                if live:
                    self.events.append(("play " if t == "PLAY_AUDIO" else "volume ") + js_str(self.val(n["value"], loc)))
            elif t in ("SHOW", "HIDE"):
                self.state["visibility"] = 1 if t == "SHOW" else 0
            elif t == "IF":
                if self.cond(n["cond"], loc):
                    loc = self.run(n["if"], loc, live)
            elif t == "IF_ELSE":
                loc = self.run(n["if"] if self.cond(n["cond"], loc) else n["else"], loc, live)
            elif t in ("SET_X", "SET_Y", "SET_WIDTH", "SET_HEIGHT", "SET_OPACITY"):
                s = js_str(self.val(n["value"], loc))
                if is_numeric(s):
                    x = to_number(s)
                    key = t[4:].lower()
                    if key in ("width", "height"):
                        x = max(0.0, x)
                    if key == "opacity":
                        x = max(0.0, min(1.0, x))
                    self.state[key] = x
            n = n["cont"]
        return loc

    def get_value(self, node: dict, res: dict) -> str:
        loc = self.run(node, {}, False)
        return js_str(self.val(res, loc))

    def get_state(self, node: dict) -> dict:
        self.state = {}
        self.run(node, {}, False)
        return dict(self.state)


def calc(op: str, a, b):
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    if op == "ADD":
        if isinstance(a, bool):
            return True if a else (b if isinstance(b, bool) else (b != 0 if num(b) else b != ""))
        if num(a):
            return a + b if num(b) else (a + (1 if b else 0) if isinstance(b, bool) else a)
        return a + b if isinstance(b, str) else a
    if op == "SUB":
        if isinstance(a, bool):
            return a if a is False else (b == 0 if num(b) else (not b if isinstance(b, bool) else b == ""))
        if num(a):
            return a - b if num(b) else (a - (1 if b else 0) if isinstance(b, bool) else a)
        return a
    if op == "MUL":
        if isinstance(a, bool):
            return False if a is False else b
        if num(a):
            if isinstance(b, bool):
                return a if b else 0
            if num(b):
                return a * b
            return b * max(0, int(a))
        if isinstance(b, bool):
            return a if b else ""
        return a * max(0, int(b)) if num(b) else a
    if op == "DIV":
        if num(a) and num(b) and not isinstance(b, bool):
            return a if b == 0 else js_round(a * 100 / b) / 100
        return a
    return a


# ---------------------------------------------------------------- renderer

FONT_CANDIDATES = {
    "Darwin": ["/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/Supplemental/AppleGothic.ttf"],
    "Linux": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
              "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"],
    "Windows": [os.path.expandvars(r"%WINDIR%\Fonts\malgun.ttf"), os.path.expandvars(r"%WINDIR%\Fonts\gulim.ttc")],
}


def _font_path(override: str | None) -> str | None:
    if override:
        return override
    from .config import get
    saved = get("font")
    if saved and os.path.exists(saved):
        return saved
    for p in FONT_CANDIDATES.get(platform.system(), []):
        if os.path.exists(p):
            return p
    return None


def _load_image(src: str):
    from PIL import Image
    if not src:
        return None
    path = src
    if src.startswith(("http://", "https://")):
        os.makedirs(CACHE, exist_ok=True)
        path = os.path.join(CACHE, hashlib.sha1(src.encode()).hexdigest())
        if not os.path.exists(path):
            # Percent-encode non-ASCII (a hosted `…/호박집/초롱_3.webp`) the way a browser does;
            # characters already escaped or reserved in a URL stay as they are.
            url = urllib.parse.quote(src, safe=":/?#[]@!$&'()*+,;=%~")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
            except (OSError, ValueError):    # 404 or a bad URL: the site draws nothing either
                return None
            with open(path, "wb") as f:      # only a full download enters the cache
                f.write(data)
    if not os.path.isfile(path):
        return None
    try:
        im = Image.open(path)
        im.seek(0)
        return im.convert("RGBA")
    except OSError:
        return None


def _fit(box_w, box_h, im_w, im_h, mode):
    ar, br = im_w / im_h, box_w / box_h
    w, h = box_w, box_h
    if mode == "cover":
        if ar > br:
            w = box_h * ar
        else:
            h = box_w / ar
    else:
        if ar > br:
            h = box_w / ar
        else:
            w = box_h * ar
    return w, h, (box_w - w) / 2, (box_h - h) / 2


def wrap(text: str, width: float, measure, white_space: str) -> list[str]:
    if white_space == "normal":
        text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    out = []

    def by_char(word):
        g, v = [], ""
        for ch in word:
            if measure(v + ch) <= width:
                v += ch
            elif v == "":
                g.append(ch)
            else:
                g.append(v)
                v = ch
        if v:
            g.append(v)
        return g

    for para in text.split("\n"):
        if para == "":
            out.append("")
            continue
        cur, lines = "", []
        for w in para.split(" "):
            cand = w if cur == "" else f"{cur} {w}"
            if measure(cand) <= width:
                cur = cand
            elif cur == "":
                lines += by_char(w)
            else:
                lines.append(cur)
                if measure(w) <= width:
                    cur = w
                else:
                    k = by_char(w)
                    lines += k[:-1]
                    cur = k[-1]
        if cur:
            lines.append(cur)
        out += lines
    return out


def _parse_color(c: str):
    from PIL import ImageColor
    c = (c or "#FFFFFF").strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{8})", c)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
    m = re.fullmatch(r"#([0-9a-fA-F]{4})", c)
    if m:
        return tuple(int(ch * 2, 16) for ch in m.group(1))
    try:
        rgb = ImageColor.getrgb(c)
        return rgb if len(rgb) == 4 else (*rgb, 255)
    except ValueError:
        return (255, 255, 255, 255)


def render(raw: dict, values: dict, *, now: float = 1.0e12, after: float = 0, mouse=(0, 0), first=False, last=True, events=(),
           char_images: dict | None = None, username: str = "유저", font: str | None = None, boxes=False,
           selected: int | None = None, out: str | None = None, scale: float = 1.0) -> dict:
    """Render a rawTemplate to PNG. events: list of ('load'|'key:<code>'|'click:<sprite index>'),
    run at timer = now - after, then the frame is drawn at timer = now."""
    from PIL import Image, ImageDraw, ImageFont

    var_list = raw["variables"]
    system = [v["varName"] for v in var_list if v["mode"] == "none"]
    e = {v["varName"]: "" for v in var_list}
    e.update({k: str(v) for k, v in values.items()})
    storage = {"images": {d["imageName"]: d["url"] for d in raw.get("imageStorage", [])},
               "audio": {d["audioName"]: d["url"] for d in raw.get("audioStorage", [])}}
    host = {"now": now - after, "mouse": mouse, "first": first, "last": last}
    it = Interp(e, system, storage, host)
    sprites = raw["sprites"]
    log = []
    for ev in events:
        if ev == "load":
            for tr in raw.get("triggers", []):
                if tr["triggerCond"]["type"] == "ONMOUNTED":
                    it.run(tr["node"], {}, True)
        elif ev.startswith("key:"):
            for tr in raw.get("triggers", []):
                if tr["triggerCond"]["type"] == "KEYDOWN" and tr["triggerCond"].get("key") == ev[4:]:
                    it.run(tr["node"], {}, True)
        elif ev.startswith(("click:", "tap:")):
            # click:<sprite index> aims at that sprite's current centre; tap:x,y at a point.
            # Like the site, the first sprite from index 0 that is a visible image with a
            # non-empty action and contains the point receives the click.
            def geom(s):
                st = it.get_state(s["stateScript"]["node"])
                return (st.get("visibility", 1), st.get("x", s["x"]), st.get("y", s["y"]),
                        st.get("width", s["width"]), st.get("height", s["height"]))
            if ev.startswith("click:"):
                _, x, y, w, h = geom(sprites[int(ev[6:])])
                px, py = x + w / 2, y + h / 2
            else:
                px, py = (float(t) for t in ev[4:].split(","))
            got = None
            for si, s in enumerate(sprites):
                if s["spriteType"] != "image" or s["actionScript"]["node"]["type"] == "NULL":
                    continue
                vis, x, y, w, h = geom(s)
                if vis != 0 and x <= px <= x + w and y <= py <= y + h:
                    it.run(s["actionScript"]["node"], {}, True)
                    got = si
                    break
            it.events.insert(0, f"@({px:.0f},{py:.0f}) → " + (f"sprite {got}" if got is not None else "받은 레이어 없음"))
        log.append((ev, list(it.events)))
        it.events.clear()
    host["now"] = now

    W, H = int(1280 * scale), int(720 * scale)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    fpath = _font_path(font)
    font_cache = {}

    def get_font(size):
        key = round(size * scale, 2)
        if key not in font_cache:
            font_cache[key] = ImageFont.truetype(fpath, max(1, int(key))) if fpath else ImageFont.load_default()
        return font_cache[key]

    states = []
    for i in range(len(sprites) - 1, -1, -1):
        s = sprites[i]
        st = it.get_state(s["stateScript"]["node"])
        g = {"visibility": st.get("visibility", 1), "x": st.get("x", s["x"]), "y": st.get("y", s["y"]),
             "width": st.get("width", s["width"]), "height": st.get("height", s["height"]), "opacity": st.get("opacity", 1)}
        info = {"index": i, "type": s["spriteType"], "name": s.get("objectName"), **{k: round(v, 2) if isinstance(v, float) else v for k, v in g.items()}}
        states.append(info)
        if g["visibility"] == 0 or g["width"] <= 0 or g["height"] <= 0:
            continue
        bx, by, bw, bh = (g[k] * scale for k in ("x", "y", "width", "height"))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if s["spriteType"] in ("image", "frame"):
            if s["spriteType"] == "image":
                src, mode = s.get("imageURL", ""), "contain"
            else:
                a = s["assignedValueScript"]
                val = it.get_value(a["node"], a["res"])
                info["value"] = val
                src = (char_images or {}).get(val, val)
                mode = s.get("objectFit", "contain")
            im = _load_image(src)
            if im is None:
                d = ImageDraw.Draw(layer)
                d.rectangle([bx, by, bx + bw, by + bh], outline=(255, 80, 200, 200), width=2)
                d.text((bx + 6, by + 6), f"[{s['spriteType']}] {src[:40] or '(빈 값)'}", fill=(255, 80, 200, 230), font=get_font(16))
            else:
                w, h, ox, oy = _fit(bw, bh, im.width, im.height, mode)
                im2 = im.resize((max(1, round(w)), max(1, round(h))), Image.LANCZOS)
                tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                tmp.paste(im2, (round(bx + ox), round(by + oy)))  # onto a clear layer: copy RGBA as is
                mask = Image.new("L", (W, H), 0)
                md = ImageDraw.Draw(mask)
                rad = (s.get("borderRadius", 0) or 0) * scale if s["spriteType"] == "frame" else 0
                md.rounded_rectangle([bx, by, bx + bw - 1, by + bh - 1], radius=rad, fill=255)
                layer.paste(tmp, (0, 0), mask)
        else:
            a = s["assignedValueScript"]
            val = it.get_value(a["node"], a["res"]).replace("{user}", username)
            info["value"] = val
            size = s.get("fontSize", 32) or 32
            f = get_font(size)
            measure = lambda txt: f.getlength(txt) / scale  # the site measures lines with '*' included
            lines = wrap(val, g["width"], measure, s.get("whiteSpace", "normal"))
            info["lines"] = len(lines)
            overflow_px = size * 0.2 + size * 1.4 * len(lines) - g["height"]
            if overflow_px > 0:
                info["overflow"] = f"{overflow_px:.0f}px 넘침" + (" (스크롤 가능)" if s.get("overflow") == "scroll" else " (잘림)")
            col = _parse_color(s.get("color", "#FFFFFF"))
            txt_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(txt_layer)
            yy = g["y"] + size * 0.2
            dim = False
            for ln in lines:
                lw = measure(ln)
                xx = g["x"] + ((g["width"] - lw) / 2 if s.get("textAlign") == "center" else (g["width"] - lw) if s.get("textAlign") == "right" else 0)
                for part_i, part in enumerate(ln.split("*")):
                    if part_i > 0:
                        dim = not dim
                    if part:
                        alpha = int(col[3] * (0.7 if dim else 1.0))
                        d.text((xx * scale, yy * scale), part, font=f, fill=(*col[:3], alpha))
                        xx += f.getlength(part) / scale
                yy += size * 1.4
            clip = Image.new("L", (W, H), 0)
            ImageDraw.Draw(clip).rectangle([bx, by, bx + bw - 1, by + bh - 1], fill=255)
            layer.paste(txt_layer, (0, 0), clip)
        if g["opacity"] < 1:
            alpha = layer.getchannel("A").point(lambda p: int(p * g["opacity"]))
            layer.putalpha(alpha)
        canvas = Image.alpha_composite(canvas, layer)

    if boxes:
        d = ImageDraw.Draw(canvas)
        for info in states:
            col = (0, 246, 182, 255) if info["visibility"] else (120, 120, 120, 200)
            if info["index"] == selected:
                col = (255, 210, 0, 255)
            x, y, w, h = (info[k] * scale for k in ("x", "y", "width", "height"))
            d.rectangle([x, y, x + w, y + h], outline=col, width=1)
            d.text((x + 3, y + 2), f"{info['index']}:{info['name']}", fill=col, font=get_font(12))
    if out:
        canvas.convert("RGB").save(out)
    states.sort(key=lambda s: s["index"])
    return {"states": states, "events": log, "values": dict(e), "font": fpath}
