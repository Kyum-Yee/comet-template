"""External image hosting on Cloudflare Workers static assets (`comet host`).

Why a host at all: the cinema-chat image tab and template storage take only files uploaded to
image.comet-talk.ai ("외부 이미지가 감지되었습니다"), but a template frame whose value is not an
image-tab name is drawn from that value as a URL. A frame value of `HOST + 장면 + ".webp"` therefore
shows any number of hosted images without the image tab.

What the site's loader needs (bundle `index-*.js`, loader `Jde`, 2026-10-18):
- `img.crossOrigin = "anonymous"` → every response must carry `Access-Control-Allow-Origin`,
  or onerror fires and the frame stays empty.
- `fetch(url, {headers: {Range: "bytes=0-5"}})` and `bytes=0-50` (GIF / animated-WebP sniffing)
  → the host must answer the CORS preflight for `Range` (browsers that do not safelist it send one).
- CSP is `default-src 'self' https: …`, so any https host is allowed.

Layout: `<host dir>/wrangler.jsonc`, `worker.js` (CORS + preflight + cache in front of the assets),
`public/<작품>/<이미지 이름>.webp`. Static-asset requests on Workers are free; with
`run_worker_first` each request also counts one Worker invocation (free plan: 100,000 a day).
No R2 bucket is needed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from . import config

WRANGLER = "wrangler@4.147.0"       # pinned: npx fetches this exact version
DEFAULT_NAME = "comet-img"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
BAD_NAME = re.compile(r"[/\\?#%\"<>|]|^\s|\s$")   # breaks a URL path or a frame value

WORKER_JS = r"""// comet host: Cloudflare Workers static assets with CORS for comet-talk.ai frames.
// The site loads frame images with crossOrigin="anonymous" and sniffs them with Range fetches.
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Range",
  "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
};

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: { ...CORS, "Access-Control-Max-Age": "86400" } });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("method not allowed", { status: 405, headers: CORS });
    }
    const res = await env.ASSETS.fetch(request);
    // The site sniffs every image with Range: bytes=0-5 and 0-50; answer 206 so a viewer
    // downloads a few bytes there instead of the whole image twice more.
    const m = /^bytes=(\d+)-(\d*)$/.exec(request.headers.get("Range") || "");
    if (m && res.status === 200) {
      const buf = await res.arrayBuffer();
      const start = Number(m[1]);
      const end = Math.min(m[2] === "" ? buf.byteLength - 1 : Number(m[2]), buf.byteLength - 1);
      if (start > end) {
        return new Response(null, { status: 416, headers: { ...CORS, "Content-Range": `bytes */${buf.byteLength}` } });
      }
      const part = new Response(buf.slice(start, end + 1), { status: 206, headers: res.headers });
      part.headers.set("Content-Range", `bytes ${start}-${end}/${buf.byteLength}`);
      part.headers.set("Content-Length", String(end - start + 1));
      for (const [k, v] of Object.entries(CORS)) part.headers.set(k, v);
      return part;
    }
    const out = new Response(res.body, res);
    for (const [k, v] of Object.entries(CORS)) out.headers.set(k, v);
    out.headers.set("Accept-Ranges", "bytes");
    // An hour, not a day: a replaced image (same name, new picture) reaches viewers within the hour.
    if (res.ok) out.headers.set("Cache-Control", "public, max-age=3600");
    return out;
  },
};
"""


def host_dir() -> str:
    d = config.get("host_dir")
    if d:
        return d
    works = config.get("works_dir")
    return os.path.join(works, "호스팅") if works else os.path.expanduser("~/comet-host")


def _wrangler_config(name: str) -> str:
    return json.dumps({
        "name": name,
        "main": "worker.js",
        "compatibility_date": "2025-09-01",
        "workers_dev": True,
        "assets": {"directory": "public", "binding": "ASSETS", "run_worker_first": True},
    }, indent=2) + "\n"


def init(d: str | None = None, name: str | None = None, force: bool = False) -> list[str]:
    """Creates the host folder. Existing files stay unless `force`; returns the paths written."""
    d = os.path.abspath(os.path.expanduser(d or host_dir()))
    os.makedirs(os.path.join(d, "public"), exist_ok=True)
    files = {
        "wrangler.jsonc": _wrangler_config(name or DEFAULT_NAME),
        "worker.js": WORKER_JS,
        ".gitignore": ".wrangler/\nnode_modules/\n",
        ".assetsignore": ".DS_Store\n",
    }
    wrote = []
    for fn, body in files.items():
        p = os.path.join(d, fn)
        if force or not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            wrote.append(p)
    if config.get("host_dir") != d:
        config.set_key("host_dir", d)
    return wrote


def _require(d: str):
    if not os.path.exists(os.path.join(d, "wrangler.jsonc")):
        raise SystemExit(f"호스팅 폴더가 아닙니다: {d}  (`comet host init` 먼저)")


def _sources(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            out += sorted(os.path.join(p, f) for f in os.listdir(p)
                          if os.path.splitext(f)[1].lower() in IMAGE_EXT)
        elif os.path.isfile(p):
            out.append(p)
        else:
            raise SystemExit(f"없는 경로: {p}")
    return out


def add(work: str, paths: list[str], quality: int = 90, max_side: int | None = None,
        keep_gif: bool = True) -> list[tuple[str, str, int]]:
    """Converts images to public/<work>/<name>.webp. The file stem is the image name the AI writes.

    GIFs are copied as they are (the site animates GIF and animated WebP). A target newer than its
    source is skipped. Returns (name, state, bytes) rows; state: new | updated | same.
    """
    from PIL import Image
    if BAD_NAME.search(work) or not work:
        raise SystemExit(f"작품 폴더 이름에 쓸 수 없는 글자: {work!r}")
    d = host_dir()
    _require(d)
    out_dir = os.path.join(d, "public", work)
    os.makedirs(out_dir, exist_ok=True)
    rows, seen = [], {}
    for src in _sources(paths):
        stem, ext = os.path.splitext(os.path.basename(src))
        if BAD_NAME.search(stem):
            raise SystemExit(f"이미지 이름에 쓸 수 없는 글자(/ \\ ? # % 따옴표 <>| 앞뒤 공백): {stem!r}")
        if stem in seen:
            raise SystemExit(f"같은 이름이 두 번: {seen[stem]} · {src}")
        seen[stem] = src
        gif = keep_gif and ext.lower() == ".gif"
        dst = os.path.join(out_dir, stem + (".gif" if gif else ".webp"))
        state = "new" if not os.path.exists(dst) else "updated"
        if state == "updated" and os.path.getmtime(dst) >= os.path.getmtime(src):
            rows.append((stem, "same", os.path.getsize(dst)))
            continue
        if gif:
            shutil.copy2(src, dst)
        else:
            im = Image.open(src)
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
            if max_side and max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.LANCZOS)
            im.save(dst, "WEBP", quality=quality, method=6)
        rows.append((stem, state, os.path.getsize(dst)))
    return rows


def listing(work: str | None = None) -> dict[str, list[str]]:
    pub = os.path.join(host_dir(), "public")
    works = [work] if work else sorted(w for w in os.listdir(pub) if os.path.isdir(os.path.join(pub, w)))
    return {w: sorted(os.path.splitext(f)[0] for f in os.listdir(os.path.join(pub, w))
                      if not f.startswith(".")) for w in works if os.path.isdir(os.path.join(pub, w))}


def deploy() -> str | None:
    """`npx wrangler deploy` in the host folder. Auth: CLOUDFLARE_API_TOKEN (+ CLOUDFLARE_ACCOUNT_ID)
    or a prior `npx wrangler login`. Saves and returns the workers.dev base URL."""
    d = host_dir()
    _require(d)
    if not shutil.which("npx"):
        raise SystemExit("npx가 없습니다: Node.js를 설치하세요(wrangler를 npx로 부릅니다)")
    r = subprocess.run(["npx", "--yes", WRANGLER, "deploy"], cwd=d, text=True, capture_output=True)
    print(r.stdout[-3000:], r.stderr[-3000:] if r.returncode else "", sep="")
    if r.returncode:
        raise SystemExit(f"wrangler deploy 실패(종료 코드 {r.returncode})")
    m = re.search(r"https://[\w.-]+\.workers\.dev", r.stdout)
    if m:
        config.set_key("host_url", m.group(0))
    return m.group(0) if m else None


def url_for(work: str, name: str = "") -> str:
    base = config.get("host_url")
    if not base:
        raise SystemExit("host_url이 없습니다: `comet host deploy` 뒤에 저장되거나 `comet config set host_url …`")
    return f"{base.rstrip('/')}/{urllib.parse.quote(work)}/{urllib.parse.quote(name)}"


def _req(url: str, method: str = "GET", headers: dict | None = None):
    # Cloudflare's bot rule (error 1010) refuses the default Python-urllib agent with 403.
    req = urllib.request.Request(url, method=method, headers={
        "Origin": "https://comet-talk.ai", "User-Agent": "Mozilla/5.0 (comet host check)", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read(64)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), b""


def verify(url: str) -> list[tuple[bool, str, str]]:
    """What the site's loader does to one hosted image. (passed, check, detail) rows."""
    rows = []
    st, h, body = _req(url)
    acao = h.get("Access-Control-Allow-Origin") or h.get("access-control-allow-origin")
    rows.append((st == 200, "GET 200", f"{st} {h.get('Content-Type', '')}"))
    rows.append((acao in ("*", "https://comet-talk.ai"), "CORS 헤더(crossOrigin=anonymous)", str(acao)))
    st, h, _ = _req(url, "OPTIONS", {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "range"})
    allow = (h.get("Access-Control-Allow-Headers") or "").lower()
    rows.append((200 <= st < 300 and "range" in allow, "Range 프리플라이트", f"{st} allow-headers={allow or '-'}"))
    st, h, part = _req(url, headers={"Range": "bytes=0-15"})
    sniff = part[:4] == b"RIFF" and part[8:12] == b"WEBP" or part[:6] in (b"GIF87a", b"GIF89a")
    rows.append((st == 206 and sniff, "Range 206·형식 판별", f"{st} {h.get('Content-Range', '-')} {part[:12]!r}"))
    return rows


def frame_snippet(work: str, var: str) -> str:
    base = url_for(work)
    return (f'# logic.py (Python) — 전역 변수를 return 식에 직접 써야 fade·cross-fade가 돈다\n'
            f'HOST = "{base}"\n\n@value("<프레임 레이어 이름>")\ndef _():\n    return f"{{HOST}}{{{var}}}.webp"\n\n'
            f'# DSL: "{base}" .. ${var} .. ".webp"')
