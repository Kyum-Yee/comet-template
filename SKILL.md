---
name: comet-template
description: 코멧(comet-talk.ai) 시네마챗 템플릿을 JSON 스펙과 Python(또는 들여쓰기 DSL) 스크립트로 만들어 블록 코딩으로 컴파일하고, 오프라인 검사·PNG 미리보기·클릭 시뮬레이션을 거쳐 파이썬으로 바로 업로드한다. 올린 템플릿을 스펙·Python으로 내려받는다. 노벨챗 매크로·로어북 JSON과 정규식을 사이트와 같은 규칙(JS 문법, safe-regex 위험 판정, 매크로 실행 순서, {user} 조사 보정)으로 검사·실행하고, 시네마챗·노벨챗 제작 가이드를 담는다. 처음 쓸 때는 `comet setup`이 설치·설정·로그인을 한 번에 한다. Trigger — "코멧 템플릿", "코멧 설치", "comet setup", "시네마챗 템플릿 만들어", "시네마챗 UI", "템플릿 업로드", "노벨챗 매크로", "코멧 정규식", "코멧 로어북", "comet-talk", "/comet-template".
---

# comet-template — 코멧 시네마챗 템플릿 제작·업로드

시네마챗은 16:9 화면에서 AI가 낸 **로그**(변수 하나를 새 값으로 바꾸는 단위)를 하나씩 재생하는 채팅이다. **템플릿**은 그 화면의 UI다: 변수, 1280×720 캔버스 위 오브젝트(텍스트·이미지·프레임), 블록 스크립트, 스토리지, 트리거. 이 도구는 템플릿 하나를 `template.json` 스펙 한 장(+ 로직 파일 `logic.py`)으로 다룬다.

| 문서 | 언제 읽나 |
|---|---|
| 이 문서 | 명령, 스펙 필드, DSL, 렌더·입력 규칙, 미리보기, 업로드 |
| [RULES.md](RULES.md) | 스크립트를 Python으로 쓸 때: 구문 → 블록 대응, JS 값 규칙, 풀어 쓰기, `logic.py` |
| [GUIDE.md](GUIDE.md) | 시네마챗·노벨챗 제작 전반: 칸별 제한, 프롬프트, 로어북, 매크로·정규식, 이미지, 점검표 |

### 유저 설정과 창작 자료

작업을 시작할 때 `comet config show`를 한 번 돌린다. 유저가 `comet setup`에서 정한 값이 나온다.

| 키 | 에이전트가 하는 일 |
|---|---|
| `materials` | 유저의 창작 자료(캐릭터 체크리스트, 이름 규칙, 세계관 문서, 그림체 등) 목록. 항목마다 `언제`에 적힌 단계 전에 그 파일을 읽고, 이 문서와 GUIDE.md의 일반론보다 앞세운다. 폴더면 그 안의 문서를 같은 자격으로 읽는다 |
| `prompt_template` | 시네마챗·노벨챗 프롬프트는 이 파일을 복사해 고정부는 그대로, 슬롯만 채워 쓴다. 비어 있으면 GUIDE.md 5장 구조로 쓴다 |
| `works_dir` | 완성품을 `works_dir/{템플릿,시네마챗,노벨챗}/<작품>/`에 둔다. 비어 있으면 유저가 말한 곳이나 현재 폴더 |
| `username`·`font`·`chrome_profile` | `preview`·`login`의 기본값. 명령 옵션이 이긴다 |

값이 비었거나 "(없는 경로)"로 나오면 그 항목은 건너뛰고, 필요하면 유저에게 경로를 물어 `comet config set …`·`comet config material add …`로 저장한다.

## 실행

```bash
comet <명령> …        # comet setup이 설치한 명령. 없으면: uv run <이 폴더>/comet.py <명령> … (또는 python3)
```

