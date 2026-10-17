"""Pull the Comet session cookie out of the user's own Chrome without touching the Keychain.

Chrome encrypts cookies with a key only Chrome itself may read. So instead of decrypting, we copy
the logged-in profile to a temp dir, start Chrome headless on that copy with a DevTools port, and
ask Chrome (CDP Storage.getCookies) for the decrypted values. The real browser is never touched.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import platform
import shutil
import socket
import sqlite3
import struct
import subprocess
import tempfile
import time
import urllib.request

COOKIE_HOST = "comet-talk.ai"


def chrome_paths() -> tuple[str | None, list[str]]:
    sysname = platform.system()
    if sysname == "Darwin":
        bins = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium"]
        dirs = ["~/Library/Application Support/Google/Chrome", "~/Library/Application Support/Chromium"]
    elif sysname == "Windows":
        bins = [os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")]
        dirs = [r"%LocalAppData%\Google\Chrome\User Data"]
    else:
        bins = [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
        dirs = ["~/.config/google-chrome", "~/.config/chromium"]
    binary = next((b for b in bins if b and os.path.exists(b)), None)
    return binary, [os.path.expanduser(os.path.expandvars(d)) for d in dirs if os.path.isdir(os.path.expanduser(os.path.expandvars(d)))]


def find_profiles(user_dir: str) -> list[tuple[float, str]]:
    """Profiles holding a comet connect.sid, newest cookie access first."""
    found = []
    for db in glob.glob(os.path.join(user_dir, "*", "Cookies")):
        tmp = tempfile.mktemp(suffix=".db")
        try:
            shutil.copy2(db, tmp)
            con = sqlite3.connect(tmp)
            row = con.execute("select max(last_access_utc) from cookies where host_key like ? and name='connect.sid'",
                              (f"%{COOKIE_HOST}",)).fetchone()
            con.close()
            if row and row[0]:
                found.append((row[0], os.path.basename(os.path.dirname(db))))
        except sqlite3.Error:
            pass
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return sorted(found, reverse=True)


# ---------------------------------------------------------------- minimal websocket (CDP over localhost)

class _WS:
    def __init__(self, url: str):
        assert url.startswith("ws://")
        hostport, path = url[5:].split("/", 1)
        host, port = hostport.split(":")
        self.s = socket.create_connection((host, int(port)), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n", 1)[0]:
            raise ConnectionError(resp.split(b"\r\n", 1)[0].decode())
        self.buf = resp.split(b"\r\n\r\n", 1)[1]

    def _recv(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.s.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> dict:
        msg = b""
        while True:
            b0, b1 = self._recv(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv(8))[0]
            payload = self._recv(n)
            op = b0 & 0x0F
            if op in (0x1, 0x0):
                msg += payload
                if b0 & 0x80:
                    return json.loads(msg)
            elif op == 0x8:
                raise ConnectionError("websocket closed by peer")

    def call(self, method: str, params: dict | None = None, mid: int = 1) -> dict:
        self.send({"id": mid, "method": method, "params": params or {}})
        while True:
            m = self.recv()
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m["result"]

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def comet_profiles(user_dir: str | None = None) -> list[tuple[float, str, str]]:
    """(last cookie access, user data dir, profile) for every profile logged in to Comet, newest first."""
    _, dirs = chrome_paths()
    dirs = [user_dir] if user_dir else dirs
    return sorted(((t, d, p) for d in dirs for t, p in find_profiles(d)), reverse=True)


def sid_from_chrome(profile: str | None = None, user_dir: str | None = None, log=print) -> tuple[str, str]:
    binary, _ = chrome_paths()
    if not binary:
        raise RuntimeError("Chrome 실행 파일을 찾지 못했습니다. `comet login --sid <값>`으로 직접 넣으세요")
    candidates = comet_profiles(user_dir)
    if profile:
        picked = [c for c in candidates if c[2] == profile]
        if candidates and not picked:
            raise RuntimeError(f"크롬 프로필 '{profile}'에 코멧 로그인이 없습니다. 로그인된 프로필: "
                               + ", ".join(c[2] for c in candidates) + " (`comet config set chrome_profile <이름>`)")
        candidates = picked
    if not candidates:
        raise RuntimeError("comet-talk.ai에 로그인된 Chrome 프로필이 없습니다. 크롬에서 코멧에 로그인한 뒤 다시 실행하세요")
    _, src_dir, prof = candidates[0]
    log(f"[login] 프로필 '{prof}' ({src_dir})")
    work = tempfile.mkdtemp(prefix="comet-login-")
    proc = None
    try:
        dst = os.path.join(work, prof)
        os.makedirs(dst)
        if os.path.exists(os.path.join(src_dir, "Local State")):
            shutil.copy2(os.path.join(src_dir, "Local State"), work)
        for name in ("Cookies", "Cookies-journal", "Preferences"):
            p = os.path.join(src_dir, prof, name)
            if os.path.exists(p):
                shutil.copy2(p, dst)
        proc = subprocess.Popen([binary, f"--user-data-dir={work}", f"--profile-directory={prof}", "--headless=new",
                                 "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check",
                                 "--disable-sync", "--disable-extensions", "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = os.path.join(work, "DevToolsActivePort")
        for _ in range(100):
            if os.path.exists(port_file) and open(port_file).read().strip():
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("헤드리스 Chrome이 DevTools 포트를 열지 않았습니다")
        port = open(port_file).read().split()[0]
        ver = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=10).read())
        ws = _WS(ver["webSocketDebuggerUrl"])
        try:
            cookies = ws.call("Storage.getCookies")["cookies"]
        finally:
            ws.close()
        sid = next((c["value"] for c in cookies if c["name"] == "connect.sid" and COOKIE_HOST in c["domain"]), None)
        if not sid:
            raise RuntimeError("복사본에서 connect.sid를 읽지 못했습니다(쿠키 복호화 실패). `comet login --sid`를 쓰세요")
        return sid, f"chrome:{prof}"
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(work, ignore_errors=True)
