"""comet — build, check, preview and upload Comet cinema-chat templates from a JSON spec."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time

from . import api, config, dsl, pyc, regex as R, spec as S, works as W

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(obj, path: str | None):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"→ {path}")
    else:
        print(text)


def _sidecar(spec_path: str) -> str:
    return os.path.splitext(spec_path)[0] + ".comet.json"


def _local_payload(spec_path: str):
    """Payload with local file paths left in place (for check/preview)."""
    sp = _load(spec_path)
    return S.build_payload(sp, None, os.path.dirname(os.path.abspath(spec_path)))


def _print_report(rep: S.Report):
    print(rep.text())
    print(f"— 오류 {len(rep.errors)} · 경고 {len(rep.warnings)}")


def _kind(sp: dict) -> str:
    k = sp.get("kind", "template")
    if k not in ("template", *W.KINDS):
        sys.exit(f"✗ kind는 template|novel|cinema ('{k}')")
    return k


SESSION_TEMPLATE_KEYS = ("variables", "sprites", "imageStorage", "audioStorage", "triggers")


def _local_template(path: str) -> tuple[dict, S.Report]:
    """A local template spec compiled offline into the sessionTemplate shape a cinema chat stores."""
    payload, rep = S.build_payload(_load(path), None, os.path.dirname(os.path.abspath(path)))
    side = _sidecar(path)
    tid = _load(side).get("templateId") if os.path.exists(side) else "none"
    raw = payload["rawTemplate"]
    return {"templateId": tid, **{k: raw[k] for k in SESSION_TEMPLATE_KEYS}}, rep


def _own_session_template(c: "api.Client", tid: str) -> dict:
    own = c.own_templates()
    for t in own:
        if t["sessionTemplate"].get("templateId") == tid:
            return t["sessionTemplate"]
    sys.exit(f"✗ 템플릿 {tid}를 쓸 수 없습니다(내 템플릿·보유 템플릿에 없음). 쓸 수 있는 것:\n"
             + "\n".join(f"  {t['sessionTemplate'].get('templateId')}  {t['title']}" for t in own))


def _cinema_template(sp: dict, base: str, c: "api.Client | None" = None, cid: str | None = None):
    """(sessionTemplate or None, Report of a local template). Online (c given) the server copy wins."""
    mode, ref = W.template_ref(sp, base)
    trep = S.Report()
    if mode == "file":
        if not os.path.exists(ref):
            trep.err("template", f"템플릿 스펙이 없습니다: {ref}")
            return None, trep
        st, trep = _local_template(ref)
        if c is None:
            return st, trep
        if st["templateId"] == "none":
            sys.exit(f"✗ 템플릿을 먼저 올리세요: comet upload {os.path.relpath(ref)}")
        return _own_session_template(c, st["templateId"]), trep
    if mode == "id":
        return (_own_session_template(c, ref) if c else None), trep
    if c is None:
        return None, trep
    if not cid:
        sys.exit("✗ 새 시네마챗에는 \"template\"이 필요합니다(템플릿 스펙 경로 또는 템플릿 ID, `comet list --own`)")
    return c.post_work("cinema", cid)["postCharacter"]["rawCharacter"]["sessionTemplate"], trep


def _build_work(kind: str, sp: dict, base: str, resolve=None, c=None, cid=None):
    if kind == "novel":
        return W.build_novel(sp, resolve, base)
    st, trep = _cinema_template(sp, base, c, cid)
    payload, rep = W.build_cinema(sp, st, resolve, base)
    rep.errors[:0] = [f"template › {e}" for e in trep.errors]
    return payload, rep


def _resolver(c: "api.Client", work: str):
    """Uploads local files once (content-hash cache) and records what went up."""
    uploaded = []

    def resolve(p, kind, where):
        if p.startswith(("http://", "https://")):
            return p
        if kind == "thumbnail":
            p = _thumb_ready(p, work)
            kind = "image"
        url = c.upload_file(p, kind)
        uploaded.append((where, os.path.basename(p), url + ("  (같은 파일, 캐시 재사용)" if c.last_cached else "")))
        return url
    return resolve, uploaded


# ---------------------------------------------------------------- commands

def cmd_login(a):
    from . import auth
    if a.sid:
        sid, src = a.sid, "manual"
    else:
        cfg = config.load()
        sid, src = auth.sid_from_chrome(profile=a.profile or cfg["chrome_profile"], user_dir=a.chrome_dir or cfg["chrome_dir"])
    user = api.Client(sid=sid).user()
    api.save_session(sid, src, user)
    print(f"✓ 로그인: {user.get('email')} ({src}) → {api.SESSION_FILE}")


def cmd_setup(a):
    from . import setup
    sys.exit(setup.run(a))


def cmd_doctor(a):
    from . import setup
    sys.exit(setup.doctor())


def cmd_config(a):
    if a.op in (None, "show"):
        print(config.show())
    elif a.op == "path":
        print(config.CONFIG_FILE)
    elif a.op == "get":
        v = config.get(a.key)
        print(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else ("" if v is None else v))
    elif a.op in ("set", "unset"):
        try:
            cfg = config.set_key(a.key, a.value if a.op == "set" else None)
        except KeyError:
            keys = ", ".join(k for k in config.KEYS if k != "materials")
            sys.exit(f"✗ 모르는 키: {a.key} (있는 키: {keys}; 자료는 `comet config material add`)")
        print(f"✓ {a.key} = {cfg[a.key] if cfg[a.key] is not None else '-'}")
    elif a.op == "material":
        if a.mop == "add":
            config.add_material(a.path, a.when)
            print(f"✓ 자료 추가: {a.path} (언제: {a.when})")
        elif not config.remove_material(a.path):
            sys.exit(f"✗ 등록된 자료가 아닙니다: {a.path}")
        else:
            print(f"✓ 자료 삭제: {a.path}")


def cmd_whoami(a):
    c = api.Client()
    u = c.user()
    print(f"{u.get('email')}  id={u.get('id')}")


def cmd_models(a):
    rows = api.Client().models()
    print("모델 ID                         시네마  프로  이름")
    for m in rows:
        print(f"{m.get('model', ''):<31} {'○' if m.get('canCinema') else '·':^5} {'○' if m.get('proBudget') else '·':^4}  "
              f"{m.get('modelName', '')}" + ("  (로그 제공 동의 필요)" if m.get("needAllowLog") else ""))


def cmd_list(a):
    c = api.Client()
    if a.kind != "template":
        rows = c.my_works(a.kind)
        if not rows:
            print(f"(올린 {W.KINDS[a.kind]['label']} 없음)")
        for t in rows:
            print(f"{t[W.KINDS[a.kind]['id_key']]}  [{t.get('visibility')}]  {t.get('title')}  "
                  f"세션 {t.get('sessionCount', 0)} · 채팅 {t.get('chatCount', 0)} · ♥{t.get('likeCount', 0)}")
        return
    if a.own:
        for i, t in enumerate(c.own_templates()):
            st = t["sessionTemplate"]
            print(f"[{i}] {t['title']}  id={st.get('templateId')}  변수 {len(st['variables'])} · 오브젝트 {len(st['sprites'])} · 트리거 {len(st['triggers'])}")
        return
    rows = c.my_templates()
    if not rows:
        print("(올린 템플릿 없음)")
    for t in rows:
        print(f"{t['templateId']}  [{t['visibility']}]  {t['title']}  ♥{t.get('likeCount', 0)} 구매 {t.get('purchaseCount', 0)}")


def _pull_work(a, c):
    k = W.KINDS[a.kind]
    got = c.post_work(a.kind, a.id)
    post = got[k["post_key"]]
    if a.raw:
        return _dump(got, a.output)
    if not a.output:
        sys.exit("pull --kind novel|cinema는 -o 스펙 경로가 필요합니다(긴 글·목록은 옆 파일로 씀)")
    out_dir = os.path.dirname(os.path.abspath(a.output))
    os.makedirs(out_dir, exist_ok=True)
    sp = W.post_to_spec(a.kind, post, out_dir)
    _dump(sp, a.output)
    with open(_sidecar(a.output), "w", encoding="utf-8") as f:
        json.dump({"kind": a.kind, "id": a.id, "pulled_at": time.strftime("%Y-%m-%d %H:%M:%S"), **W.links(a.kind, a.id)},
                  f, ensure_ascii=False, indent=1)
    if got.get("blockReason"):
        print(f"△ 제재 사유: {got['blockReason']}")


def cmd_pull(a):
    c = api.Client()
    if a.kind != "template":
        if not a.id:
            sys.exit(f"pull --kind {a.kind}: id가 필요합니다(`comet list --kind {a.kind}`)")
        return _pull_work(a, c)
    if a.own is not None:
        t = c.own_templates()[a.own]
        post = {"title": t["title"], "thumbnail": t["thumbnail"], "rawTemplate": t["sessionTemplate"]}
    else:
        try:
            post = c.post_template(a.id)
        except api.ApiError:
            d = c.detail_template(a.id)
            post = d.get("postTemplate") or d
    if a.raw:
        return _dump(post, a.output)
    sp = S.payload_to_spec(post)
    if a.lang == "py":
        if not a.output:
            sys.exit("--lang py는 -o 경로가 필요합니다(옆에 logic.py를 씀)")
        raw = post.get("rawTemplate") or post.get("sessionTemplate") or post
        names = [L["name"] for L in sp["layers"]]
        logic = os.path.join(os.path.dirname(os.path.abspath(a.output)), "logic.py")
        with open(logic, "w", encoding="utf-8") as f:
            f.write(pyc.module_text(raw, names))
        print(f"→ {logic}")
        for L in sp["layers"]:
            for k in ("state", "value", "action"):
                L.pop(k, None)
        sp["scripts"] = "logic.py"
        sp["triggers"] = []
    _dump(sp, a.output)
    if a.output and a.id:
        with open(_sidecar(a.output), "w", encoding="utf-8") as f:
            json.dump({"templateId": a.id, "pulled_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False, indent=1)


def cmd_check(a):
    sp = _load(a.spec)
    kind = _kind(sp)
    if kind != "template":
        _, rep = _build_work(kind, sp, os.path.dirname(os.path.abspath(a.spec)))
        _print_report(rep)
        sys.exit(1 if rep.errors or (a.strict and rep.warnings) else 0)
    payload, rep = _local_payload(a.spec)
    raw_errs = S.check_raw(payload["rawTemplate"])
    for e in raw_errs:
        rep.err("payload", e)
    _print_report(rep)
    if a.show:
        sp = _load(a.spec)
        for L in sp.get("layers", []):
            for key, ctx in (("state", "state"), ("value", "value"), ("action", "action")):
                if L.get(key):
                    print(f"\n# {L.get('name', L['type'])}.{key}")
                    try:
                        node, ret = dsl.compile_value(L[key]) if ctx == "value" else dsl.compile_script(L[key], ctx)
                        print(dsl.decompile(node, ret))
                    except dsl.ScriptError as e:
                        print(f"  ✗ {e}")
    sys.exit(1 if rep.errors or (a.strict and rep.warnings) else 0)


def cmd_build(a):
    sp = _load(a.spec)
    kind = _kind(sp)
    if kind != "template":
        payload, rep = _build_work(kind, sp, os.path.dirname(os.path.abspath(a.spec)))
    else:
        payload, rep = _local_payload(a.spec)
    _print_report(rep)
    _dump(payload, a.output)
    sys.exit(1 if rep.errors else 0)


def _cinema_preview(a, sp: dict):
    """Template of a cinema spec + the screen a player first sees: defaults, then intro logs up to the
    first one that stops auto-advance (or exactly --log N), frames fed from the spec's images."""
    base = os.path.dirname(os.path.abspath(a.spec))
    st, trep = _cinema_template(sp, base)
    if st is None:
        side = _sidecar(a.spec)
        cid = _load(side).get("id") if os.path.exists(side) else None
        st, trep = _cinema_template(sp, base, api.Client(), cid)
    payload, rep = W.build_cinema(sp, st, None, base)
    rep.errors[:0] = [f"template › {e}" for e in trep.errors]
    sessions = payload["rawCharacter"]["startSessions"]
    if not sessions:
        return st, rep, {}, {}
    s = sessions[min(a.session, len(sessions) - 1)]
    names = [v["varName"] for v in st["variables"]]
    auto = {v["varName"]: v.get("isAuto") for v in st["variables"]}
    values = {n: v for n, v in zip(names, s["initSession"]) if v != ""}
    shown = 0
    for i, lg in enumerate(s["logs"]):
        if a.log is not None and i >= a.log:
            break
        values[lg["varName"]] = lg["value"]
        shown = i + 1
        if a.log is None and not auto.get(lg["varName"]):
            break
    print(f"시작 세션 '{s['sessionTitle']}': 기본값 + 인트로 로그 {shown}/{len(s['logs'])}개")
    images = {}
    for name, v in (sp.get("images") or {}).items():
        src = v if isinstance(v, str) else (v or {}).get("src", "")
        images[name] = src if src.startswith(("http://", "https://")) else os.path.join(base, src)
    return st, rep, values, images