| 명령 | 하는 일 |
|---|---|
| `setup [--yes] [--link] [--skills-dir D] [--bin-dir D] [--works-dir D] [--material 경로=언제 …] [--login\|--sid 값]` | 초기 세팅: 환경 점검 → 스킬 폴더(기본 `~/.claude/skills/comet-template`)·`comet` 명령(기본 `~/.local/bin`) 설치 → 설정 → 로그인. 터미널이면 단계마다 묻고, `--yes`면 기본값과 이미 저장된 값으로 묻지 않고 끝낸다. 다시 돌려도 안전하다(업데이트 = `git pull` 뒤 다시 실행, `--link`면 `git pull`만) |
| `doctor` | Python·Pillow·uv·node·한글 글꼴·Chrome·로그인된 프로필·세션·PATH 점검. 필수가 빠지면 종료 코드 1 |
| `config [show]` · `config get K` · `config set K V` · `config unset K` · `config material add 경로 --when 단계` · `config material remove 경로` · `config path` | 설정 보기·바꾸기(`<설정 폴더>/config.json`). 경로 값은 절대 경로로 저장된다 |
| `login [--sid 값] [--profile 'Profile 1']` | 크롬에 로그인된 코멧 세션(`connect.sid`)을 가져와 `<설정 폴더>/session.json`(600)에 저장. 크롬 프로필을 임시 폴더에 복사해 헤드리스로 띄우고 CDP로 쿠키를 받는다. 실제 크롬과 키체인은 건드리지 않는다 |
| `whoami` | 세션 계정 |
| `new <폴더> [--example vn-dialogue]` | 예제를 복사해 새 작업 폴더 |
| `check spec.json [--strict] [--show]` | 오프라인 검사. 오류는 업로드 차단, 경고는 의도 확인용. `--show`는 스크립트를 정규형으로 다시 출력 |
| `preview spec.json [옵션]` | PNG와 레이어 상태표. 아래 "미리보기" |
| `upload spec.json [--new] [--id ID] [--public] [--dry-run -o p.json]` | 검사 → 로컬 파일 업로드(내용 해시 캐시) → 등록/수정 → 서버 저장본 대조 |
| `list [--own]` | 내가 올린 템플릿 / `--own`: 시네마챗에서 고를 수 있는 템플릿(보유·구매 포함) |
| `pull <ID> [-o spec.json] [--raw] [--lang py]`, `pull --own N` | 템플릿을 스펙으로 풀어 내려받기. `--lang py`면 스크립트를 옆의 `logic.py`(Python)로. 남의 공개 템플릿·기본 템플릿 연구용 |
| `delete <ID> --yes` | 내 템플릿 삭제(되돌릴 수 없음) |
| `assets panel·button·hit·pad·thumb …` | UI 소재 PNG. 아래 "소재" |
| `py state·action·value '<코드>' [--spec s.json] [--module logic.py] [--json]` | Python 한 토막 → 블록(에디터 모양)과 다시 Python. RULES.md |
| `dsl state·action·value '<코드>' [--decompile]` | DSL 한 토막을 노드 JSON으로 |
| `regex check 패턴… [--json]` | 사이트와 같은 위험(safe-regex 2.1.1)·JS 문법 판정, 캡처 수, JS/Python 차이 함정. 위험이면 종료 코드 1 |
| `regex test 패턴 글 [--to 대체식]` | node로 JS 그대로 매치·치환(`g`) |
| `regex range LO HI` · `regex words 말…` · `regex image --names a,b --range 1 10 --url '…/$1/$2.webp' [--default N] [--anon URL] [-o f.json]` | 수 범위·단어 목록 정규식(반복 0개), 이미지 매크로 네 단계 JSON |
| `macro check m.json [--strict]` · `macro test m.json 글 [--user 이름] [--turn N] [--trace]` | 노벨챗 매크로 JSON 규격·정규식 검사 / 사이트 엔진과 같은 순서로 실행(체인, 위험 매크로 건너뛰기, `{user}` 조사 보정까지) |
| `lorebook check l.json [--strict]` | 로어북 JSON 규격·트리거·정규식 검사 |

글 인자는 `@파일`이나 `-`(stdin)로도 준다. `regex test`·`macro test`는 node가 필요하다(정확한 JS 실행). 없으면 `check`는 정적 검사만 한다.

