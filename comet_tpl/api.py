"""Comet HTTP API (api.comet-talk.ai) with the express session cookie `connect.sid`.

Endpoints (observed in the site bundle and verified live, 2026-10-16):
  GET  /auth/user                      logged-in user
  POST /upload/image   multipart image  -> {"imageUrl"}   jpeg|png|webp|gif, <=5MB
  POST /upload/audio   multipart audio  -> {"audioUrl"}   mpeg|mp3|wav|ogg|webm, <=5MB
  POST /upload/newtemplate   JSON post  -> 200 | 400 {"msg","goto"(tab)}
  POST /upload/template/{id} JSON post  -> same, edits in place
  GET  /get/mytemplate                 own uploads (newest first)
  GET  /get/postTemplate/{id}          editable post of an own template
  GET  /get/ownTemplate                templates usable in cinema chats (owned + bought)
  GET  /get/detail/template/{id}       public detail page data
  POST /delete/template/{id}
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid

from .config import CONFIG_DIR

API = "https://api.comet-talk.ai"
SITE = "https://comet-talk.ai"
SESSION_FILE = os.path.join(CONFIG_DIR, "session.json")
CACHE_FILE = os.path.join(CONFIG_DIR, "uploads.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0 Safari/537.36"
IMAGE_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
AUDIO_MIME = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/ogg", "audio/webm"}
MAGIC = [(b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"), (b"ID3", "audio/mpeg"),
         (b"\xff\xfb", "audio/mpeg"), (b"\xff\xf3", "audio/mpeg"), (b"\xff\xf2", "audio/mpeg"), (b"OggS", "audio/ogg"),
         (b"\x1aE\xdf\xa3", "audio/webm")]


class ApiError(RuntimeError):
    def __init__(self, status: int, msg: str, goto=None):
        self.status, self.msg, self.goto = status, msg, goto
        tab = {1: "기본 정보", 2: "프롬프트·변수·스크립트", 3: "에디터", 4: "스토리지", 5: "트리거"}.get(goto, "")
        super().__init__(f"HTTP {status}: {msg}" + (f" (편집기 {goto}번 탭: {tab})" if goto else ""))


def sniff_mime(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(16)
    for sig, mime in MAGIC:
        if head.startswith(sig):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_session() -> dict | None:
    sid = os.environ.get("COMET_SID")
    if sid:
        return {"sid": sid, "source": "env COMET_SID"}
    try:
        with open(SESSION_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_session(sid: str, source: str, user: dict | None = None):
    _save_json(SESSION_FILE, {"sid": sid, "source": source, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "email": (user or {}).get("email"), "user_id": (user or {}).get("id")})


class Client:
    def __init__(self, sid: str | None = None):
        sess = {"sid": sid} if sid else load_session()
        if not sess or not sess.get("sid"):
            raise ApiError(401, "세션이 없습니다. `comet login`을 먼저 실행하세요")
        self.sid = sess["sid"]
        self.last_cached = False
        self._persist = sid is None and "source" in sess and sess["source"] != "env COMET_SID"

    # -- transport
    def request(self, method: str, path: str, body: bytes | None = None, ctype: str | None = None, timeout=60):
        headers = {"Cookie": f"connect.sid={self.sid}", "User-Agent": UA, "Origin": SITE, "Referer": SITE + "/",
                   "Accept": "application/json, */*"}
        if ctype:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(API + path, data=body, method=method, headers=headers)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    self._rotate(r.headers.get_all("Set-Cookie") or [])
                    raw = r.read()
                    return r.status, raw
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code >= 500 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                msg, goto = raw.decode("utf-8", "replace")[:500], None
                try:
                    j = json.loads(raw)
                    msg, goto = j.get("msg", msg), j.get("goto")
                except ValueError:
                    pass
                if e.code == 401:
                    msg = "로그인이 풀렸습니다. `comet login`으로 세션을 다시 가져오세요"
                raise ApiError(e.code, msg, goto) from None
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ApiError(0, f"연결 실패: {e.reason}") from None
        raise ApiError(0, "unreachable")

    def _rotate(self, cookies: list[str]):
        for c in cookies:
            if c.startswith("connect.sid="):
                new = c.split(";", 1)[0].split("=", 1)[1]
                if new and new != self.sid:
                    self.sid = new
                    if self._persist:
                        sess = load_session() or {}
                        sess["sid"] = new
                        _save_json(SESSION_FILE, sess)

    def get(self, path: str):
        _, raw = self.request("GET", path)
        return json.loads(raw) if raw else None

    def post_json(self, path: str, data) -> tuple[int, bytes]:
        return self.request("POST", path, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    # -- account
    def user(self) -> dict:
        return self.get("/auth/user")

    # -- media
    def upload_file(self, path: str, kind: str) -> str:
        """kind: image | audio. Uploads (deduplicated by content hash) and returns the CDN URL."""
        data = open(path, "rb").read()
        if len(data) > 5 * 1024 * 1024:
            raise ApiError(413, f"{path}: 5MB 초과")
        mime = sniff_mime(path)
        allowed = IMAGE_MIME if kind == "image" else AUDIO_MIME
        if mime not in allowed:
            raise ApiError(415, f"{path}: {mime}는 {kind} 업로드 형식이 아닙니다({', '.join(sorted(allowed))})")
        digest = hashlib.sha256(data).hexdigest()
        cache = _load_cache()
        self.last_cached = digest in cache
        if self.last_cached:
            return cache[digest]["url"]
        boundary = "----comet" + uuid.uuid4().hex
        field = "image" if kind == "image" else "audio"
        fname = os.path.basename(path).encode("utf-8", "replace").decode("latin-1", "replace")
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{fname}\"\r\n"
                f"Content-Type: {mime}\r\n\r\n").encode("latin-1") + data + f"\r\n--{boundary}--\r\n".encode()
        _, raw = self.request("POST", f"/upload/{field}", body, f"multipart/form-data; boundary={boundary}", timeout=120)
        url = json.loads(raw)["imageUrl" if kind == "image" else "audioUrl"]
        cache[digest] = {"url": url, "file": os.path.abspath(path), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_json(CACHE_FILE, cache)
        return url

    # -- templates
    def my_templates(self) -> list[dict]:
        return self.get("/get/mytemplate").get("mytemplates", [])

    def own_templates(self) -> list[dict]:
        return self.get("/get/ownTemplate").get("ownTemplates", [])

    def post_template(self, tid: str) -> dict:
        return self.get(f"/get/postTemplate/{tid}")["postTemplate"]

    def detail_template(self, tid: str) -> dict:
        return self.get(f"/get/detail/template/{tid}")

    def create_template(self, payload: dict) -> str:
        before = {t["templateId"] for t in self.my_templates()}
        self.post_json("/upload/newtemplate", payload)
        for _ in range(5):
            new = [t for t in self.my_templates() if t["templateId"] not in before]
            if new:
                return new[0]["templateId"]
            time.sleep(1)
        raise ApiError(0, "업로드는 성공했지만 새 템플릿 ID를 찾지 못했습니다(/get/mytemplate 확인)")

    def update_template(self, tid: str, payload: dict):
        self.post_json(f"/upload/template/{tid}", payload)

    def delete_template(self, tid: str):
        self.request("POST", f"/delete/template/{tid}")


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def links(tid: str) -> dict:
    return {"edit": f"{SITE}/build/template/{tid}", "detail": f"{SITE}/detail/template/{tid}"}