def _resolve_preview_target(a):
    if a.id:
        c = api.Client()
        post = c.post_template(a.id)
        return post["rawTemplate"], S.Report(), {}, {}
    data = _load(a.spec)
    if "rawTemplate" in data:
        return data["rawTemplate"], S.Report(), {}, {}
    if data.get("kind") == "cinema":
        return _cinema_preview(a, data)
    if data.get("kind") == "novel":
        sys.exit("✗ 노벨챗은 그림 미리보기가 없습니다. `comet macro test`로 출력이 화면 직전 글로 바뀌는 과정을 봅니다")
    payload, rep = S.build_payload(data, None, os.path.dirname(os.path.abspath(a.spec)))
    return payload["rawTemplate"], rep, {}, {}


def cmd_preview(a):
    from . import render
    raw, rep, values, spec_images = _resolve_preview_target(a)
    if rep.errors:
        _print_report(rep)
        sys.exit(1)
    n = len(raw["sprites"])
    names = {}
    for si, s in enumerate(raw["sprites"]):
        names.setdefault(s.get("objectName"), []).append(si)
    values.update(dict(kv.split("=", 1) for kv in a.set))
    now = a.now
    for kv in a.ago:
        k, ms = kv.split("=", 1)
        values[k] = str(int(now - float(ms)))
    events = []
    for ev in a.event:
        if ev.startswith("click:"):
            target = ev[6:]
            if target.lstrip("-").isdigit():
                si = n - 1 - int(target)
            else:
                hits = names.get(target, [])
                if len(hits) != 1:
                    sys.exit(f"click:{target} — 이름이 {len(hits)}개 일치합니다. 레이어 번호(아래→위 0부터)로 지정하세요")
                si = hits[0]
            events.append(f"click:{si}")
        else:
            events.append(ev)
    char_images = {**spec_images, **dict(kv.split("=", 1) for kv in a.char_image)}
    out = a.output or os.path.join(tempfile.gettempdir(), "comet-preview.png")
    res = render.render(raw, values, now=now, after=a.after, mouse=tuple(a.mouse), first=a.pos == "first", last=a.pos != "middle" and a.pos != "first",
                        events=events, char_images=char_images, username=a.username or config.get("username"), font=a.font, boxes=a.boxes, out=out,
                        scale=a.scale)
    def layer_label(si: int) -> str:
        return f"레이어 {n - 1 - si}({raw['sprites'][si].get('objectName')})"

    for ev, fired in res["events"]:
        if ev.startswith("click:"):
            ev = f"click:{layer_label(int(ev[6:]))}"
        fired = [re.sub(r"sprite (\d+)", lambda m: layer_label(int(m.group(1))), f) for f in fired]
        print(f"이벤트 {ev}: {', '.join(fired) if fired else '부수효과 없음'}")
    sysvars = [v["varName"] for v in raw["variables"] if v["mode"] == "none"]
    if sysvars:
        print("시스템 변수: " + ", ".join(f"{k}={res['values'].get(k)!r}" for k in sysvars))
    print("레이어(아래→위):")
    for st in reversed(res["states"]):
        li = n - 1 - st["index"]
        vis = "보임" if st["visibility"] else "숨김"
        extra = ""
        if "value" in st:
            v = st["value"].replace("\n", "⏎")
            extra = f"  값={v[:60]!r}" + (f" · {st['lines']}줄" if "lines" in st else "") + (f" · {st['overflow']}" if "overflow" in st else "")
        print(f"  {li:>3} {st['type']:<5} {st['name']:<10} {vis} x={st['x']} y={st['y']} w={st['width']} h={st['height']} α={st['opacity']}{extra}")
    if not res["font"]:
        print("△ 한글 글꼴을 못 찾아 기본 글꼴로 그렸습니다(--font로 지정)")
    print(f"→ {out}")