설정 폴더는 `~/.config/comet-template`(환경변수 `COMET_CONFIG_DIR`로 바꿈)다.

작업 흐름: (처음 한 번 `setup`) → `login` → `new` → 스펙 편집 → `check` → `preview`(눈으로 확인, 클릭 시뮬레이션) → `upload` → 결과 링크의 편집 화면에서 "스크립트 실행"으로 실물 확인. 같은 스펙을 다시 `upload`하면 옆의 `template.comet.json`에 적힌 ID를 **수정**한다(새로 만들려면 `--new`). 완성품은 설정 `works_dir` 아래 `{템플릿,시네마챗,노벨챗}/`에 작품별 폴더로 둔다.

## 1. 동작 모델 (이걸 알아야 설계가 맞는다)

- **로그**: AI 응답 하나 = 로그 여러 개 `(변수명, 값)`. 플레이어가 넘길 때마다 다음 로그 하나가 실행되어 그 변수만 바뀐다. 되감기(prev)는 가장 최근 로그를 취소한다.
- **자동 넘기기(auto)**: 이 변수를 바꾸는 로그가 실행되면 다음 로그까지 곧바로 실행한다. 배경·인물·이름·상태처럼 "대사와 함께 바뀌어야 할" 변수는 auto, 플레이어가 읽고 넘겨야 하는 대사는 auto 아님. 프롬프트의 "동시 변경 순서"에서 auto 변수를 먼저 출력시키면 그림과 대사가 한 번에 바뀐다.
- **변수 3종**
  - `유저 입력`: 모든 템플릿에 자동으로 있는 변수. 플레이어가 보낸 메시지가 들어간다. 이름 변경·삭제 불가, 스펙에 안 써도 된다.
  - `global`(전역, 플랫폼 표기 mode `default`): **AI만** 로그로 바꾼다. 1~20개, 최소 1개 필수. `type`은 AI 출력 제한용: `string`·`integer`·`number`·`boolean`.
  - `system`(시스템, mode `none`): **스크립트만** 바꾼다(action 스크립트의 `$이름 = 값`). 최대 200개. 창 열림 여부, 열린 시각 같은 UI 상태용. AI는 못 본다.
  - 변수명: 10자 이내, 서로 다르게, `}`·줄바꿈 금지. 모든 값은 문자열로 저장된다.
- **기본값과 인트로**는 템플릿이 아니라 시네마챗(캐릭터) 쪽 "시작 세션"에서 변수별로 정한다. 그래서 템플릿은 빈 값(`""`)을 늘 견뎌야 한다(이름이 비면 이름표 숨기기 등).
- **시네마챗 이미지**: 프레임에 들어온 값이 시네마챗 이미지 탭에 등록된 **이미지 이름**이면 그 이미지를, 아니면 값을 URL로 쓴다. 그래서 배경·인물용 전역 변수에는 "이미지 이름"을 쓰라고 프롬프트에 적는다. 템플릿 자체 스토리지 이미지는 프레임이 이름으로 찾지 않는다(`img("키")`로 URL을 얻어야 함).

## 2. 스펙 `template.json`

```jsonc
{
  "title": "…",            // 1~20자 (JS 길이: 이모지는 2)
  "description": "…",      // 1~2500자. 시네마챗 제작자에게 보일 사용법
  "visibility": "private", // private | public (템플릿에 링크 공개는 없음)
  "thumbnail": "thumb.png",// 필수. 로컬 파일(16:9가 아니면 가운데 잘라 올림) 또는 image.comet-talk.ai URL. 5MB 이하
  "prompt": "…",           // 1~1000자. AI에게 변수 쓰는 법 (4장)
  "variables": [ {"name": "대사", "kind": "global", "type": "string", "auto": false},
                 {"name": "창열림", "kind": "system"} ],
  "images": {"효과음판": "assets/a.png"},   // 템플릿 이미지 스토리지: 키 → 파일|URL (최대 1000)
  "audio":  {"bgm": "assets/bgm.mp3"},      // 오디오 스토리지 (최대 1000, mp3·wav·ogg·webm, 5MB)
  "lang": "py",             // 스크립트 문자열의 언어: py(RULES.md) | cs(DSL, 기본)
  "scripts": "logic.py",    // 선택. @state/@action/@value/@on 함수로 레이어 칸·트리거를 채움(lang은 py로)
  "layers": [ … ],          // 아래(배경)→위 순서. 최대 200
  "triggers": [ … ]         // 생략하면 ←=prev, →=next 두 개. 빈 배열이면 트리거 없음. 최대 200
}
```

