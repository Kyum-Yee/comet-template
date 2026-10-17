# comet-template

코멧([comet-talk.ai](https://comet-talk.ai)) 시네마챗 **템플릿**을 JSON 스펙 한 장과 Python(또는 줄 단위 DSL) 스크립트로 만들고, 사이트 에디터의 블록 코딩으로 컴파일해 바로 올리는 도구입니다. **노벨챗·시네마챗 작품**도 스펙 한 장(프롬프트·시작 세션·이미지·매크로·로어북)으로 검사하고 올리고 고치고 내려받습니다. 사이트 해석기·렌더러를 옮겨 와서 오프라인 검사, PNG 미리보기, 클릭·키·애니메이션 시뮬레이션까지 사이트와 같은 규칙으로 돌립니다. 노벨챗 매크로·로어북 JSON과 정규식도 사이트와 같은 순서와 위험 판정으로 검사·실행합니다.

명령줄 도구(`comet`)이면서 [Claude Code](https://claude.com/claude-code) 스킬입니다. 스킬로 설치하면 "시네마챗 템플릿 만들어줘" 같은 요청에 에이전트가 이 문서들(SKILL.md·RULES.md·GUIDE.md)을 읽고 설계부터 업로드까지 진행합니다.

> 코멧 공식 도구가 아닙니다. 사이트 번들 분석과 실측(2026-10)에 기댄 것이라 사이트가 바뀌면 어긋날 수 있습니다. 업로드는 내 계정 세션으로 내 템플릿만 다룹니다.

## 설치

필요한 것: Python 3.10+ (또는 [uv](https://docs.astral.sh/uv/), 권장). 선택: node(정규식·매크로를 JS 그대로 실행), Chrome(로그인 자동 가져오기).

```bash
git clone https://github.com/Kyum-Yee/comet-template.git
cd comet-template
./install.sh            # macOS · Linux
# Windows: python comet.py setup
```

`install.sh`는 uv가 있으면 uv로, 없으면 Python 3.10+를 찾아(필요하면 Pillow를 pip로 설치) `comet setup`을 부릅니다.

### 초기 세팅 (`comet setup`)

터미널에서는 단계마다 한 번씩 묻습니다. 엔터는 `[기본값]`, `-`는 비우기입니다.

| 단계 | 하는 일 |
|---|---|
| 1. 환경 점검 | Python·Pillow·uv·node·한글 글꼴·Chrome·코멧에 로그인된 크롬 프로필·세션·PATH (`comet doctor`와 같음) |
| 2. 설치 | 스킬 → `~/.claude/skills/comet-template`, 명령 → `~/.local/bin/comet` (Windows는 `comet.cmd`). 이전 설치본은 지우지 않고 `~/.cache/comet-template/backup/`으로 옮깁니다 |
| 3. 설정 | 크롬 프로필, 미리보기 `{user}` 이름, 완성품 폴더(아래에 `템플릿/`·`시네마챗/`·`노벨챗/`), 내 프롬프트 템플릿 파일, 창작 자료(경로 + 언제 읽을지) |
| 4. 로그인 | 크롬에 로그인된 코멧 세션을 가져옵니다. 크롬 프로필 복사본을 헤드리스로 띄워 CDP로 쿠키를 받으므로 실제 크롬과 키체인은 건드리지 않습니다 |

묻지 않고 한 번에:

```bash
./install.sh --yes --works-dir ~/코멧작품 \
  --material "~/세계관/캐릭터 체크리스트.md=캐릭터 설계 전" \
  --material "~/세계관/이름 규칙.md=이름 짓기 전" \
  --login
```

| 옵션 | 뜻 |
|---|---|
| `--yes` | 묻지 않음. 이미 저장된 설정은 유지 |
| `--skills-dir D` · `--bin-dir D` | 설치 위치 (다른 에이전트의 스킬 폴더도 가능) |
| `--link` | 복사 대신 이 클론을 링크. 이후 `git pull`이 곧 업데이트 |
| `--no-install` | 설정·로그인만 |
| `--chrome-profile` · `--username` · `--font` · `--works-dir` · `--prompt-template` | 설정값 |
| `--material 경로=언제` | 창작 자료 등록(여러 번) |
| `--login` · `--sid 값` · `--no-login` | 로그인 방식 |

업데이트: `git pull && ./install.sh --yes`. 다시 돌려도 설정은 그대로 남습니다.

### 설정 바꾸기

```bash
comet config show                          # 전부 보기 (없는 경로 표시)
comet config set works_dir ~/코멧작품
comet config material add ~/세계관 --when "세계관 설정 전"
comet config unset prompt_template
```

설정은 `~/.config/comet-template/config.json`(환경변수 `COMET_CONFIG_DIR`로 이동)에 있습니다. 에이전트는 작업 전에 `comet config show`를 읽고, 창작 자료를 지정된 단계 전에 읽으며, 프롬프트 템플릿이 있으면 거기서 시작하고, 완성품을 완성품 폴더에 둡니다.

## 써 보기

```bash
comet new 내대사창                                   # 예제(비주얼노벨 대사창)를 복사
comet check 내대사창/template.json --strict          # 오프라인 검사
comet preview 내대사창/template.json -o p.png \
  --set 대사="안녕" --set 배경=교실 --char-image 교실=내대사창/sample/bg.png \
  --event click:"상태 버튼" --after 120 --boxes      # 그림 + 레이어 상태표 + 클릭 결과
comet login                                          # 처음 한 번 (setup에서 했으면 생략)
comet upload 내대사창/template.json                  # 파일 업로드 → 등록 → 저장본 대조
```

노벨챗·시네마챗 작품:

```bash
comet new 등대 --example novel-basic && comet upload 등대/novel.json        # 비공개 등록
comet new 도서관 --example cinema-basic                                  # 템플릿 예제가 도서관/template/에 같이 옴
comet upload 도서관/template/template.json                               # 시네마챗이 쓸 템플릿 먼저
comet preview 도서관/cinema.json -o first.png                            # 플레이어가 처음 보는 화면
comet upload 도서관/cinema.json
comet list --kind novel · comet pull --kind cinema <ID> -o 작품/cinema.json · comet delete --kind novel <ID> --yes
```

노벨챗 매크로·정규식:

```bash
comet regex check '\{\{img::(.*?)\}\}'               # 사이트와 같은 위험(safe-regex)·JS 문법 판정
comet macro test 등대/novel.json @출력예시.txt --user 민준 --trace   # 이미지 매크로까지 저장될 순서로
comet lorebook check lore.json
```

외부 이미지 호스팅(Cloudflare Workers, 계정과 Node.js 필요):

```bash
comet host init && comet host add 등대 그림/ && comet host deploy && comet host check
comet host url 등대 --var 장면                     # 템플릿 프레임에 넣을 value 스크립트
```

전체 명령과 스펙 필드는 [SKILL.md](SKILL.md), Python으로 스크립트 쓰는 규칙은 [RULES.md](RULES.md), 시네마챗·노벨챗 제작 가이드(칸별 제한, 로어북, 매크로, 이미지, 점검표)는 [GUIDE.md](GUIDE.md)에 있습니다.

## 개인정보

- 세션(`session.json`, 권한 600)과 업로드 캐시는 설정 폴더에만 저장되고 이 저장소에는 들어가지 않습니다. `session.json`은 계정 열쇠이니 공유하지 마세요.
- 크롬이 없는 기기(서버 등)에서는 `comet login --sid <값>` 또는 환경변수 `COMET_SID`를 씁니다.
- 도구가 접속하는 곳은 `api.comet-talk.ai`(업로드·조회)와, 미리보기에서 URL 이미지를 그릴 때의 그 이미지 주소뿐입니다.

## 테스트

```bash
for t in tests/test_*.py; do uv run --with 'pillow>=10' python "$t"; done   # 또는 python3 -m pytest -q tests
```

node가 없으면 JS 실행이 필요한 정규식 테스트는 건너뜁니다.

## 라이선스

MIT