def _thumb_ready(path: str, workdir: str) -> str:
    from . import assets
    if path.startswith(("http://", "https://")) or not assets.needs_16x9(path):
        return path
    out = os.path.join(workdir, "thumb-16x9.png")
    assets.thumbnail(path, out)
    print(f"△ 썸네일을 16:9로 가운데 잘라 올립니다: {out}")
    return out


def _upload_work(a, sp: dict, spec_path: str, kind: str):
    k = W.KINDS[kind]
    base = os.path.dirname(spec_path)
    side = _sidecar(spec_path)
    cid = a.id
    if not cid and not a.new and os.path.exists(side):
        meta = _load(side)
        cid = meta.get("id") if meta.get("kind") == kind else None
    if a.dry_run:
        payload, rep = _build_work(kind, sp, base)
        print(rep.text())
        print(f"(dry-run) {'수정 ' + cid if cid else '새로 만들기'} · {k['label']}")
        return _dump(payload, a.output)
    payload, rep = _build_work(kind, sp, base)   # offline pass first: nothing goes up if the spec is broken
    if rep.errors:
        _print_report(rep)
        sys.exit("✗ 오류가 있어 올리지 않았습니다")
    c = api.Client()
    if cid and cid not in {t[k["id_key"]] for t in c.my_works(kind)}:
        print(f"△ {cid}는 내 {k['label']} 목록에 없어 새로 만듭니다")
        cid = None
    models = {m.get("model"): m for m in c.models()}
    model = payload["defaultModel"]
    if model not in models or (kind == "cinema" and not models[model].get("canCinema")):
        usable = [m for m, d in models.items() if kind != "cinema" or d.get("canCinema")]
        sys.exit(f"✗ defaultModel '{model}'은 {k['label']}에 쓸 수 없습니다. 쓸 수 있는 모델: {', '.join(usable)}")
    work = tempfile.mkdtemp(prefix="comet-up-")
    try:
        resolve, uploaded = _resolver(c, work)
        payload, rep = _build_work(kind, sp, base, resolve, c, cid)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if rep.errors:
        _print_report(rep)
        sys.exit(1)
    if rep.warnings:
        print(rep.text())
    for where, name, url in uploaded:
        print(f"  ↑ {where}: {name} → {url}")
    try:
        if cid:
            c.update_work(kind, cid, payload)
            action = "수정"
        else:
            cid = c.create_work(kind, payload)
            action = "등록"
    except api.ApiError as e:
        sys.exit(f"✗ 서버 거부 — {e}")
    ln = W.links(kind, cid)
    with open(side, "w", encoding="utf-8") as f:
        json.dump({"kind": kind, "id": cid, "title": payload["title"], "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"), **ln},
                  f, ensure_ascii=False, indent=1)
    got = c.post_work(kind, cid)
    diffs = W.diff(kind, payload, got[k["post_key"]])
    print(f"✓ {k['label']} {action}: {payload['title']} ({payload['visibility']})  id={cid}")
    print(f"  편집 {ln['edit']}\n  상세 {ln['detail']}")
    print("  저장본 대조: " + ("보낸 그대로 ✓" if not diffs else f"서버가 바꾼 칸 {diffs} — `comet pull --kind {kind} {cid} --raw`로 확인"))
    if got.get("blockReason"):
        print(f"  △ 제재 사유: {got['blockReason']}")