이미지 파일 형식: jpeg·png·webp·gif, 5MB 이하. 외부 URL은 서버가 거부한다(로컬 경로를 주면 도구가 올린다). 같은 파일은 다시 올리지 않는다(`<설정 폴더>/uploads.json`).

### 레이어

`layers`는 **아래→위**로 쓴다. 플랫폼은 반대(0번이 맨 위)로 저장하며 도구가 뒤집는다. 겹치면 뒤에 쓴 것이 위에 그려지고, **클릭도 위 레이어부터** 판정한다.

공통 키: `type`(text·image·frame), `name`(에디터 목록 이름), `x`·`y`(왼쪽 위), `width`·`height`, `state`(상태 스크립트), `note`(메모, 업로드 안 함). 캔버스 1280×720, 소수 허용.

| type | 키 (기본값) | 뜻 |
|---|---|---|
| `text` | `value` **필수** | 표시값 스크립트. 보통 `"$대사"` |
| | `fontSize` 32, `fontWeight` 500 | px, 두께(글꼴마다 지원 두께가 다름) |
| | `fontFamily` "Pretendard Variable" | 에디터 목록: Pretendard, 나눔스퀘어_ac, 경기천년제목, 에스코어드림, 나눔바른고딕, 이순신체, D2Coding, 검은고딕, 잉크립퀴드, 조선굴림체, 경기천년바탕, Neo둥근모 |
| | `color` "#FFFFFF" | CSS 색. `#RRGGBBAA`로 반투명 |
| | `textAlign` center | left·center·right |
| | `easing` none | none·typing(한 글자씩)·fade. 속도는 플레이어 설정 |
| | `overflow` hidden | hidden·scroll(휠·드래그 스크롤) |
| | `whiteSpace` normal | normal(줄바꿈 무시, 공백 압축)·pre-wrap(줄바꿈 유지) |
| `image` | `src` **필수** | `images` 키, 로컬 파일, image.comet-talk.ai URL. 항상 contain으로 맞춤 → PNG를 레이어 비율로 만든다 |
| | `action` | 클릭 스크립트. 비어 있으면 클릭이 아래 레이어로 통과 |
| `frame` | `value` **필수** | 이미지 이름 또는 URL을 내는 표시값 스크립트. 보통 `"$배경"` |
| | `objectFit` contain | contain(여백)·cover(잘라 채움) |
| | `moping` none, `duration` 300 | 전환: none·fade(사라진 뒤 등장)·cross-fade(겹쳐 전환), ms |
| | `borderRadius` 0 | 모서리 둥글기(px) |

### 트리거

```json
{"on": "load", "do": "$창열림 = false"}          // 템플릿 처음 로드 때 1회
{"on": "key:KeyS", "do": "…"}                     // KeyboardEvent.code: KeyA~KeyZ, Digit0~9, Space, Enter, Escape, ArrowLeft…, F1…
```
같은 이벤트의 트리거는 위에서부터 차례로 돈다. `do`는 action 스크립트다. 입력칸(textarea)에 초점이 있으면 키 트리거는 돌지 않는다.

## 3. 스크립트 언어

두 가지 중 하나로 쓴다. **Python**(`"lang": "py"` 또는 `"scripts": "logic.py"`)은 익숙한 문법을 블록으로 컴파일하고 `%`·삼항·f-string·도우미 함수까지 풀어 쓴다. 규칙은 [RULES.md](RULES.md). 아래 **DSL**(기본, `"lang": "cs"`)은 블록과 한 줄씩 대응하는 문법이다. 두 예제(`examples/vn-dialogue-py/`, `examples/vn-dialogue/`)가 같은 블록을 낸다.

