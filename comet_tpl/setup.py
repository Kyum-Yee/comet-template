"""First-run setup: environment check, install (skill folder + `comet` command), settings, login.

`comet setup` asks one question per step when run in a terminal; `--yes` takes every default
without asking (CI, agents). Each step is safe to re-run: installs replace only their own files,
settings keep what is already saved unless a new value is given.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

from . import config

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_NAME = "comet-template"
DEFAULT_SKILLS_DIR = "~/.claude/skills"
DEFAULT_BIN_DIR = "~/.local/bin"
SKIP = shutil.ignore_patterns("__pycache__", ".git", ".github", "*.comet.json", ".pytest_cache", "*.pyc")


# ---------------------------------------------------------------- environment check

def _version(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout or r.stderr).strip().splitlines()[0] if r.returncode == 0 else None


def checks() -> list[tuple[str, str, str, str]]:
    """(level, item, state, fix). level: ok | need (a core command fails without it) | opt (one feature)."""
    from . import auth, render
    rows = []
    py = sys.version_info
    rows.append(("ok" if py >= (3, 10) else "need", "Python ≥ 3.10", platform.python_version(),
                 "python.org 또는 `uv python install 3.12`"))
    uv = shutil.which("uv")
    rows.append(("ok" if uv else "opt", "uv", _version([uv, "--version"]) if uv else "없음",
                 "https://docs.astral.sh/uv/ — 있으면 pillow를 저절로 받는다"))
    try:
        import PIL
        rows.append(("ok", "Pillow", PIL.__version__, ""))
    except ImportError:
        rows.append(("ok" if uv else "need", "Pillow", "uv가 실행 때 받음" if uv else "없음",
                     "uv를 설치하거나 `python3 -m pip install --user 'pillow>=10'` (preview·assets·upload 썸네일)"))
    node = shutil.which("node")
    rows.append(("ok" if node else "opt", "node", _version([node, "--version"]) if node else "없음",
                 "regex test·macro test가 JS를 그대로 돌릴 때만. 없으면 check는 정적 검사"))
    font = render._font_path(None)
    rows.append(("ok" if font else "opt", "한글 글꼴", font or "못 찾음",
                 "`comet config set font <ttf/otf/ttc 경로>` (미리보기 글자)"))
    binary, dirs = auth.chrome_paths()
    rows.append(("ok" if binary else "opt", "Chrome", binary or "없음",
                 "없으면 `comet login --sid <connect.sid 값>`으로 로그인"))
    if binary:
        profs = auth.comet_profiles(config.get("chrome_dir"))
        rows.append(("ok" if profs else "opt", "코멧 로그인된 프로필", ", ".join(p for _, _, p in profs) or "없음",
                     "크롬에서 comet-talk.ai에 로그인한 뒤 `comet login`"))
    from . import api
    sess = api.load_session()
    rows.append(("ok" if sess else "opt", "세션", (sess or {}).get("email") or (sess or {}).get("source") or "없음",
                 "`comet login` (upload·list·pull에 필요)"))
    bin_dir = os.path.expanduser(DEFAULT_BIN_DIR)
    on_path = shutil.which("comet")
    rows.append(("ok" if on_path else "opt", "comet 명령", on_path or "PATH에 없음",
                 f"`comet setup`이 {DEFAULT_BIN_DIR}에 설치. PATH에 {bin_dir} 추가"))
    return rows


def _pad(s: str, width: int) -> str:
    """Left-justify by terminal columns: Hangul and other wide characters take two."""
    import unicodedata
    cols = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)
    return s + " " * max(1, width - cols)


def doctor(out=print) -> int:
    rows = checks()
    mark = {"ok": "✓", "need": "✗", "opt": "△"}
    for level, item, state, fix in rows:
        out(f"{mark[level]} {_pad(item, 20)}{state}")
        if level != "ok" and fix:
            out(f"  └ {fix}")
    need = sum(1 for r in rows if r[0] == "need")
    out(f"— 필수 부족 {need} · 선택 부족 {sum(1 for r in rows if r[0] == 'opt')}")
    return 1 if need else 0


# ---------------------------------------------------------------- install

BACKUP_DIR = os.path.expanduser("~/.cache/comet-template/backup")


def install_skill(skills_dir: str, link: bool = False) -> tuple[str, str | None]:
    """Copies (or links) this folder to <skills_dir>/comet-template.

    Returns (installed path, where the previous copy was moved or None). A previous copy is moved
    out of the skills folder, never deleted, so local edits to it survive an update.
    """
    dst = os.path.join(os.path.expanduser(skills_dir), SKILL_NAME)
    if os.path.realpath(dst) == os.path.realpath(HERE):
        return dst, None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    backup = None
    if os.path.islink(dst):
        os.remove(dst)
    elif os.path.isdir(dst):
        if not os.path.exists(os.path.join(dst, "comet_tpl")):
            raise RuntimeError(f"{dst}에 comet-template가 아닌 폴더가 있습니다. 옮긴 뒤 다시 실행하세요")
        import time
        stamp = os.path.join(BACKUP_DIR, time.strftime("%Y%m%d-%H%M%S"))
        backup, n = stamp, 1
        while os.path.exists(backup):
            backup, n = f"{stamp}-{n}", n + 1
        os.makedirs(BACKUP_DIR, exist_ok=True)
        shutil.move(dst, backup)
    if link:
        os.symlink(HERE, dst, target_is_directory=True)
    else:
        shutil.copytree(HERE, dst, ignore=SKIP)
    return dst, backup


def shim_text(skill_dir: str, windows: bool) -> str:
    entry = os.path.join(skill_dir, "comet.py")
    if windows:
        return ("@echo off\r\nrem comet: Comet template tool (installed by `comet setup`)\r\n"
                "where uv >nul 2>nul\r\n"
                f"if %ERRORLEVEL%==0 (uv run --quiet --script \"{entry}\" %*) else (python \"{entry}\" %*)\r\n")
    q = entry.replace("'", "'\\''")
    return ("#!/bin/sh\n# comet: Comet template tool (installed by `comet setup`)\n"
            "if command -v uv >/dev/null 2>&1; then\n"
            f"  exec uv run --quiet --script '{q}' \"$@\"\nfi\n"
            f"exec python3 '{q}' \"$@\"\n")


def install_shim(skill_dir: str, bin_dir: str) -> tuple[str, bool]:
    """Writes the `comet` command. Returns (path, bin_dir is on PATH)."""
    windows = platform.system() == "Windows"
    bin_dir = os.path.expanduser(bin_dir)
    os.makedirs(bin_dir, exist_ok=True)
    path = os.path.join(bin_dir, "comet.cmd" if windows else "comet")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(shim_text(skill_dir, windows))
    if not windows:
        os.chmod(path, 0o755)
    on_path = any(os.path.realpath(p) == os.path.realpath(bin_dir)
                  for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    return path, on_path


# ---------------------------------------------------------------- wizard

def _ask(prompt: str, default: str | None, interactive: bool) -> str | None:
    if not interactive:
        return default
    shown = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{shown}: ").strip()
    except EOFError:
        return default
    if ans == "-":
        return None
    return ans or default


def _yes(prompt: str, default: bool, interactive: bool) -> bool:
    if not interactive:
        return default
    ans = _ask(prompt + (" (Y/n)" if default else " (y/N)"), None, True)
    return default if not ans else ans.lower().startswith("y")


def run(a, out=print) -> int:
    interactive = not a.yes and sys.stdin.isatty()
    out("comet-template 초기 세팅" + ("" if interactive else " (묻지 않고 기본값)"))
    if interactive:
        out("엔터 = [기본값], '-' = 비우기\n")

    out("1) 환경 점검")
    if doctor(lambda s: out("   " + s)) and not _yes("필수 항목이 부족합니다. 그래도 계속할까요?", False, interactive):
        return 1

    out("\n2) 설치")
    skill_dir = HERE
    if not a.no_install:
        skills = a.skills_dir or _ask("   스킬 폴더(Claude Code가 읽는 곳)", DEFAULT_SKILLS_DIR, interactive)
        if skills:
            skill_dir, backup = install_skill(skills, link=a.link)
            out(f"   ✓ 스킬 → {skill_dir}" + ("  (링크: 이 클론을 고치면 바로 반영)" if a.link else ""))
            if backup:
                out(f"   · 이전 설치본은 지우지 않고 옮김 → {backup}")
        bins = a.bin_dir or _ask("   comet 명령을 둘 폴더", DEFAULT_BIN_DIR, interactive)
        if bins:
            path, on_path = install_shim(skill_dir, bins)
            out(f"   ✓ 명령 → {path}")
            if not on_path:
                d = os.path.expanduser(bins)
                hint = (f'설정 → 환경 변수 → 사용자 Path에 {d}' if platform.system() == "Windows"
                        else f'셸 설정(~/.zshrc·~/.bashrc)에 export PATH="{d}:$PATH"')
                out(f"   △ PATH에 없습니다. 추가: {hint}")
    else:
        out("   (건너뜀: --no-install)")

    out("\n3) 설정  " + config.CONFIG_FILE)
    cfg = config.load()
    from . import auth
    profs = auth.comet_profiles(cfg.get("chrome_dir"))
    if profs:
        out("   코멧에 로그인된 크롬 프로필(최근 순): " + ", ".join(p for _, _, p in profs))
    answers = {
        "chrome_profile": a.chrome_profile or _ask("   크롬 프로필(비우면 가장 최근 것)", cfg["chrome_profile"], interactive),
        "username": a.username or _ask("   미리보기 {user} 이름", cfg["username"], interactive),
        "works_dir": a.works_dir or _ask("   완성품 폴더(작품별 하위 폴더)", cfg["works_dir"], interactive),
        "prompt_template": a.prompt_template or _ask("   내 프롬프트 템플릿 파일(없으면 비움)", cfg["prompt_template"], interactive),
    }
    font_now = cfg["font"]
    if a.font or (interactive and not font_now and not _font_found()):
        answers["font"] = a.font or _ask("   한글 글꼴 파일 경로", None, interactive)
    for k, v in answers.items():
        cfg[k] = config.normalize(k, v)
    for spec in a.material or []:
        path, _, when = spec.partition("=")
        config.put_material(cfg, path, when or "작업 전")
    while interactive and _yes("   창작 자료(캐릭터 체크리스트·세계관 문서 등)를 등록할까요?", False, True):
        path = _ask("     경로", None, True)
        if path:
            config.put_material(cfg, path, _ask("     언제 읽나(예: 캐릭터 설계 전)", "작업 전", True))
    if cfg.get("works_dir"):
        for sub in ("템플릿", "시네마챗", "노벨챗"):
            os.makedirs(os.path.join(cfg["works_dir"], sub), exist_ok=True)
    config.save(cfg)
    out("   ✓ 저장. 확인·수정: `comet config show` / `comet config set <키> <값>`")

    out("\n4) 로그인")
    from . import api
    if a.sid or a.login or (interactive and not a.no_login and _yes("   지금 코멧 세션을 가져올까요?", bool(profs), True)):
        try:
            if a.sid:
                sid, src = a.sid, "manual"
            else:
                sid, src = auth.sid_from_chrome(profile=cfg.get("chrome_profile"), user_dir=cfg.get("chrome_dir"),
                                                log=lambda s: out("   " + s))
            user = api.Client(sid=sid).user()
            api.save_session(sid, src, user)
            out(f"   ✓ {user.get('email')} ({src})")
        except (RuntimeError, api.ApiError) as e:
            out(f"   ✗ {e}\n   나중에: `comet login` 또는 `comet login --sid <값>`")
    else:
        out("   (건너뜀) 업로드 전에 `comet login`")

    out("\n끝. 다음: `comet new 내템플릿` → `comet check 내템플릿/template.json` → `comet preview …` → `comet upload …`")
    return 0


def _font_found() -> bool:
    from . import render
    return bool(render._font_path(None))