def cmd_upload(a):
    spec_path = os.path.abspath(a.spec)
    base = os.path.dirname(spec_path)
    sp = _load(spec_path)
    if a.public:
        sp["visibility"] = "public"
    kind = _kind(sp)
    if kind != "template":
        return _upload_work(a, sp, spec_path, kind)
    payload, rep = S.build_payload(sp, None, base)
    if rep.errors:
        _print_report(rep)
        sys.exit("✗ 오류가 있어 올리지 않았습니다")
    if rep.warnings:
        print(rep.text())
    side = _sidecar(spec_path)
    tid = a.id
    if not tid and not a.new and os.path.exists(side):
        tid = _load(side).get("templateId")
    if a.dry_run:
        print(f"(dry-run) {'수정 ' + tid if tid else '새로 만들기'} · 오브젝트 {len(payload['rawTemplate']['sprites'])} · 트리거 {len(payload['rawTemplate']['triggers'])}")
        return _dump(payload, a.output)
    c = api.Client()
    work = tempfile.mkdtemp(prefix="comet-up-")
    try:
        resolve, uploaded = _resolver(c, work)
        payload, rep = S.build_payload(sp, resolve, base)
        if rep.errors:
            _print_report(rep)
            sys.exit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for where, name, url in uploaded:
        print(f"  ↑ {where}: {name} → {url}")
    if tid:
        mine = {t["templateId"] for t in c.my_templates()}
        if tid not in mine:
            print(f"△ {tid}는 내 템플릿 목록에 없어 새로 만듭니다")
            tid = None
    try:
        if tid:
            c.update_template(tid, payload)
            action = "수정"
        else:
            tid = c.create_template(payload)
            action = "등록"
    except api.ApiError as e:
        sys.exit(f"✗ 서버 거부 — {e}")
    links = api.links(tid)
    with open(side, "w", encoding="utf-8") as f:
        json.dump({"templateId": tid, "title": payload["title"], "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"), **links},
                  f, ensure_ascii=False, indent=1)
    # read back: the server silently rewrites unknown nodes/triggers, so compare
    stored = c.post_template(tid)["rawTemplate"]
    diffs = [k for k in ("variables", "sprites", "imageStorage", "audioStorage", "triggers", "prompt") if stored.get(k) != payload["rawTemplate"][k]]
    print(f"✓ {action}: {payload['title']} ({payload['visibility']})  id={tid}")
    print(f"  편집 {links['edit']}\n  상세 {links['detail']}")
    print("  저장본 대조: " + ("보낸 그대로 ✓" if not diffs else f"서버가 바꾼 칸 {diffs} — `comet pull {tid}`로 확인"))