플랫폼의 블록 코딩을 줄 단위 문법으로 쓴다. 블록 하나 = 한 줄, `if`는 들여쓰기(공백). 한 줄에 여러 단순 문장은 `;`로 잇는다. `#`은 주석.

**어디서 무엇을 쓸 수 있나** (플랫폼 에디터의 규칙 그대로. 어기면 도구가 오류를 낸다)

| 스크립트 | 위치 | 쓸 수 있는 문장 |
|---|---|---|
| `state` | 모든 레이어 | `show` `hide` `x = e` `y = e` `width = e` `height = e` `opacity = e`(0~1), `@지역 = e`, `if` |
| `action` | image `action`, 트리거 `do` | `prev` `next` `play e` `stop_audio` `volume e`(0~100), `$시스템 = e`, `@지역 = e`, `if` |
| `value` | text·frame `value` | `@지역 = e`, `if`, 마지막 줄 `return e`(식 하나만 쓰면 그 식이 return) |

**조건**: `if c:` / `elif c:` / `else:`(elif는 else 안의 if로 저장됨). `pass`는 빈 블록.

**값**
- 상수 `12` `-3.5` `"글자"` `true` `false`, 템플릿 변수 `$대사` · 공백 있으면 `${유저 입력}`, 지역 변수 `@t`
- `timer`(Date.now() ms), `mouse_x` `mouse_y`(캔버스 좌표)
- `a + b` `a - b` `a * b` `a / b`, 이어 붙이기 `a .. b` 또는 `join(a, b)`, `-a`
- `len(a)` `sin(a)` `cos(a)`(도 단위) `round(a)` `sqrt(a)`, `img("키")`·`audio("키")`(스토리지 URL, 없으면 "")

**조건식**: `a == b` `!=` `<` `<=` `>` `>=`, `and` `or` `not`, `true` `false`, `is_first`(첫 로그 위치) `is_last`(마지막 로그). 맨값 조건은 없다: `if $열림:` 대신 `if $열림 == true:`.

**값 규칙** (JS 동작을 그대로 옮긴 것. 미리보기도 같다)
- 모든 변수는 문자열로 저장되고 읽을 때 `"true"`/`"false"`는 참거짓, 숫자 모양은 숫자로 바뀐다.
- `+`는 **숫자+숫자, 글자+글자만** 더한다. `1 + "a"`는 `1`(오른쪽 버림). 글자를 붙이려면 `..`.
- `/`는 소수 둘째 자리에서 반올림(`1/3` = 0.33), 0으로 나누면 왼쪽 값. `"ab" * 2` = "abab". DSL에는 나머지 연산이 없다(주기는 `sin(timer * k)`로). Python 문법은 `%`·`//`를 정확히 풀어 쓴다(RULES.md 6장).
- `==`는 JS 느슨한 비교(`"1" == 1`, `"" == 0`). 숫자 모양 글자는 읽을 때 숫자가 되어 `"10" < "9"`는 거짓, 숫자 모양이 아닌 글자끼리만 사전순.
- 지역 변수(`@`)는 스크립트가 한 번 돌 때만 산다. 다른 스크립트·다음 프레임으로 넘기려면 시스템 변수.
- `state`·`value` 스크립트는 **매 프레임** 다시 계산된다. 그래서 `timer`로 애니메이션이 된다. `action`은 클릭·키·로드 때 한 번.
- 스크립트로 바뀐 x·y·크기·투명도는 그 프레임의 표시값일 뿐 레이어 원래 값은 그대로다(다음 프레임에 state가 다시 정함).

**등장 효과 조건**: text `easing`(typing·fade)과 frame `moping` 전환은 **return 식에 직접 쓴 전역 변수**가 로그로 바뀔 때만 돈다. `@s = $상태; return @s`처럼 지역 변수를 거치거나, 시스템 변수·`유저 입력`이면 효과 없이 바로 바뀐다(`check`가 경고). 타이핑·페이드 중에 next를 누르면 넘기지 않고 효과를 끝까지 보여 준다.

