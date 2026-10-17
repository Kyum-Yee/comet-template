"""Per-user settings: `<config dir>/config.json` beside the session file.

Every key is optional. Commands read their defaults from here (Chrome profile, preview font and
player name), and the agent reads `works_dir`, `prompt_template` and `materials` through
`comet config show` before it writes characters, prompts or finished works.
"""
from __future__ import annotations

import json
import os

CONFIG_DIR = os.path.expanduser(os.environ.get("COMET_CONFIG_DIR", "~/.config/comet-template"))
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# key -> (default, one-line meaning shown by `comet config show`)
KEYS = {
    "chrome_profile": (None, "comet login이 쓸 크롬 프로필 폴더 이름(예: 'Profile 1'). 비우면 코멧 쿠키가 가장 최근인 프로필"),
    "chrome_dir": (None, "크롬 사용자 데이터 폴더. 비우면 OS 기본 위치"),
    "font": (None, "미리보기·썸네일 한글 글꼴 파일. 비우면 OS 기본 한글 글꼴"),
    "username": ("유저", "미리보기에서 {user} 자리에 넣을 이름"),
    "works_dir": (None, "완성품을 둘 폴더. 아래에 템플릿/·시네마챗/·노벨챗/ 작품별 폴더"),
    "prompt_template": (None, "시네마챗·노벨챗 프롬프트를 시작할 내 템플릿 파일. 고정부는 그대로, 슬롯만 채운다"),
    "materials": ([], "창작 자료 목록 [{path, when}]: 에이전트가 'when' 단계 전에 읽는다"),
}
PATH_KEYS = {"chrome_dir", "font", "works_dir", "prompt_template"}


def load() -> dict:
    """Saved settings over defaults. A missing or broken file yields the defaults."""
    cfg = {k: (list(v[0]) if isinstance(v[0], list) else v[0]) for k, v in KEYS.items()}
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return cfg
    if isinstance(saved, dict):
        cfg.update({k: v for k, v in saved.items() if k in KEYS})
    return cfg


def get(key: str):
    return load().get(key)


def save(cfg: dict):
    """Writes only keys that differ from their defaults, so the file stays short and readable."""
    out = {k: v for k, v in cfg.items() if k in KEYS and v != KEYS[k][0]}
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_FILE)


def normalize(key: str, value: str | None):
    """CLI string -> stored value. Paths become absolute so the config works from any folder."""
    if key not in KEYS or key == "materials":
        raise KeyError(key)
    if value in (None, ""):
        return KEYS[key][0]
    if key in PATH_KEYS:
        return os.path.abspath(os.path.expanduser(value))
    return value


def set_key(key: str, value: str | None) -> dict:
    cfg = load()
    cfg[key] = normalize(key, value)
    save(cfg)
    return cfg


def put_material(cfg: dict, path: str, when: str) -> dict:
    """Adds or replaces (same path) one entry in memory. `when` is the step before which the agent reads it."""
    path = os.path.abspath(os.path.expanduser(path))
    cfg["materials"] = [m for m in cfg.get("materials", []) if m.get("path") != path] + [{"path": path, "when": when}]
    return cfg


def add_material(path: str, when: str) -> dict:
    cfg = put_material(load(), path, when)
    save(cfg)
    return cfg


def remove_material(path: str) -> bool:
    path = os.path.abspath(os.path.expanduser(path))
    cfg = load()
    kept = [m for m in cfg["materials"] if m.get("path") != path]
    if len(kept) == len(cfg["materials"]):
        return False
    cfg["materials"] = kept
    save(cfg)
    return True


def show(cfg: dict | None = None) -> str:
    cfg = cfg or load()
    lines = [f"# {CONFIG_FILE}" + ("" if os.path.exists(CONFIG_FILE) else "  (아직 없음: 모두 기본값)")]
    for k, (default, meaning) in KEYS.items():
        if k == "materials":
            continue
        v = cfg.get(k)
        mark = "" if v == default else "  *"
        warn = "  (없는 경로)" if k in PATH_KEYS and v and not os.path.exists(v) else ""
        lines.append(f"{k:<16} {v if v is not None else '-'}{mark}{warn}\n{'':<16} └ {meaning}")
    mats = cfg.get("materials") or []
    lines.append(f"materials        {len(mats)}개\n{'':<16} └ {KEYS['materials'][1]}")
    for m in mats:
        warn = "" if os.path.exists(m.get("path", "")) else "  (없는 경로)"
        lines.append(f"  - {m.get('path')}{warn}\n    언제: {m.get('when') or '-'}")
    return "\n".join(lines)