def cmd_delete(a):
    c = api.Client()
    if a.kind != "template":
        k = W.KINDS[a.kind]
        mine = {t[k["id_key"]]: t for t in c.my_works(a.kind)}
        if a.id not in mine:
            sys.exit(f"✗ {a.id}는 내 {k['label']}이 아닙니다")
        t = mine[a.id]
        if not a.yes:
            sys.exit(f"'{t['title']}'(세션 {t.get('sessionCount', 0)}·채팅 {t.get('chatCount', 0)})를 지우려면 --yes를 붙이세요(되돌릴 수 없음)")
        c.delete_work(a.kind, a.id)
        print(f"✓ 삭제: {t['title']} ({a.id})")
        return
    mine = {t["templateId"]: t for t in c.my_templates()}
    if a.id not in mine:
        sys.exit(f"✗ {a.id}는 내 템플릿이 아닙니다")
    if not a.yes:
        sys.exit(f"'{mine[a.id]['title']}'를 지우려면 --yes를 붙이세요(되돌릴 수 없음)")
    c.delete_template(a.id)
    print(f"✓ 삭제: {mine[a.id]['title']} ({a.id})")


def cmd_assets(a):
    from . import assets
    k = a.kind
    if k == "panel":
        assets.panel(a.out, a.w, a.h, a.fill, a.radius, a.border, a.border_width, a.gradient_to)
    elif k == "button":
        assets.button(a.out, a.size, a.icon, a.shape, a.fill, a.fg, a.border, a.pad)
    elif k == "hit":
        assets.hit(a.out)
    elif k == "pad":
        assets.pad(a.src, a.out, a.margin)
    elif k == "thumb":
        assets.thumbnail(a.src, a.out, a.title, a.font)
    print(f"→ {a.out}")


def cmd_new(a):
    src = os.path.join(HERE, "examples", a.example)
    if not os.path.isdir(src):
        sys.exit(f"예제 없음: {a.example} (있는 것: {', '.join(sorted(os.listdir(os.path.join(HERE, 'examples'))))})")
    if os.path.exists(a.dir) and os.listdir(a.dir):
        sys.exit(f"{a.dir}가 비어 있지 않습니다")
    skip = shutil.ignore_patterns("*.comet.json", "__pycache__")
    shutil.copytree(src, a.dir, dirs_exist_ok=True, ignore=skip)
    spec = next((n for n in ("template.json", "novel.json", "cinema.json") if os.path.exists(os.path.join(a.dir, n))), "")
    if spec == "cinema.json":
        # the example points at a sibling template; give the copy its own template folder instead
        cpath = os.path.join(a.dir, spec)
        sp = _load(cpath)
        mode, ref = W.template_ref(sp, src)
        if mode == "file" and os.path.isdir(os.path.dirname(ref)):
            shutil.copytree(os.path.dirname(ref), os.path.join(a.dir, "template"), ignore=skip)
            sp["template"] = "template/" + os.path.basename(ref)
            _dump(sp, cpath)
            print(f"  템플릿: {os.path.join(a.dir, 'template')} — 먼저 `comet upload {os.path.join(a.dir, sp['template'])}`")
    print(f"→ {a.dir}  (spec: {os.path.join(a.dir, spec)})")


def cmd_py(a):
    src = sys.stdin.read() if a.code == "-" else a.code
    variables = None
    if a.spec:
        sp = _load(a.spec)
        variables = S.variable_table(sp, S.Report())
    module = pyc.Module(open(a.module, encoding="utf-8").read(), a.module) if a.module else None
    try:
        node, ret = pyc.PyCompiler(variables, module).compile_source(src, a.context)
    except dsl.ScriptError as e:
        sys.exit(f"✗ {e}")
    if a.json:
        print(json.dumps({"node": node, "res": ret} if a.context == "value" else node, ensure_ascii=False, indent=1))
        return
    print("# 블록(에디터에 보이는 순서)")
    print(dsl.decompile(node, ret))
    names = [v["varName"] for v in variables] if variables else None
    print("\n# 블록을 다시 Python으로")
    print(pyc.PyPrinter(names).inline(node, ret))


def cmd_dsl(a):
    src = sys.stdin.read() if a.code == "-" else a.code
    try:
        if a.context == "value":
            node, ret = dsl.compile_value(src)
            out = {"node": node, "res": ret}
        else:
            node, _ = dsl.compile_script(src, a.context)
            out = node
    except dsl.ScriptError as e:
        sys.exit(f"✗ {e}")
    if a.decompile:
        print(dsl.decompile(node, out.get("res") if isinstance(out, dict) and "res" in out else None))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=1))


def _arg_text(v: str) -> str:
    """'-' = stdin, '@path' = file contents, else the literal argument."""
    if v == "-":
        return sys.stdin.read()
    if v.startswith("@") and os.path.isfile(v[1:]):
        with open(v[1:], encoding="utf-8") as f:
            return f.read()
    return v


def _need_node():
    if not R.node_bin():
        sys.exit("✗ node가 없습니다. 정확한 JS 실행이 필요한 명령입니다(brew install node 또는 nvm)")