## 4. 렌더·입력 규칙

- 레이어는 아래→위로 그려진다. `hide`는 그리지도 클릭받지도 않고, `opacity = 0`은 안 보여도 **클릭은 받는다**.
- 클릭: 위 레이어부터 "보이는 image이면서 action이 비지 않았고 점을 포함하는" 첫 레이어 하나만 받는다. 판정은 PNG 픽셀이 아니라 레이어 상자 기준이다.
  - 투명 PNG(`comet assets hit`)를 깔면 보이지 않는 터치 영역이 된다.
  - 클릭을 막기만 할 레이어(모달 몸통)는 action에 무해한 문장을 둔다: `@흡수 = 1`. 비워 두면 클릭이 아래로 샌다.
- 텍스트: 위 여백 0.2×fontSize, 줄 높이 1.4×fontSize. 줄 수 × 1.4 × size + 0.2 × size가 height를 넘으면 잘리거나(hidden) 스크롤(scroll)된다(`preview`가 "N px 넘침"으로 알림). `*별표*` 사이 글자는 70% 불투명도로 그려지고 별표는 안 보인다(나레이션 표기). `{user}`는 플레이어 이름으로 바뀐다(조사 을/를 등 자동 보정).
- 프레임 값이 비거나 이미지가 없으면 아무것도 안 그린다.

## 5. 프롬프트(≤1000자) 쓰는 법

AI는 템플릿 프롬프트로 변수 쓰임을 배운다. 전역 변수마다 무엇을, 언제 바꾸는지 한 줄씩, 그리고 동시에 바꿀 때 순서를 적는다(auto 변수 먼저, 넘김을 멈출 변수 마지막). 한 로그에는 한 문장(줄바꿈 금지는 시스템이 이미 넣는다). 이미지 변수에는 "등록된 이미지 이름"을 쓰라고 적는다. 프롬프트에 이름이 안 나오는 전역 변수는 `check`가 경고한다.

```md
## 변수
- 배경: 장면 배경 이미지 이름. 장소나 시간이 바뀔 때만 바꾼다.
- 대사: 대사 한 마디 또는 나레이션 한 문장. 나레이션은 *별표*로 감싼다.
## 동시에 바꿀 때 순서
1. 배경 2. 대사
```

## 6. 레시피

```text
# 값이 비면 숨기기 (state)
if $이름 == "":
    hide

# 첫 로그에서는 이전 버튼을 흐리게 (state)
if is_first:
    opacity = 0.35

# 토글 버튼 (action) — 시각을 같이 기록해 애니메이션 기준점으로
if $창열림 == true:
    $창열림 = false
else:
    $창열림 = true
$창시간 = timer

# 0.25초 페이드 + 40px 슬라이드 업 (state, 레이어 원래 y = 180)
@t = timer - $창시간
if $창열림 == true:
    if @t < 250:
        opacity = @t / 250
        y = 220 - 40 * sin(@t * 90 / 250)
elif @t < 250:
    opacity = 1 - @t / 250
    y = 180 + 40 * sin(@t * 90 / 250)
else:
    hide

# 깜빡이는 ▼ 넘김 표시 (state)
opacity = 0.6 + 0.4 * sin(timer * 0.36)

# 마우스를 따라가는 커서 (state)
x = mouse_x - 16
y = mouse_y - 16

# 배경 음악 (트리거 load)
volume 40
play audio("bgm")

# 전역 숫자 변수로 게이지 폭 (state; $호감도 0~100)
width = 300 * $호감도 / 100
```

UI 관례: 화면 전체 투명 터치 레이어(`action: next`)를 클릭 레이어 중 맨 아래에 두고 버튼은 그 위에. 모바일 터치 버튼은 아이콘보다 넓게(`assets button --pad 16`). 키 트리거로 → / Space / Enter = next, ← = prev를 주면 PC 플레이가 편하다. 예제 `examples/vn-dialogue/`가 이 전부(배경·인물 프레임, 이름표, 타이핑 대사, 유저 입력 표시, 이전·다음, 상태창 모달과 애니메이션, 키 7개)를 담고 있다.

