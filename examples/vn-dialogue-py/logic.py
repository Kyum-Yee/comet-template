"""비주얼노벨 대사창 — 템플릿 로직 (comet-template Python, 규칙: RULES.md)

간단한 표시값·버튼은 template.json에 한 줄로 두고, 상태창처럼 여러 레이어가 함께 움직이는 로직은 여기 모은다.
전역(AI) 변수: 배경 인물 이름 대사 상태 / 시스템 변수: 창열림 창시간
"""

FADE = 250   # 상태창 애니메이션 길이(ms)
RISE = 40    # 열릴 때 아래에서 떠오르는 거리(px)


def popup(base_y):
    """상태창 구성 레이어 공통: 열릴 때 페이드인 + 떠오르기, 닫힐 때 페이드아웃 + 가라앉기."""
    t = timer - 창시간
    if 창열림 == True:
        if t < FADE:
            self.opacity = t / FADE
            self.y = base_y + RISE - RISE * sin(t * 90 / FADE)
    elif t < FADE:
        self.opacity = 1 - t / FADE
        self.y = base_y + RISE * sin(t * 90 / FADE)
    else:
        hide()


def close_window():
    창열림 = False
    창시간 = timer


def toggle_window():
    if 창열림 == True:
        창열림 = False
    else:
        창열림 = True
    창시간 = timer


# ---- 상태창 모달 ---------------------------------------------------------

@action("상태 버튼")
def _():
    toggle_window()


@state("창 배경막")
def _():
    t = timer - 창시간
    if 창열림 == True:
        show()
        if t < FADE:
            self.opacity = t / FADE
    elif t < FADE:
        self.opacity = 1 - t / FADE
    else:
        hide()


@action("창 배경막")      # 바깥을 누르면 닫힘
def _():
    close_window()


@state("상태창")
def _():
    popup(180)


@action("상태창")         # 비어 있지 않은 action이 클릭을 흡수해 배경막으로 새지 않게 한다
def _():
    클릭흡수 = 1


@state("상태 제목")
def _():
    popup(204)


@value("상태 제목")
def _():
    return "상태"


@state("상태 내용")
def _():
    popup(262)


@value("상태 내용")
def _():
    if 상태 == "":
        s = "아직 기록된 상태가 없습니다."
    else:
        s = 상태
    return s


@state("닫기")
def _():
    popup(188)


@action("닫기")
def _():
    close_window()


# ---- 키보드 ----------------------------------------------------------------

@on("load")
def _():
    창열림 = False
    창시간 = timer - 1000


@on("key:ArrowLeft")
def _():
    prev()


@on("key:ArrowRight")
@on("key:Space")
@on("key:Enter")
def _():
    next()


@on("key:KeyS")
def _():
    toggle_window()


@on("key:Escape")
def _():
    if 창열림 == True:
        close_window()