def cmd_regex(a):
    if a.op == "check":
        rows, bad = [], 0
        for p in a.patterns:
            st, js = R.check(_arg_text(p))
            bad += not st.safe
            rows.append({"pattern": p, "ok": st.ok, "safe": st.safe, "error": st.error, "groups": st.groups,
                         "named": st.named, "repetitions": st.repetitions, "star_height": st.star_height,
                         "notes": st.notes, "re2": R.re2_issues(_arg_text(p), st.groups) if st.ok else [],
                         "js_checked": js is not None})
            if not a.json:
                print(f"{st.verdict()}  {p}")
                if st.ok:
                    print(f"   캡처 그룹 {st.groups}{' ' + str(st.named) if st.named else ''} · 반복 기호 {st.repetitions}/{R.REP_LIMIT} · 중첩 {st.star_height}/1"
                          + ("" if js else "  (node 없음: JS 문법은 정적 검사만)"))
                for n in st.notes:
                    print(f"   · {n}")
                for x in (R.re2_issues(_arg_text(p), st.groups) if st.ok else []):
                    print(f"   · 로어북 트리거로 쓰면 서버(RE2)가 무시합니다: {x}")
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        sys.exit(1 if bad else 0)
    if a.op == "test":
        _need_node()
        pat, text = _arg_text(a.pattern), _arg_text(a.text)
        st = R.analyze(pat)
        payload = {"op": "match", "pattern": pat, "text": text}
        if a.to is not None:
            payload["to"] = _arg_text(a.to)
        try:
            out = R.run_js(payload)
        except RuntimeError as e:
            sys.exit(f"✗ {e}")
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return
        print(st.verdict() + ("  — 매크로로 쓰면 사이트가 건너뜁니다" if st.ok and not st.safe else ""))
        print(f"매치 {len(out['matches'])}개")
        for m in out["matches"]:
            g = "  " + " ".join(f"${k + 1}={v!r}" for k, v in enumerate(m["groups"])) if m["groups"] else ""
            print(f"  @{m['index']}: {m['match']!r}{g}")
        if "replaced" in out:
            print("치환 결과:\n" + out["replaced"])
        return
    if a.op == "range":
        print(R.int_range(a.lo, a.hi, capture=not a.no_capture))
        return
    if a.op == "words":
        print(R.alternation(a.words, capture=not a.no_capture))
        return
    if a.op == "image":
        ms = R.image_macros(a.tag, a.names.split(","), a.lo, a.hi, a.url, a.default, a.anon, spaces=not a.strict_spaces)
        _dump(ms, a.output)


def cmd_macro(a):
    data = _load(a.file)
    if isinstance(data, dict) and data.get("kind") == "novel":
        # a novel spec: run the macro list exactly as it will be stored (image macros first)
        payload, rep = W.build_novel(data, None, os.path.dirname(os.path.abspath(a.file)))
        if rep.errors:
            _print_report(rep)
        data = [{k: m[k] for k in (("type", "from", "prefix", "suffix") if m["type"] == "regex_regex" else ("type", "from", "to"))}
                for m in payload["rawNovel"]["macros"]]
    if a.op == "check":
        errs, warns = R.check_macros(data)
        for e in errs:
            print(f"✗ {e}")
        for w in warns:
            print(f"⚠ {w}")
        if R.node_bin() is None:
            print("· node 없음: 정규식 문법은 정적 검사만 했습니다")
        print(f"— 매크로 {len(data) if isinstance(data, list) else '?'}개 · 오류 {len(errs)} · 경고 {len(warns)}")
        sys.exit(1 if errs or (a.strict and warns) else 0)
    _need_node()
    errs, _ = R.check_macros(data)
    for e in errs:
        print(f"✗ {e}", file=sys.stderr)
    out = R.run_macros(_arg_text(a.text), data, a.user, a.turn)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    if a.trace:
        prev = None
        for s in out["steps"]:
            span = f"[{s['from']}]" if s["from"] == s["to"] else f"[{s['from']}→{s['to']}] 체인"
            mark = f" ✗ {s['error']}" if s.get("error") else (" (변화 없음)" if s["text"] == prev else "")
            print(f"── {span} {data[s['from']]['type']}{mark}")
            if s["text"] != prev and not s.get("error"):
                print(s["text"])
            prev = s["text"]
        print("── {user}·{turn} 치환 뒤(마크다운 직전)")
    print(out["result"])


def cmd_lorebook(a):
    data = _load(a.file)
    errs, warns = R.check_lorebooks(data)
    for e in errs:
        print(f"✗ {e}")
    for w in warns:
        print(f"⚠ {w}")
    print(f"— 로어북 {len(data) if isinstance(data, list) else '?'}개 · 오류 {len(errs)} · 경고 {len(warns)}")
    sys.exit(1 if errs or (a.strict and warns) else 0)