## 7. 미리보기

```bash
comet preview template.json -o out.png \
  --set 대사="안녕" --set 배경=교실 --char-image 교실=sample/bg.png \
  --event load --event click:"상태 버튼" --after 120 --boxes
```

- 사이트 렌더러와 해석기를 옮긴 것: 값 규칙·state 실행·그리기 순서·줄바꿈·`*` 반투명·contain/cover가 같다. 글꼴만 로컬 한글 글꼴로 대신한다(폭이 조금 다를 수 있음).
- `--set 변수=값`(여러 번), `--char-image 이름=파일`(프레임이 받을 시네마챗 이미지), `--now ms`(timer), `--mouse x y`, `--pos first|middle|last`(is_first/is_last), `--username`.
- `--event`는 순서대로: `load`, `key:<code>`, `click:<레이어 이름|아래→위 번호>`(그 레이어 가운데를 누름), `tap:x,y`. 클릭은 사이트처럼 위에서부터 받을 레이어를 찾고, 누가 받았는지와 부수효과(next·prev·play·시스템 변수)를 출력한다.
- `--after ms`: 이벤트를 그만큼 전에 일어난 것으로 보고 그린다 → 애니메이션 중간 프레임.
- `--boxes`: 레이어 테두리와 플랫폼 인덱스. 출력 표에는 레이어마다 보임·위치·투명도·표시값·줄 수·넘침이 나온다.
- `--id <ID>`로 올린 템플릿을 그대로 그릴 수도 있다.

## 8. 업로드와 서버 동작 (2026-10-16 실측)

- `POST /upload/newtemplate`(새로)·`/upload/template/{id}`(수정), 본문은 `{thumbnail, visibility, price: 0, title, description, rawTemplate}`. 성공은 빈 200이라 도구가 `/get/mytemplate` 전후 비교로 새 ID를 찾는다.
- 400이면 `{"msg", "goto"}`: goto는 편집기 탭 번호(1 기본 정보, 2 프롬프트·변수·스크립트). 실측 메시지: "썸네일을 업로드해 주세요", "외부 이미지 썸네일은 사용 불가능합니다", "제목/설명이 너무 깁니다", "프롬프트를 입력해주세요", "프롬프트 길이 제한을 초과했어요", "전역변수가 필요합니다", "유저 입력 변수가 손상되었습니다", "변수명이 너무 깁니다", "변수 이름이 중복되었습니다", "사용 불가능한 스크립트가 감지되었습니다"(없는 변수 읽기, 전역 변수에 대입, state에서 시스템 변수 대입), "외부 이미지는 사용할 수 없습니다", "응답 형식이 올바르지 않습니다"(필드 누락·타입 오류·visibility=link).
- **조용히 고치는 것**: 모르는 노드 타입은 오류 없이 `NULL`로 바뀌고, 모르는 트리거 조건이 하나라도 있으면 트리거 배열 전체가 기본(←/→)으로 바뀐다. 글꼴 이름은 검사하지 않는다. 그래서 `upload`는 올린 뒤 저장본을 다시 받아 보낸 것과 대조해 "보낸 그대로 ✓" 또는 바뀐 칸을 알린다.
- 올린 템플릿은 `list --own`에 바로 나타나 시네마챗 제작 2번 탭 "템플릿"에서 고를 수 있다.
- 링크: 편집 `https://comet-talk.ai/build/template/<ID>`, 상세 `https://comet-talk.ai/detail/template/<ID>`.

## 9. 소재 (`comet assets`)

| 명령 | 예 |
|---|---|
| `panel out.png --w 1200 --h 210 --fill "#0E1018D9" [--gradient-to "#0A0B12F2"] [--radius 18] [--border "#FFFFFF2E"]` | 대사창·이름표·모달 |
| `button out.png --size 56 --icon chevron-right [--shape circle|square|none] [--fill …] [--fg …] [--pad 16]` | 아이콘: arrow-left·arrow-right·chevron-left·chevron-right·play·pause·menu·close·plus·minus·dot·none |
| `hit out.png` | 투명 터치 영역 |
| `pad in.png out.png --margin 20` | 기존 아이콘에 터치 여백 |
| `thumb out.png [--src 그림] [--title 제목]` | 16:9 썸네일. 미리보기 PNG를 썸네일로 써도 된다 |

## 10. 세션

- 크롬이 있는 기기(macOS·Windows·Linux): `comet login`이 설정 `chrome_profile`을, 비어 있으면 코멧 쿠키가 가장 최근인 프로필을 고른다. 세션은 요청마다 30일 연장되고(서버가 `Set-Cookie`로 갱신), 도구가 새 값을 저장한다.
- 크롬이 없는 기기(서버 등): 다른 기기의 `<설정 폴더>/session.json`을 복사하거나 `comet login --sid <값>`, 또는 환경변수 `COMET_SID`. `connect.sid` 값은 브라우저 개발자 도구 → Application → Cookies → `comet-talk.ai`에서 본다.
- `session.json`은 계정 열쇠다. 공유·커밋하지 않는다(파일 권한 600).
- 401이 나면 크롬에서 코멧에 다시 로그인한 뒤 `comet login`.

## 파일

| 경로 | 역할 |
|---|---|
| `comet.py` | 진입점(PEP 723: pillow) |
| `install.sh` | 설치 진입점(macOS·Linux): uv 또는 Python 3.10+를 찾아 `comet setup`을 부른다. Windows는 `python comet.py setup` |
| `comet_tpl/setup.py` | `setup`·`doctor`: 환경 점검, 스킬 복사·링크, `comet` 명령(sh·cmd) 설치, 설정 마법사, 로그인 |
| `comet_tpl/config.py` | `config.json` 읽기·쓰기, 키 표(기본값과 뜻), 창작 자료 목록 |
| `comet_tpl/dsl.py` | DSL ↔ 노드 JSON, 문맥 규칙, 구조 검사 |
| `comet_tpl/pyc.py` | Python 부분집합 → 블록 컴파일러, 블록 → Python 출력기 |
| `comet_tpl/regex.py` | 정규식 위험 판정(safe-regex 2.1.1 이식)·JS 문법, node 실행기(매크로 엔진·`{user}` 보정), 범위·단어·이미지 매크로 생성, 매크로·로어북 JSON 검사 |
| `comet_tpl/spec.py` | 스펙 ↔ 페이로드, 제한·참조 검사, 경고 |
| `comet_tpl/api.py` | HTTP(세션 갱신·재시도), 업로드 캐시, 템플릿 CRUD |
| `comet_tpl/auth.py` | 크롬 프로필 복사본 + 헤드리스 CDP로 세션 꺼내기(표준 라이브러리 웹소켓) |
| `comet_tpl/render.py` | 해석기·렌더러 이식, 클릭 라우팅 |
| `comet_tpl/assets.py` | UI 소재 PNG |
| `examples/vn-dialogue/` | 완성 예제(DSL): `template.json`, `assets/`, 미리보기용 `sample/`, `thumb.png` |
| `examples/vn-dialogue-py/` | 같은 템플릿의 Python판: 한 줄 스크립트는 스펙에, 상태창 로직은 `logic.py` |
| `RULES.md` · `GUIDE.md` | Python 규칙서 · 제작 가이드 |
| `tests/test_core.py` · `test_pyc.py` · `test_regex.py` | 오프라인 테스트(각 파일을 `python3`로 실행). regex의 node 테스트는 node 없으면 건너뜀. 공식 기본 템플릿 픽스처 |
| `~/.config/comet-template/` (`COMET_CONFIG_DIR`) | `config.json`(설정), `session.json`(세션, 600), `uploads.json`(파일 해시 → URL) |
| `~/.cache/comet-template/img/` | 미리보기용 이미지 캐시 |

근거: 사이트 번들 `assets/index-*.js`의 zod 스키마(`vn`·`Ou`·`an`), 해석기 `ws()`, 렌더러 `SessionCanvas`, 에디터 `BuildTemplateView`·`Tab1~5`, 그리고 실측 업로드(2026-10-16).