def main(argv=None):
    p = argparse.ArgumentParser(prog="comet", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("login", help="크롬에 로그인된 코멧 세션을 가져와 저장")
    s.add_argument("--sid", help="connect.sid 값을 직접 넣기")
    s.add_argument("--profile", help="Chrome 프로필 폴더 이름(예: 'Profile 1'). 기본: 설정 chrome_profile")
    s.add_argument("--chrome-dir", help="Chrome 사용자 데이터 폴더. 기본: 설정 chrome_dir")
    s.set_defaults(fn=cmd_login)

    s = sub.add_parser("setup", help="초기 세팅: 환경 점검 → 스킬·명령 설치 → 설정 → 로그인")
    s.add_argument("--yes", "-y", action="store_true", help="묻지 않고 기본값(이미 저장된 설정은 유지)")
    s.add_argument("--skills-dir", help="스킬을 둘 폴더(기본 ~/.claude/skills)")
    s.add_argument("--bin-dir", help="comet 명령을 둘 폴더(기본 ~/.local/bin)")
    s.add_argument("--link", action="store_true", help="복사 대신 이 클론을 링크(개발용: git pull이 곧 업데이트)")
    s.add_argument("--no-install", action="store_true", help="설치 단계 건너뛰기")
    s.add_argument("--chrome-profile"); s.add_argument("--username"); s.add_argument("--font")
    s.add_argument("--works-dir", help="완성품 폴더(템플릿/·시네마챗/·노벨챗/를 만든다)")
    s.add_argument("--prompt-template", help="시네마챗·노벨챗 프롬프트를 시작할 내 템플릿 파일")
    s.add_argument("--material", action="append", metavar="경로=언제", help="창작 자료 등록(여러 번)")
    s.add_argument("--login", action="store_true", help="--yes여도 크롬에서 세션을 가져오기")
    s.add_argument("--sid", help="connect.sid 값으로 로그인")
    s.add_argument("--no-login", action="store_true")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("doctor", help="환경 점검(필수가 빠지면 종료 코드 1)")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("config", help="설정 보기·바꾸기 (show | path | get K | set K V | unset K | material add|remove)")
    csub = s.add_subparsers(dest="op")
    csub.add_parser("show"); csub.add_parser("path")
    q = csub.add_parser("get"); q.add_argument("key")
    q = csub.add_parser("set"); q.add_argument("key"); q.add_argument("value")
    q = csub.add_parser("unset"); q.add_argument("key")
    q = csub.add_parser("material")
    msub = q.add_subparsers(dest="mop", required=True)
    m = msub.add_parser("add"); m.add_argument("path"); m.add_argument("--when", default="작업 전", help="이 단계 전에 읽는다")
    m = msub.add_parser("remove"); m.add_argument("path")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("whoami", help="현재 세션의 계정")
    s.set_defaults(fn=cmd_whoami)

    s = sub.add_parser("list", help="내가 올린 템플릿·노벨챗·시네마챗(--own: 시네마챗에 쓸 수 있는 템플릿)")
    s.add_argument("--own", action="store_true")
    s.add_argument("--kind", choices=["template", "novel", "cinema"], default="template", help="template | novel(노벨챗) | cinema(시네마챗)")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("pull", help="템플릿·노벨챗·시네마챗을 내려받아 스펙으로 풀기")
    s.add_argument("id", nargs="?")
    s.add_argument("--kind", choices=["template", "novel", "cinema"], default="template", help="template | novel(노벨챗) | cinema(시네마챗)")
    s.add_argument("--own", type=int, help="list --own의 번호")
    s.add_argument("-o", "--output")
    s.add_argument("--raw", action="store_true", help="플랫폼 JSON 그대로")
    s.add_argument("--lang", choices=["cs", "py"], default="cs", help="py: 스크립트를 옆의 logic.py(Python)로")
    s.set_defaults(fn=cmd_pull)

    s = sub.add_parser("check", help="스펙 검사(오프라인). 스펙의 kind로 템플릿·노벨챗·시네마챗을 가름")
    s.add_argument("spec")
    s.add_argument("--strict", action="store_true", help="경고도 실패로")
    s.add_argument("--show", action="store_true", help="스크립트를 정규형으로 다시 출력")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("build", help="업로드 페이로드 JSON 만들기(파일은 안 올림)")
    s.add_argument("spec")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("preview", help="PNG 미리보기 + 레이어 상태표")
    s.add_argument("spec", nargs="?", help="스펙 또는 페이로드 JSON")
    s.add_argument("--id", help="올린 템플릿을 미리보기")
    s.add_argument("-o", "--output")
    s.add_argument("--set", action="append", default=[], metavar="변수=값")
    s.add_argument("--ago", action="append", default=[], metavar="변수=ms", help="변수에 (now - ms)를 넣기: 애니메이션 중간 프레임")
    s.add_argument("--event", action="append", default=[], help="load | key:<code> | click:<레이어 이름|번호>(그 레이어 가운데를 누름) | tap:x,y — 순서대로")
    s.add_argument("--char-image", action="append", default=[], metavar="이름=경로", help="프레임이 받을 시네마챗 이미지")
    s.add_argument("--after", type=float, default=0, metavar="ms", help="--event들이 이만큼 전에 일어난 것으로 보고 그림(애니메이션 중간 프레임)")
    s.add_argument("--now", type=float, default=1.0e12, help="timer 값(ms)")
    s.add_argument("--mouse", type=float, nargs=2, default=[0, 0])
    s.add_argument("--pos", choices=["first", "middle", "last"], default="last", help="is_first/is_last 판정용 로그 위치")
    s.add_argument("--username", help="{user} 이름. 기본: 설정 username")
    s.add_argument("--font")
    s.add_argument("--boxes", action="store_true", help="레이어 테두리와 번호 표시")
    s.add_argument("--scale", type=float, default=1.0)
    s.add_argument("--session", type=int, default=0, help="시네마챗 스펙: 시작 세션 번호(0부터)")
    s.add_argument("--log", type=int, help="시네마챗 스펙: 인트로 로그를 정확히 N개 적용(기본: 첫 비자동 로그까지)")
    s.set_defaults(fn=cmd_preview)

    s = sub.add_parser("models", help="기본 모델로 고를 수 있는 모델 ID(시네마챗 가능 여부)")
    s.set_defaults(fn=cmd_models)

    s = sub.add_parser("upload", help="템플릿·노벨챗·시네마챗 스펙: 검사 → 파일 업로드 → 등록/수정 → 저장본 대조")
    s.add_argument("spec")
    s.add_argument("--id", help="이 템플릿을 수정")
    s.add_argument("--new", action="store_true", help="사이드카 ID를 무시하고 새로 등록")
    s.add_argument("--public", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("-o", "--output", help="--dry-run 페이로드 저장")
    s.set_defaults(fn=cmd_upload)

    s = sub.add_parser("delete", help="내 템플릿·노벨챗·시네마챗 삭제")
    s.add_argument("id")
    s.add_argument("--kind", choices=["template", "novel", "cinema"], default="template", help="template | novel(노벨챗) | cinema(시네마챗)")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_delete)

    s = sub.add_parser("assets", help="UI 소재 PNG 만들기")
    asub = s.add_subparsers(dest="kind", required=True)
    q = asub.add_parser("panel")
    q.add_argument("out"); q.add_argument("--w", type=int, required=True); q.add_argument("--h", type=int, required=True)
    q.add_argument("--fill", default="#000000B3"); q.add_argument("--radius", type=int, default=24)
    q.add_argument("--border"); q.add_argument("--border-width", type=int, default=2); q.add_argument("--gradient-to")
    q = asub.add_parser("button")
    q.add_argument("out"); q.add_argument("--size", type=int, default=96); q.add_argument("--icon", default="chevron-right")
    q.add_argument("--shape", default="circle"); q.add_argument("--fill", default="#FFFFFF26"); q.add_argument("--fg", default="#FFFFFF")
    q.add_argument("--border"); q.add_argument("--pad", type=int, default=0)
    q = asub.add_parser("hit")
    q.add_argument("out")
    q = asub.add_parser("pad")
    q.add_argument("src"); q.add_argument("out"); q.add_argument("--margin", type=int, required=True)
    q = asub.add_parser("thumb")
    q.add_argument("out"); q.add_argument("--src"); q.add_argument("--title"); q.add_argument("--font")
    s.set_defaults(fn=cmd_assets)

    s = sub.add_parser("new", help="예제에서 새 템플릿 폴더 만들기")
    s.add_argument("dir")
    s.add_argument("--example", default="vn-dialogue", help="vn-dialogue | vn-dialogue-py | novel-basic | cinema-basic")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("py", help="Python 스크립트 조각 → 코멧 블록(무엇으로 바뀌는지 확인)")
    s.add_argument("context", choices=["state", "action", "value"])
    s.add_argument("code", help="Python 코드, '-'면 stdin")
    s.add_argument("--spec", help="변수 표를 읽을 스펙(없으면 모르는 이름은 템플릿 변수로 봄)")
    s.add_argument("--module", help="상수·도우미 함수를 가져올 logic.py")
    s.add_argument("--json", action="store_true", help="노드 JSON 출력")
    s.set_defaults(fn=cmd_py)

    s = sub.add_parser("dsl", help="스크립트 한 토막을 노드 JSON으로(또는 --decompile로 정규형)")
    s.add_argument("context", choices=["state", "action", "value"])
    s.add_argument("code", help="스크립트, '-'면 stdin")
    s.add_argument("--decompile", action="store_true")
    s.set_defaults(fn=cmd_dsl)

    s = sub.add_parser("regex", help="정규식: 사이트와 같은 안전·문법 판정, JS로 매치, 범위·단어·이미지 매크로 생성")
    rsub = s.add_subparsers(dest="op", required=True)
    q = rsub.add_parser("check", help="안전(ReDoS)·JS 문법·함정 판정. 위험이면 종료 코드 1")
    q.add_argument("patterns", nargs="+", help="정규식('@파일', '-'=stdin). '-'로 시작하면 앞에 --")
    q.add_argument("--json", action="store_true")
    q = rsub.add_parser("test", help="JS 그대로 매치(g 플래그)·치환")
    q.add_argument("pattern"); q.add_argument("text", help="글, '@파일', '-'")
    q.add_argument("--to", help="대체식($1 $& $$ …)")
    q.add_argument("--json", action="store_true")
    q = rsub.add_parser("range", help="정수 범위 정규식(반복 기호 0개)")
    q.add_argument("lo", type=int); q.add_argument("hi", type=int)
    q.add_argument("--no-capture", action="store_true")
    q = rsub.add_parser("words", help="단어 목록 → (긴말|짧은말), 특수문자 이스케이프")
    q.add_argument("words", nargs="+")
    q.add_argument("--no-capture", action="store_true")
    q = rsub.add_parser("image", help="이미지 매크로 4종(정확→번호 보정→익명→숨김) JSON")
    q.add_argument("--tag", default="img"); q.add_argument("--names", required=True, help="쉼표로")
    q.add_argument("--range", type=int, nargs=2, required=True, metavar=("LO", "HI"), help="번호 범위")
    q.add_argument("--url", required=True, help="$1=이름 $2=번호, 예: https://…/$1/$2.webp")
    q.add_argument("--default", type=int, help="번호가 틀릴 때 쓸 번호")
    q.add_argument("--anon", help="이름이 목록에 없을 때 URL($2=번호)")
    q.add_argument("--strict-spaces", action="store_true", help="{{ img::… }} 공백 허용 끄기")
    q.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_regex)

    s = sub.add_parser("macro", help="소설 매크로 JSON: 검사 · 사이트와 같은 순서로 실행")
    msub = s.add_subparsers(dest="op", required=True)
    q = msub.add_parser("check"); q.add_argument("file"); q.add_argument("--strict", action="store_true")
    q = msub.add_parser("test", help="AI 출력 예시에 매크로를 돌려 화면 직전 글을 보기")
    q.add_argument("file"); q.add_argument("text", help="글, '@파일', '-'")
    q.add_argument("--user", help="{user} 자리에 넣을 이름(조사 보정까지 재현)")
    q.add_argument("--turn", type=int)
    q.add_argument("--trace", action="store_true", help="매크로 하나마다 결과")
    q.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_macro)

    s = sub.add_parser("lorebook", help="로어북 JSON 검사(규격·정규식 트리거)")
    lsub = s.add_subparsers(dest="op", required=True)
    q = lsub.add_parser("check"); q.add_argument("file"); q.add_argument("--strict", action="store_true")
    s.set_defaults(fn=cmd_lorebook)

    a = p.parse_args(argv)
    if a.cmd == "regex" and a.op == "image":
        a.lo, a.hi = a.range
    if a.cmd == "preview" and not (a.spec or a.id):
        p.error("preview: 스펙 경로나 --id가 필요합니다")
    if a.cmd == "pull" and not (a.id or a.own is not None):
        p.error("pull: id나 --own N이 필요합니다")
    try:
        a.fn(a)
    except api.ApiError as e:
        sys.exit(f"✗ {e}")
    except FileNotFoundError as e:
        sys.exit(f"✗ 파일 없음: {e.filename}")
    except json.JSONDecodeError as e:
        sys.exit(f"✗ JSON 문법 오류: {e}")
