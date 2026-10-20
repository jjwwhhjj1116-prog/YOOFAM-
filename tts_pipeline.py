# tts_pipeline.py
# 대본([화자|감정]"대사" + 나레이션) → 이중 TTS → (선택)영상 매칭
# 나레이션 = ElevenLabs / 대사 = Supertone (화자별 성우 + 감정)
#
# 사용:
#   python tts_pipeline.py tts   대본.txt          # 오디오만 생성
#   python tts_pipeline.py match scene_map.json    # 영상 매칭
#   python tts_pipeline.py list                    # 슈퍼톤 성우 목록 조회(세팅용)

import os, re, sys, json, math, time, hashlib, subprocess

# ─── .env 로딩 (python-dotenv) ───────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv 미설치 시에도 시스템 환경변수로 동작하도록 무시
    pass

from elevenlabs.client import ElevenLabs
from elevenlabs.types.voice_settings import VoiceSettings
from supertone import Supertone

# ─── 설정 ────────────────────────────────────────────
EL = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
ST = Supertone(api_key=os.environ["SUPERTONE_API_KEY"])

OUT_AUDIO   = "오디오"          # 001.mp3 … (문장 단위 조각)
OUT_SCENE   = "장면오디오"      # 001.mp3 … (장면 단위 병합, legacy)
OUT_MATCH   = "매칭"           # 001_matched.mp4 …
OUT_SRT     = "자막"           # 전체.srt / 001.srt …
VIDEO_DIR   = "영상클립"        # 001.mp4 … (Flow에서 받아 정리한 클립)
for d in (OUT_AUDIO, OUT_SCENE, OUT_MATCH, OUT_SRT):
    os.makedirs(d, exist_ok=True)

# voice_map.json  : 역할 ↔ 성우 (영구 고정, 작품 바뀌어도 안 건드림)
with open("voice_map.json", encoding="utf-8") as f:
    VOICE_MAP = json.load(f)

# character_cast.json : 캐릭터 ↔ 역할 (작품마다 새로 씀). 없으면 빈 캐스트.
CAST_PATH = "character_cast.json"
if os.path.exists(CAST_PATH):
    with open(CAST_PATH, encoding="utf-8") as f:
        CHARACTER_CAST = json.load(f)
else:
    CHARACTER_CAST = {}
    print(f"[경고] {CAST_PATH} 없음 → 모든 대사가 기본 역할로 처리됩니다.")

# character_cast에 없는 화자를 처리할 기본 역할 (voice_map에 존재해야 함)
DEFAULT_DIALOGUE_ROLE = "남자1"

# 슈퍼톤 대사 TTS 모델. 모델 바꿀 땐 이 한 곳만 수정.
#   sona_speech_1(Sona 1) / sona_speech_2(Sona 2) / supertonic_api_1 / supertonic_api_3 …
SUPERTONE_MODEL = "sona_speech_2"

# ── 감정어 → style 후보 우선순위(같은 계열; 앞일수록 이상적) ──
# 성우가 지원하는 '첫' style을 고른다. 앞쪽 이상적 style이 없으면 같은 방향의 근사 style로 내려감.
# 핵심: 14개 역할 전부 {angry, happy, sad, neutral}은 공통 지원하므로, 모든 체인이 방향에 맞는
#   공통 앵커(격함=angry / 슬픔=sad / 밝음=happy)로 끝나게 해 neutral 최후폴백을 사실상 없앤다.
#   (담담·차분·침착·무심 계열만 neutral이 '의도된' 톤 → CALM_OK)
EMOTION_CHAINS = {
    # 화난·격한계열 → angry
    "분노":  ["angry", "annoyed", "excited"],
    "격노":  ["angry", "annoyed", "excited"],
    "분개":  ["angry", "annoyed", "excited"],
    "다급":  ["annoyed", "excited", "anxious", "angry"],
    "발악":  ["angry", "scared", "painful", "excited"],          # 필사적 몸부림 = 분노+공포
    "광기":  ["evil", "excited", "dominating", "angry"],         # 광기 = 사악/과열 → angry
    "격분":  ["angry", "annoyed", "excited"],
    # 차가운·조소계열(냉소/조롱/비웃음) → 최후 angry
    "냉소":  ["teasing", "evil", "suspicious", "annoyed", "angry"],
    "조롱":  ["teasing", "evil", "suspicious", "annoyed", "angry"],
    "조소":  ["teasing", "evil", "suspicious", "annoyed", "angry"],
    "비웃음": ["teasing", "evil", "suspicious", "annoyed", "angry"],
    "냉혹":  ["dominating", "evil", "suspicious", "teasing", "annoyed", "angry"],  # 냉혈·잔혹
    "서늘함": ["suspicious", "evil", "dominating", "teasing", "annoyed", "angry"], # 서늘=냉기 위협
    "위협":  ["dominating", "teasing", "suspicious", "evil", "annoyed", "angry"],
    # 단호·결의계열 → 최후 angry(강한 어조)
    "단호":  ["firm", "command", "courageous", "dominating", "angry"],
    "결의":  ["firm", "command", "courageous", "dominating", "angry"],
    "결사":  ["firm", "courageous", "command", "dominating", "angry"],  # 결사(必死) 각오
    "확신":  ["firm", "command", "confident", "courageous", "angry"],
    "오만":  ["dominating", "firm", "teasing", "evil", "command", "angry"],
    # 슬픈계열 → sad
    "슬픔":  ["sad"],
    "절망":  ["sad", "painful"],
    "오열":  ["sad", "painful", "scared"],                       # 오열=통곡
    "체념":  ["sad", "neutral"],
    "비장":  ["firm", "courageous", "sad"],
    "애원":  ["sad", "scared", "anxious"],
    # 밝은계열 → happy
    "다정":  ["friendly", "kind", "happy"],
    "기쁨":  ["happy", "excited", "friendly"],
    "희열":  ["excited", "happy", "admiring", "friendly"],       # 희열=환희/쾌감
    "안도":  ["relieved", "serene", "happy"],
    "감탄":  ["admiring", "happy", "excited"],
    # 놀란·두려운계열 → 지원 없으면 격함(angry)/슬픔(sad)으로 근사
    "놀람":  ["surprised", "scared", "excited", "angry"],
    "경악":  ["surprised", "scared", "excited", "angry"],
    "당황":  ["embarrassed", "anxious", "scared", "surprised", "annoyed", "angry"],  # 당황=허둥/동요
    "두려움": ["scared", "anxious", "surprised", "sad"],
    "절규":  ["scared", "angry", "painful", "excited"],
    # 차분·무표정계열(neutral이 정상 톤)
    "담담":  ["neutral"],
    "차분":  ["serene", "neutral"],
    "침착":  ["serene", "neutral"],
    "무심함": ["neutral"],                                       # 무심=무감정 → neutral 의도됨
    # 기타
    "수줍":  ["shy", "embarrassed", "neutral"],
    "혐오":  ["disgusted", "annoyed", "angry"],
}
# 이 감정들은 neutral로 가도 '밋밋 실패'가 아니라 의도된 톤(최후폴백 경고 제외).
CALM_OK = {"담담", "차분", "침착", "무심함"}

# ── 표에 없는 감정어도 '부분일치 키워드'로 계열 자동 판별 (매 화 새 감정어 대응) ──
#   앞 계열이 우선. 모든 계열이 성우 공통 지원 앵커(angry/happy/sad)로 끝나 neutral최후를 없앰.
KEYWORD_FAMILY = [
    (("살의", "살기", "분노", "격노", "격분", "분개", "진노", "노기", "노성", "성남",
      "화남", "화가", "울화", "격앙", "광기", "광란", "발광", "호통", "일갈", "포효",
      "증오", "적개", "적의", "앙칼", "표독", "노발", "배신", "배반", "분통", "격분"),
        ["angry", "annoyed", "excited"]),
    (("경멸", "멸시", "비웃", "비아냥", "조소", "조롱", "냉소", "냉혹", "냉담", "싸늘",
      "서늘", "비꼬", "깔보", "오만", "거만", "교만", "비열", "냉랭", "시니컬",
      "차가", "냉엄", "냉정", "도발", "비꼼", "빈정", "약올"),
        ["teasing", "evil", "suspicious", "annoyed", "angry"]),
    (("위협", "협박", "위압", "으름", "살벌", "섬뜩", "공갈", "겁박"),
        ["dominating", "teasing", "suspicious", "evil", "annoyed", "angry"]),
    (("단호", "결연", "결의", "결사", "확신", "단정", "비장", "각오", "결단",
      "자신", "진중", "묵직", "엄중", "결심", "경고", "단호히", "당당", "위엄"),
        ["firm", "command", "courageous", "dominating", "angry"]),
    (("환희", "희열", "기쁨", "기뻐", "행복", "황홀", "쾌재", "득의", "의기양양",
      "신남", "벅참", "통쾌", "흥분", "들뜸"),
        ["excited", "happy", "admiring", "friendly"]),
    (("다정", "자애", "인자", "따뜻", "온화", "자상", "부드러", "다감"),
        ["friendly", "kind", "happy"]),
    (("안도", "안심", "평온", "편안", "후련", "홀가분", "만족", "흡족", "뿌듯", "흐뭇"),
        ["relieved", "serene", "happy"]),
    (("감탄", "감격", "경외", "존경", "찬탄", "탄복", "감동", "경탄", "찬사"),
        ["admiring", "happy", "excited"]),
    (("다급", "급박", "조급", "황급", "분주", "절박"),
        ["annoyed", "excited", "anxious", "angry"]),
    (("절규", "비명", "울부", "아우성", "발악", "몸부림", "악쓰"),
        ["scared", "angry", "painful", "excited"]),
    (("두려", "공포", "겁", "무서", "불안", "초조", "전율", "오싹", "떨림", "떨리"),
        ["scared", "anxious", "surprised", "sad"]),
    (("놀람", "놀란", "경악", "충격", "기겁", "당황", "경기", "아연", "기함", "화들짝",
      "혼란", "혼미", "당혹", "얼떨", "멘붕"),
        ["surprised", "scared", "excited", "angry"]),
    (("슬픔", "슬퍼", "절망", "비탄", "비통", "애통", "애절", "오열", "통곡", "눈물",
      "서글", "처량", "비애", "한탄", "체념", "울먹", "흐느"),
        ["sad", "painful"]),
    (("고통", "괴로", "신음", "아파", "고뇌", "번민", "앓"),
        ["painful", "sad", "scared"]),
    (("애원", "애걸", "간청", "호소", "사정"),
        ["sad", "scared", "anxious"]),
    (("수줍", "부끄", "쑥스", "민망", "수치"),
        ["shy", "embarrassed", "neutral"]),
    (("혐오", "역겨", "넌더리", "진저리", "구역", "정떨어"),
        ["disgusted", "annoyed", "angry"]),
]
# 이 키워드가 들어가면 neutral이 '의도된' 톤(경고 안 냄).
#   기계음/경고음/무전 등은 평평한 전달이 정상 → neutral이 실패가 아님.
CALM_KEYWORDS = ("담담", "차분", "침착", "무심", "무표정", "덤덤", "건조", "무감", "담백", "잔잔",
                 "기계", "경고음", "무전", "전자음", "방송", "안내음", "나지막", "속삭",
                 "내추럴", "뉴트럴", "중립", "평온", "평범")   # 의도된 neutral(경고 아님)

def _emotion_chain(emotion):
    """감정어 → 계열 chain. 정확표 우선, 없으면 키워드 부분일치, 없으면 None."""
    if emotion in EMOTION_CHAINS:
        return EMOTION_CHAINS[emotion]
    for keys, chain in KEYWORD_FAMILY:
        if any(k in emotion for k in keys):
            return chain
    return None

def _calm_emotion(emotion):
    return emotion in CALM_OK or any(k in emotion for k in CALM_KEYWORDS)

# 역할별 성우가 실제 지원하는 style 집합 (Supertone list 조회 기준).
# 매핑 결과가 여기 없으면 자동 neutral 폴백 → 400 에러 방지.
SUPPORTED_STYLES = {
    "남주":  {"angry", "annoyed", "embarrassed", "excited", "firm", "happy",
             "neutral", "sad", "scared", "surprised"},
    "여주":  {"angry", "friendly", "happy", "neutral", "relieved", "sad"},
    "여주2": {"angry", "happy", "neutral", "sad", "shy", "surprised"},
    "노인":  {"angry", "courageous", "happy", "neutral", "sad", "serene"},
    "노인2": {"admiring", "angry", "anxious", "happy", "neutral", "sad"},
    "남자1": {"angry", "annoyed", "embarrassed", "happy", "neutral", "sad"},
    "남자2": {"angry", "happy", "neutral", "sad"},
    "남자3": {"angry", "command", "courageous", "happy", "neutral", "sad"},
    "남자4": {"angry", "dominating", "happy", "neutral", "sad", "suspicious", "teasing"},
    "여전사": {"angry", "courageous", "disgusted", "happy", "neutral", "sad", "suspicious"},
    "여주3":  {"neutral"},                                                # Akari(여·청년) — neutral만
    "남자5":  {"angry", "happy", "neutral", "sad"},                       # Evan(남·청년)
    "노인3":  {"angry", "happy", "jealous", "neutral", "sad"},            # Bert(남·노년)
    "노인4":  {"angry", "annoyed", "embarrassed", "happy", "neutral", "sad"},  # Osman(남·노년)
    "악역":  {"angry", "annoyed", "happy", "neutral", "sad", "teasing"},
    "악역2": {"angry", "disgusted", "evil", "happy", "neutral", "sad", "suspicious"},
    "악역3": {"angry", "dominating", "happy", "neutral", "sad", "suspicious"},
    "악역4": {"angry", "disgusted", "evil", "happy", "neutral", "sad", "suspicious"},
}

# ── 등록 안 된 화자 자동 역할 추정 (이름 키워드 + 대사량) ──────────
#   무협에서 흔한 호칭으로 노인/악역/여성을 추정. 확신 없으면 None → 주인공(대사량 1위)·기본.
_OLD_KW     = ("노인", "노옹", "노괴", "노야", "노선", "노승", "노파", "할멈", "장로", "태상",
               "원로", "조사", "사조", "존자", "방장", "선사", "진인", "도장", "노고", "노조", "옹")
_VILLAIN_KW = ("마군", "마존", "마왕", "마제", "마성", "마귀", "마인", "마두", "혈마", "혈교", "혈수",
               "혈영", "독왕", "독마", "독존", "사마", "사왕", "살수", "교주", "패왕", "괴인", "악귀")
_FEMALE_KW  = ("소저", "낭자", "여협", "여인", "부인", "공주", "아씨", "마님", "왕후", "귀비",
               "선녀", "여제", "여왕", "군주", "성녀")

def guess_role_by_name(speaker):
    """이름 호칭으로 역할 추정. 매칭 없으면 None."""
    s = speaker or ""
    if any(k in s for k in _OLD_KW):     return "노인"
    if any(k in s for k in _VILLAIN_KW): return "악역"
    if any(k in s for k in _FEMALE_KW):  return "여주"
    return None

def auto_role(speaker, is_protagonist=False):
    """등록 캐스트 > 이름 키워드(노인/악역/여주) > 주인공(대사 1위→남주) > 기본역할."""
    if speaker in CHARACTER_CAST:
        return CHARACTER_CAST[speaker]
    g = guess_role_by_name(speaker)
    if g:
        return g
    if is_protagonist:
        return "남주"
    return DEFAULT_DIALOGUE_ROLE

def role_of(speaker):
    """조용한 역할 해석(캐스트 > 이름추정 > 기본). 서명·드라이런·사전조회 공용 — 생성과 일치."""
    return CHARACTER_CAST.get(speaker) or guess_role_by_name(speaker) or DEFAULT_DIALOGUE_ROLE

def resolve_role(speaker):
    """화자 이름 → 역할. cast에 없으면 이름으로 자동 추정(노인/악역/여주), 그래도 없으면 기본."""
    role = CHARACTER_CAST.get(speaker)
    if role is None:
        role = guess_role_by_name(speaker) or DEFAULT_DIALOGUE_ROLE
        print(f"  [자동추정] '{speaker}'가 {CAST_PATH}에 없음 → 역할 '{role}' 추정 사용")
    return role

# ── 실제 지원 style 조회/캐시 (400·오폴백 방지) ──────
_STYLE_CACHE = {}   # voice_id → set(styles)

def _extract_styles(resp):
    for obj in (getattr(resp, "item", None), getattr(resp, "voice", None),
                getattr(resp, "result", None), resp):
        if obj is not None:
            st = getattr(obj, "styles", None)
            if st:
                return list(st)
    return None

def preload_voice_styles(roles):
    """실행 시작 시 사용될 역할들의 실제 지원 style을 1회 조회해 캐시."""
    for role in roles:
        vid = VOICE_MAP.get(role, {}).get("voice_id")
        if not vid or vid in _STYLE_CACHE:
            continue
        try:
            styles = _extract_styles(ST.voices.get_voice(voice_id=vid))
            if styles:
                _STYLE_CACHE[vid] = set(styles)
                print(f"  [style조회] {role}: {sorted(_STYLE_CACHE[vid])}")
        except Exception as ex:
            print(f"  [경고] get_voice({role}) 실패 → 하드코딩 SUPPORTED_STYLES 사용: {type(ex).__name__}")

def supported_styles_for(role):
    """역할의 실제 지원 style(캐시). 없으면 하드코딩 SUPPORTED_STYLES."""
    vid = VOICE_MAP.get(role, {}).get("voice_id")
    if vid and vid in _STYLE_CACHE:
        return _STYLE_CACHE[vid]
    return SUPPORTED_STYLES.get(role, set())

# ── 영어 감정 태그(FIRM/ANGRY/…) 지원 ──────────────────────────
#   대본 시리즈에 따라 감정이 영어 style명으로 옴(예: 천야검가 [서무겸|FIRM]).
#   그 style을 지원하는 성우면 그대로, 아니면 공통 앵커(angry/happy/sad/neutral)로 폴백.
#   ★ firm/excited 등은 남주 전용 → 다른 성우는 아래 표대로 화남/기쁨/슬픔/내추럴 중 하나.
_EN_FALLBACK = {
    "firm":        ["firm", "angry", "neutral"],          # 단호·확신 → 없으면 화남
    "command":     ["command", "firm", "angry", "neutral"],
    "courageous":  ["courageous", "firm", "happy", "angry", "neutral"],
    "angry":       ["angry", "neutral"],
    "annoyed":     ["annoyed", "angry", "neutral"],
    "disgusted":   ["disgusted", "angry", "neutral"],
    "evil":        ["evil", "teasing", "angry", "neutral"],
    "teasing":     ["teasing", "evil", "happy", "neutral"],
    "suspicious":  ["suspicious", "annoyed", "angry", "neutral"],
    "happy":       ["happy", "neutral"],
    "excited":     ["excited", "happy", "neutral"],
    "admiring":    ["admiring", "happy", "neutral"],
    "friendly":    ["friendly", "happy", "neutral"],
    "relieved":    ["relieved", "happy", "neutral"],
    "serene":      ["serene", "neutral", "happy"],
    "sad":         ["sad", "neutral"],
    "scared":      ["scared", "sad", "neutral"],
    "anxious":     ["anxious", "scared", "sad", "neutral"],
    "embarrassed": ["embarrassed", "sad", "neutral"],
    "surprised":   ["surprised", "scared", "angry", "neutral"],
    "neutral":     ["neutral"],
}

def _en_style_chain(emotion):
    """감정이 영어 style명이면 폴백 체인 반환. 아니면 None."""
    key = (emotion or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    return _EN_FALLBACK.get(key)

def resolve_emotion(supported, emotion):
    """supported(set) + 감정 → (style, kind). kind∈{정확매칭,근사폴백,neutral최후}.
       정확표에 없어도 키워드 계열로 근사(→ neutral최후 최소화)."""
    en = _en_style_chain(emotion)               # ★ 영어 태그 우선 처리
    if en:
        for st in en:
            if st in supported:
                # 요청한 style 그대로면 정확, 대체(예: firm→angry)면 근사
                return (st, "정확매칭" if st == en[0] else "근사폴백")
        return ("neutral", "정확매칭" if en[0] == "neutral" else "neutral최후")
    exact = emotion in EMOTION_CHAINS
    chain = _emotion_chain(emotion)            # 정확표 → 키워드 → None
    if not chain:                              # 계열조차 못 찾음
        return ("neutral", "정확매칭" if _calm_emotion(emotion) else "neutral최후")
    ideal = chain[0]
    for st in chain:                           # 계열 내 지원하는 첫 style
        if st in supported:
            kind = "정확매칭" if (exact and st == ideal) else "근사폴백"
            return (st, kind)
    if _calm_emotion(emotion) or ideal == "neutral":
        return ("neutral", "정확매칭")
    return ("neutral", "neutral최후")           # 계열 style 전무 → 밋밋 neutral

def classify(role, emotion):
    """dry-run 표시용: (style, kind). 캐시된 실제 지원 style(없으면 하드코딩)로 판정."""
    return resolve_emotion(supported_styles_for(role), emotion)

def resolve_style(role, emotion):
    """실제 전송용: 역할 기준 감정 → 지원 style(계열 근사, 최후 neutral)."""
    style, kind = resolve_emotion(supported_styles_for(role), emotion)
    if kind == "neutral최후":
        print(f"  [neutral최후] 역할 '{role}' emotion='{emotion}' → 계열 지원 style 없음 → neutral")
    return style

# ─── 1. 대본 파싱 ────────────────────────────────────
# 대사:  [화자|감정]"..."   또는  [감정]"..."(화자 생략 → 기본 대사성우)
# 나레이션: 따옴표 없는 일반 줄
# 화자|감정 구분자로 반각 '|'(U+007C)·전각 '｜'(U+FF5C) 모두 허용. 공백은 파서에서 strip.
DIALOGUE_RE = re.compile(r'^\[([^\]|｜]+)(?:[|｜]([^\]]+))?\]\s*[“”"](.+?)[“”"]\s*$')
# 한 장면 안에서 대사 [화자|감정]"..." 를 위치 무관하게 '모두' 찾는 버전(다중 화자용).
DIALOGUE_FIND = re.compile(r'\[([^\[\]|｜]+?)(?:[|｜]([^\[\]]*?))?\]\s*[“”"]([^“”"]+?)[“”"]')
TAG_LEAK_RE = re.compile(r'[\[\]]|^[“”"]|[“”"]$')

def sanitize(text):
    text = re.sub(r'^\[[^\]]*\]\s*', '', text)
    text = text.strip().strip('“”"')
    return text.strip()

# ─── 한 장면 = 여러 대사/나레이션 조각(파트) 지원 ───────────────
# 예) [청허진인|경악]"…" [당무극|차가움]"…"  → 파트 2개(각기 다른 성우·감정)로 분해.
#     나레이션 문장과 대사가 섞여도(나레이션 [화자|감정]"대사") 순서대로 분해된다.
#     단일 대사/단일 나레이션이면 파트 1개 → 세그먼트 구조는 기존과 100% 동일(무협 불변).
def _split_parts(content):
    """장면 내용을 [나레이션/대사] 조각 리스트로 분해. 각 조각: {kind,speaker,emotion,text}."""
    parts, pos = [], 0
    for m in DIALOGUE_FIND.finditer(content):
        pre = content[pos:m.start()].strip()
        if pre:                                    # 대사 앞 일반 텍스트 → 나레이션 조각
            if TAG_LEAK_RE.search(pre):
                raise ValueError(f"나레이션에 태그/따옴표 잔여: {pre!r}")
            parts.append({"kind": "narration", "speaker": "나레이션", "emotion": None, "text": pre})
        a, b, text = m.groups()
        speaker, emotion = ("대사", a.strip()) if b is None else (a.strip(), (b or "").strip())
        text = text.strip().strip('“”"').strip()
        if TAG_LEAK_RE.search(text):
            raise ValueError(f"대사에 태그/따옴표 잔여: {text!r}")
        parts.append({"kind": "dialogue", "speaker": speaker, "emotion": emotion, "text": text})
        pos = m.end()
    tail = content[pos:].strip()
    if tail:                                        # 마지막 대사 뒤 일반 텍스트
        if TAG_LEAK_RE.search(tail):
            raise ValueError(f"나레이션에 태그/따옴표 발견: {tail!r}")
        parts.append({"kind": "narration", "speaker": "나레이션", "emotion": None, "text": tail})
    if not parts:                                   # 대사 없음 = 통째 나레이션
        parts.append({"kind": "narration", "speaker": "나레이션", "emotion": None, "text": content})
    return parts

def _make_segment(seq, lineno, content):
    """content(장면 한 줄/블록)를 세그먼트 dict로. 파트 1개면 기존 구조 그대로,
       2개 이상이면 parts[] 를 포함(다중 화자 장면). text는 합쳐진 전체(자막/매칭용)."""
    parts = _split_parts(content)
    if len(parts) == 1:
        p = parts[0]
        return {"seq": seq, "lineno": lineno, "kind": p["kind"],
                "speaker": p["speaker"], "emotion": p["emotion"], "text": p["text"]}
    combined = " ".join(p["text"] for p in parts)
    first_dlg = next((p for p in parts if p["kind"] == "dialogue"), parts[0])
    kind = "dialogue" if any(p["kind"] == "dialogue" for p in parts) else "narration"
    return {"seq": seq, "lineno": lineno, "kind": kind,
            "speaker": first_dlg["speaker"], "emotion": first_dlg["emotion"],
            "text": combined, "parts": parts}

def iter_parts(seg):
    """세그먼트의 파트들을 순회(다중이면 각 파트, 단일이면 자기 자신). 리포트/미리보기용."""
    parts = seg.get("parts")
    if parts:
        for p in parts:
            yield {"seq": seg["seq"], "kind": p["kind"], "speaker": p.get("speaker"),
                   "emotion": p.get("emotion"), "text": p["text"]}
    else:
        yield seg

def parse_script(path):
    segments, seq = [], 0
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            seq += 1
            try:
                segments.append(_make_segment(f"{seq:03d}", lineno, line))
            except ValueError as ex:
                raise ValueError(f"[{lineno}행] {ex}")
    return segments

# ─── 1-b. 시각화 프롬프트 양식 파싱 ──────────────────
# 3줄 구조:
#   [장면 N]
#   [한국어 번역] <TTS 대상: 대사 [화자|감정]"..." 또는 나레이션 문장>
#   [영어 이미지 프롬프트] <TTS 무관 — 완전 무시>
# [장면 N]의 N → 3자리 정규화(장면1→001), 영상 파일번호와 매칭.
SCENE_RE = re.compile(r'^\[\s*장면\s*(\d+)\s*\]')
KOR_RE   = re.compile(r'^\[\s*한국어\s*(?:번역|원문)\s*\]\s*(.*)$')   # 번역·원문 둘 다
ENG_RE   = re.compile(r'^\[\s*영어\s*이미지\s*프롬프트\s*\]')
MARK_RE  = re.compile(r'^\[\s*(?:CTA|반전|클라이맥스|climax)\s*\]\s*$', re.IGNORECASE)  # TTS 무관 마커

def _is_eng_line(line):
    """영어 이미지 프롬프트(마커/이어지는 순수영어 줄)인지. TTS 대상 아님 → 무시."""
    if ENG_RE.match(line):
        return True
    hangul = len(re.findall(r'[가-힣]', line))
    latin  = len(re.findall(r'[A-Za-z]', line))
    if latin >= 15 and latin > hangul * 2:     # 영어 위주 줄([당무극] 등 한글 조금 섞여도)
        return True
    if hangul == 0 and latin >= 3:             # 순수 영어 짧은 조각
        return True
    return False

def parse_visual_script(path):
    """[장면 N] 블록 단위로 한국어 줄을 모아 한 장면으로 만든다.
       ★ 한 장면의 [한국어 번역]이 여러 줄로 줄바꿈돼 있어도 전부 합침(뒷줄 씹힘 방지)."""
    segments = []
    cur_scene, cur_lineno, buf = None, None, []

    def flush():
        nonlocal buf
        if cur_scene is None or not buf:
            buf = []
            return
        content = " ".join(buf).strip()
        buf = []
        if not content:
            return
        seq = f"{cur_scene:03d}"
        try:
            segments.append(_make_segment(seq, cur_lineno, content))
        except ValueError as ex:
            raise ValueError(f"[장면 {seq}] {ex}")

    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            ms = SCENE_RE.match(line)
            if ms:                             # 새 장면 → 이전 장면 flush
                flush()
                cur_scene, cur_lineno = int(ms.group(1)), lineno
                continue
            if MARK_RE.match(line):            # [CTA]/[반전] 마커 → TTS 무관, 무시
                continue
            if _is_eng_line(line):             # 영어 프롬프트(여러 줄 포함) 무시
                continue
            mk = KOR_RE.match(line)
            content = mk.group(1).strip() if mk else line   # 마커 있으면 벗기고, 없으면(이어지는 줄) 그대로
            if not content:
                continue
            if cur_scene is None:
                raise ValueError(f"[{lineno}행] [장면 N] 없이 내용이 나왔습니다: {line!r}")
            buf.append(content)                # 같은 장면에 계속 누적
    flush()
    return segments

# ─── 1-c. 형식 자동 감지 → 알맞은 파서 선택 ──────────
def detect_format(path):
    """파일 내용으로 형식 판별: 'visual'(시각화 양식) | 'plain'(순수 대본)."""
    has_scene = has_kor = False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if SCENE_RE.match(s):
                has_scene = True
            elif KOR_RE.match(s):
                has_kor = True
            if has_scene and has_kor:
                break
    return "visual" if (has_scene and has_kor) else "plain"

def load_segments(path):
    """형식 자동 감지 후 parse_visual_script / parse_script 자동 선택."""
    if detect_format(path) == "visual":
        print("[형식감지] [장면]/[한국어 번역] 패턴 발견 → 시각화 양식으로 파싱")
        return parse_visual_script(path)
    print("[형식감지] [장면]/[한국어 번역] 패턴 없음 → 순수 대본으로 파싱")
    return parse_script(path)

# ─── Supertone 429(Too Many Requests) 자동 재시도 ──────
#   빠른 연속 호출 시 속도 제한에 걸림. SDK가 429 응답 파싱에 실패해 예외로 터지므로
#   메시지에 'Too Many Requests'/'429'/'rate'가 있으면 백오프 후 재시도.
_ST_MIN_INTERVAL = 0.7      # Supertone 연속 호출 최소 간격(초) — 몰아치기 → 속도제한 예방
_st_last = [0.0]

def _st_create_speech(kw, retries=10):
    delay = 5
    for attempt in range(retries):
        wait = _ST_MIN_INTERVAL - (time.time() - _st_last[0])   # 앞 호출과 간격 유지
        if wait > 0:
            time.sleep(wait)
        try:
            r = ST.text_to_speech.create_speech(**kw)
            _st_last[0] = time.time()
            return r
        except Exception as ex:
            _st_last[0] = time.time()
            msg = str(ex)
            # 크레딧/결제 부족은 재시도 무의미 → 명확한 안내로 즉시 중단(충전 후 재실행하면 이어서 생성)
            low = msg.lower()
            if any(t in low for t in ("payment", "402", "insufficient", "quota exceeded",
                                      "credit", "balance", "not enough", "billing")):
                raise RuntimeError(
                    "💳 Supertone 크레딧(결제) 부족으로 중단됐습니다. 충전 후 ②를 다시 누르면 "
                    "만든 것은 건너뛰고 남은 장면부터 이어서 생성됩니다. (원본 오류: " + msg[:100] + ")")
            rate = ("Too Many Requests" in msg or "429" in msg or "rate limit" in msg.lower())
            transient = rate or any(t in msg.lower() for t in
                                    ("timeout", "timed out", "connection", "temporarily",
                                     "502", "503", "504", "reset", "eof"))
            if transient and attempt < retries - 1:
                tag = "속도제한 429" if rate else "일시적 오류"
                print(f"    [{tag}] {delay}s 대기 후 재시도 ({attempt+1}/{retries}): {msg[:60]}", flush=True)
                time.sleep(delay)
                delay = min(int(delay * 1.8), 90)   # 5→9→16→…→90
                continue
            raise

# ─── 엔진 선택 (Supertone 2026-08-31 종료 대비) ──────────────────
#   대사/나레이션을 각각 어느 엔진으로 만들지 런타임에 지정. 앱 UI에서 설정.
#   기본: 대사=Supertone(8월 말까지 그대로), 나레이션=ElevenLabs(기존과 동일).
ENGINE_ST = "supertone"
ENGINE_EL = "elevenlabs"
ENGINE_TC = "typecast"        # 2026-09 대사 기본(캐릭터 연기·smart 감정)
ENGINE_GM = "gemini"          # 2026-10 추가(감정을 한국어 연기지시로 전달)
DIALOGUE_ENGINE  = ENGINE_TC   # 2026-09 대사 기본 = 타입캐스트(캐릭터 연기·smart 감정)
NARRATION_ENGINE = ENGINE_EL

_SCENE_CTX = {}      # seq → (앞 장면 텍스트, 뒤 장면 텍스트). 타입캐스트 smart 감정에 사용

def build_scene_context(segs):
    """대본 순서대로 앞/뒤 장면 텍스트를 모아둔다(감정 자동 판단용)."""
    _SCENE_CTX.clear()
    for i, s in enumerate(segs):
        prev = segs[i-1]["text"] if i > 0 else ""
        nxt  = segs[i+1]["text"] if i < len(segs)-1 else ""
        _SCENE_CTX[s["seq"]] = (prev, nxt)
    return _SCENE_CTX

def set_engines(dialogue=None, narration=None):
    """앱에서 엔진 선택 반영. 값이 바뀌면 scene_sig가 달라져 해당 장면만 재생성된다."""
    global DIALOGUE_ENGINE, NARRATION_ENGINE
    if dialogue:  DIALOGUE_ENGINE = dialogue
    if narration: NARRATION_ENGINE = narration
    return DIALOGUE_ENGINE, NARRATION_ENGINE

# ElevenLabs 대사용: 감정 style → v3 오디오 태그(대사 앞에 붙여 감정 연출)
EL_DIALOGUE_MODEL = "eleven_v3"        # 오디오 태그 지원 · 한국어 지원 확인됨
EL_EMOTION_TAGS = {
    "angry":       "[angry]",
    "annoyed":     "[annoyed]",
    "disgusted":   "[disgusted]",
    "evil":        "[sinister]",
    "teasing":     "[sarcastic]",
    "suspicious":  "[suspicious]",
    "dominating":  "[commanding]",
    "command":     "[commanding]",
    "firm":        "[firm]",
    "courageous":  "[confident]",
    "confident":   "[confident]",
    "happy":       "[happy]",
    "excited":     "[excited]",
    "admiring":    "[admiring]",
    "friendly":    "[warmly]",
    "relieved":    "[relieved]",
    "serene":      "[calm]",
    "sad":         "[sad]",
    "scared":      "[scared]",
    "anxious":     "[nervous]",
    "embarrassed": "[embarrassed]",
    "surprised":   "[surprised]",
    "neutral":     "",                  # 태그 없음(담담)
}

def el_voice_for(role):
    """역할 → ElevenLabs voice_id. voice_map.json의 el_voice_id, 없으면 나레이션 목소리."""
    cfg = VOICE_MAP.get(role) or {}
    return cfg.get("el_voice_id") or VOICE_MAP.get("나레이션", {}).get("voice_id")

def el_dialogue_text(style, text):
    """감정 style을 v3 오디오 태그로 바꿔 대사 앞에 붙임."""
    tag = EL_EMOTION_TAGS.get(style, "")
    return f"{tag} {text}".strip() if tag else text

# ─── ElevenLabs(나레이션) 재시도 + 크레딧 안내 + 빈 파일 방지 ──────
#   Supertone과 달리 그동안 재시도·검사가 없어, 일시 오류면 즉시 중단되고
#   빈 응답이 와도 0바이트 mp3가 조용히 생겨 무음 장면이 됐다(002.mp3 사례).
# ─── 발음 교정 레이어(조사 씹힘 대응) ───────────────
#   ElevenLabs 한국어가 조사 앞 음절을 삼키는 문제("태강에게"→"태강게") 때문에,
#   엔진에 보내는 텍스트만 발음교정.json 규칙으로 손본다. 자막/서명 원본은 그대로.
try:
    import 발음교정 as _PRON
except Exception as _ex:                      # 모듈이 없어도 파이프라인은 절대 안 깨진다
    _PRON = None
    print(f"  [발음교정] 레이어 비활성(모듈 없음): {type(_ex).__name__}")

def _pron(text, kind="narration"):
    """엔진에 보낼 텍스트로 변환. 규칙이 걸리면 무엇이 걸렸는지 로그로 남긴다."""
    if _PRON is None or not text:
        return text
    try:
        fixed, applied = _PRON.fix(text, kind)
        if applied:
            print(f"        [발음교정] {' / '.join(applied)}")
        return fixed
    except Exception as ex:
        print(f"  [발음교정] 적용 실패 → 원문 사용: {type(ex).__name__}: {ex}")
        return text

def _pron_tag(text, kind="narration"):
    """서명 꼬리표. 규칙이 텍스트를 바꿨을 때만 값이 생긴다(안 걸린 장면은 재생성 0)."""
    if _PRON is None or not text:
        return ""
    try:
        return _PRON.tag(text, kind)
    except Exception:
        return ""

def _el_convert(kwargs, out, retries=6):
    delay = 5
    for attempt in range(retries):
        try:
            audio = EL.text_to_speech.convert(**kwargs)
            tmp = out + ".part"
            with open(tmp, "wb") as f:
                for c in audio:
                    f.write(c)
            if os.path.getsize(tmp) == 0:                 # 빈 응답 → 무음 장면 방지
                os.remove(tmp)
                raise RuntimeError("ElevenLabs가 빈 오디오를 반환했습니다(0바이트).")
            os.replace(tmp, out)
            return out
        except Exception as ex:
            msg = str(ex); low = msg.lower()
            if any(t in low for t in ("quota", "credit", "payment", "402", "insufficient",
                                      "exceeded", "balance", "billing", "unusual activity")):
                raise RuntimeError(
                    "💳 ElevenLabs 크레딧(문자 한도) 부족으로 중단됐습니다. 충전/갱신 후 ②를 다시 누르면 "
                    "만든 것은 건너뛰고 남은 장면부터 이어서 생성됩니다. (원본 오류: " + msg[:150] + ")")
            transient = any(t in low for t in ("timeout", "timed out", "connection", "temporarily",
                                               "429", "500", "502", "503", "504", "reset", "eof",
                                               "빈 오디오"))
            if transient and attempt < retries - 1:
                print(f"    [나레이션 일시오류] {delay}s 후 재시도 ({attempt+1}/{retries}): {msg[:70]}", flush=True)
                time.sleep(delay); delay = min(int(delay * 1.8), 60)
                continue
            raise

# ─── 2. 세그먼트 하나 → mp3 ──────────────────────────
def _synth_one(seq, kind, speaker, emotion, text, out):
    """한 조각(나레이션 또는 대사)을 out 경로에 생성. (다중 화자 장면은 이걸 파트별로 호출)"""
    if kind == "narration":
        cfg = VOICE_MAP.get("나레이션")
        if not cfg:
            raise ValueError("voice_map.json에 '나레이션' 항목이 없습니다.")
        st = cfg.get("settings", {})                       # 나레이션 세팅(voice_map에서 조정)
        model_id = st.get("model_id", "eleven_flash_v2_5")
        language_code = st.get("language_code")
        voice_settings = VoiceSettings(                    # speed 포함 5개는 여기로
            stability=st.get("stability"),
            similarity_boost=st.get("similarity_boost"),
            style=st.get("style"),
            use_speaker_boost=st.get("use_speaker_boost"),
            speed=st.get("speed"))
        print(f"  {seq} | 화자=나레이션 → 역할=나레이션 → voice={cfg['voice_id']} "
              f"| 감정=- → style=- | 엔진=ElevenLabs → {os.path.basename(out)}")
        print(f"        model_id={model_id} | language_code={language_code} | "
              f"voice_settings={voice_settings.model_dump()}")
        kwargs = dict(text=_pron(text, "narration"), voice_id=cfg["voice_id"],
                      model_id=model_id, output_format="mp3_44100_128",
                      voice_settings=voice_settings)
        if language_code:                                  # model_id/language_code는 convert 별도 인자
            kwargs["language_code"] = language_code
        _el_convert(kwargs, out)                           # 재시도·크레딧안내·빈파일 검사 포함
    elif DIALOGUE_ENGINE == ENGINE_GM:
        # ── 대사를 Gemini 로 (감정 = 한국어 연기 지시문) ──
        import gemini_engine as gm
        role = resolve_role(speaker)
        style = resolve_style(role, emotion)          # 기존 감정 매핑 그대로 재사용
        vid = gm.voice_for(role, VOICE_MAP)
        if not vid:
            raise ValueError(
                f"역할 '{role}'의 Gemini voice(gm_voice_id)가 voice_map.json 에 없습니다. "
                f"제미나이보이스.py 로 오디션 후 채워 넣으세요.")
        # 대본의 원래 감정어를 그대로 지시문으로 — resolve_style 폴백은 보조로만 쓴다
        act = gm.style_for_emotion(emotion, style)
        print(f"  {seq} | 화자={speaker} → 역할={role} → voice={vid} "
              f"| 감정={emotion} → 지시='{act}' "
              f"| 엔진=Gemini · {gm.MODEL} → {os.path.basename(out)}")
        gm.synth(out, _pron(text, "dialogue"), vid, style=act, raw_style=True)
    elif DIALOGUE_ENGINE == ENGINE_TC:
        # ── 대사를 타입캐스트로 (감정은 smart 자동 — 앞뒤 장면 문맥으로 판단) ──
        import typecast_engine as tc
        role = resolve_role(speaker)
        vid = tc.voice_for(role, VOICE_MAP)
        if not vid:
            raise ValueError(f"역할 '{role}'의 타입캐스트 voice_id(tc_voice_id)가 없습니다.")
        base = str(seq).split(".")[0]
        prev_t, next_t = _SCENE_CTX.get(base, ("", ""))
        print(f"  {seq} | 화자={speaker} → 역할={role} → voice={vid} "
              f"| 감정=smart(문맥 자동) | 엔진=Typecast · {tc.MODEL} → {os.path.basename(out)}")
        tc.synth(out, text, vid, prev_text=prev_t, next_text=next_t, use_smart=True)
    elif DIALOGUE_ENGINE == ENGINE_EL:
        # ── 대사를 ElevenLabs로 (Supertone 종료 대비) : 감정은 v3 오디오 태그로 ──
        role = resolve_role(speaker)
        style = resolve_style(role, emotion)              # 기존 감정 매핑 그대로 재사용
        vid = el_voice_for(role)
        if not vid:
            raise ValueError(f"역할 '{role}'의 ElevenLabs voice_id(el_voice_id)가 없습니다.")
        st = (VOICE_MAP.get(role, {}).get("el_settings")
              or VOICE_MAP.get("나레이션", {}).get("settings", {}))
        body = el_dialogue_text(style, _pron(text, "dialogue"))
        print(f"  {seq} | 화자={speaker} → 역할={role} → voice={vid} "
              f"| 감정={emotion} → style={style} → 태그={EL_EMOTION_TAGS.get(style,'(없음)')} "
              f"| 엔진=ElevenLabs · 모델={EL_DIALOGUE_MODEL} → {os.path.basename(out)}")
        kwargs = dict(text=body, voice_id=vid, model_id=EL_DIALOGUE_MODEL,
                      output_format="mp3_44100_128",
                      voice_settings=VoiceSettings(
                          stability=st.get("stability"),
                          similarity_boost=st.get("similarity_boost"),
                          style=st.get("style"),
                          use_speaker_boost=st.get("use_speaker_boost"),
                          speed=st.get("speed")))
        _el_convert(kwargs, out)
    else:
        role = resolve_role(speaker)                     # 화자이름 → 역할
        cfg = VOICE_MAP.get(role)                         # 역할 → voice_id
        if not cfg:
            raise ValueError(f"voice_map.json에 역할 '{role}' 매핑이 없습니다.")
        style = resolve_style(role, emotion)              # 역할 기준 감정 매핑(+폴백)
        # 역할별 음성 설정(voice_map.json settings): 속도(speed)·피치 등. 없으면 기본값.
        st_cfg = cfg.get("settings") or {}
        vs = {k: st_cfg[k] for k in
              ("speed", "pitch_shift", "pitch_variance", "similarity", "text_guidance")
              if k in st_cfg}
        print(f"  {seq} | 화자={speaker} → 역할={role} → voice={cfg['voice_id']} "
              f"| 감정={emotion} → style={style} | 엔진=Supertone · 모델={SUPERTONE_MODEL}"
              f"{(' · settings=' + str(vs)) if vs else ''} → {os.path.basename(out)}")
        kw = dict(voice_id=cfg["voice_id"], text=text,
                  language="ko", style=style, model=SUPERTONE_MODEL,   # 상단 상수(Sona 2)
                  output_format="mp3")                     # WAV 기본값 → mp3 명시
        if vs:
            kw["voice_settings"] = vs                      # speed 1.05 등 적용
        resp = _st_create_speech(kw)                       # 429 자동 재시도 포함
        res = resp.result
        if hasattr(res, "audio_base64"):   # include_phonemes=True JSON 응답
            import base64
            data = base64.b64decode(res.audio_base64)
        elif hasattr(res, "read"):         # httpx.Response(스트리밍) / MockResponse → read() 먼저
            data = res.read()
            if not data and hasattr(res, "content"):
                data = res.content
        elif hasattr(res, "content"):
            data = res.content
        else:
            data = bytes(res)
        if not data:                                   # 빈 응답 → 무음 장면 방지(재시도는 상위에서)
            raise RuntimeError(f"Supertone이 빈 오디오를 반환했습니다(장면 {seq}).")
        with open(out, "wb") as f:
            f.write(data)
    return out

def _concat_audio(files, out, gap=0.3):
    """여러 mp3 조각을 하나로 이어붙임(화자 전환 사이 gap초 자연스러운 정적).
       두 엔진(ElevenLabs 44.1k / Supertone) 샘플레이트가 달라도 concat 필터로 리샘플."""
    if len(files) == 1:
        shutil.copy(files[0], out); return out
    inputs, chains, labels = [], [], []
    for i, f in enumerate(files):
        inputs += ["-i", f]
        lbl = f"a{i}"
        chains.append(f"[{i}:a]apad=pad_dur={gap}[{lbl}]" if i < len(files) - 1
                      else f"[{i}:a]anull[{lbl}]")        # 마지막 조각엔 꼬리 정적 안 붙임
        labels.append(f"[{lbl}]")
    filt = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(files)}:v=0:a=1[out]"
    subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", filt,
                    "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2", out],
                   check=True, capture_output=True)
    return out

def synth(seg):
    """세그먼트 → mp3. 다중 화자 장면(parts)이면 파트별 생성 후 순서대로 이어붙임."""
    out = os.path.join(OUT_AUDIO, f"{seg['seq']}.mp3")
    parts = seg.get("parts")
    if parts:                                             # 한 장면에 대사/나레이션 여러 개
        print(f"  {seg['seq']} | 다중 화자 장면 — 파트 {len(parts)}개 순서대로 생성 후 이어붙임")
        pieces = []
        for j, p in enumerate(parts):
            pout = f"{out}.p{j}.mp3"
            _synth_one(f"{seg['seq']}.{j+1}", p["kind"], p.get("speaker"), p.get("emotion"),
                       p["text"], pout)
            pieces.append(pout)
        _concat_audio(pieces, out)
        for pf in pieces:
            try: os.remove(pf)
            except OSError: pass
        return out
    return _synth_one(seg["seq"], seg["kind"], seg.get("speaker"), seg.get("emotion"),
                      seg["text"], out)

# ─── 3. 볼륨 정규화 (두 엔진 음량차 보정) ────────────
import shutil
def normalize(path):
    if shutil.which("ffmpeg") is None:
        print("  [경고] ffmpeg 미설치 → 볼륨 정규화 건너뜀 (원본 오디오 유지)")
        return
    # 빈/깨진 mp3면 loudnorm이 터져 전체 실행이 중단됐다 → 원인을 명확히 알리고 그 파일 제거
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        try: os.remove(path)
        except OSError: pass
        raise RuntimeError(f"빈 오디오 파일 생성됨({os.path.basename(path)}) — TTS 응답이 비었습니다. "
                           f"②를 다시 누르면 이 장면부터 재시도합니다.")
    tmp = path + ".norm.mp3"
    r = subprocess.run(["ffmpeg", "-y", "-i", path, "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11", tmp], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        # 정규화 실패해도 원본 오디오는 살아있으니 계속 진행(음량만 미보정)
        print(f"  [경고] 볼륨 정규화 실패 → 원본 유지: {os.path.basename(path)} "
              f"({(r.stderr or '').strip().splitlines()[-1][:80] if r.stderr else ''})")
        try: os.remove(tmp)
        except OSError: pass
        return
    os.replace(tmp, path)

# ─── 4-0. 드라이런 (API 호출 없이 파싱+역할+style 매핑 리포트만) ──
def dry_run(script_path):
    segs = load_segments(script_path)
    flat = [ps for s in segs for ps in iter_parts(s)]     # 다중 화자 장면은 파트별로 펼침
    n_narr = sum(1 for s in flat if s["kind"] == "narration")
    dials = [s for s in flat if s["kind"] == "dialogue"]
    print("=" * 72)
    print(f"[1] 전체 세그먼트 {len(segs)}줄  →  나레이션 {n_narr}줄 / 대사 {len(dials)}줄")

    rows, uncast = [], {}
    kc = {"정확매칭": 0, "근사폴백": 0, "neutral최후": 0}
    fb_emotion, neutral_scenes = {}, []
    for s in dials:
        speaker, emotion = s["speaker"], s["emotion"]
        role = role_of(speaker)
        if speaker not in CHARACTER_CAST:
            uncast[speaker] = uncast.get(speaker, 0) + 1
        style, kind = classify(role, emotion)
        kc[kind] = kc.get(kind, 0) + 1
        if kind != "정확매칭":
            fb_emotion[emotion] = fb_emotion.get(emotion, 0) + 1
        if kind == "neutral최후":
            neutral_scenes.append(s["seq"])
        rows.append((s["seq"], speaker, role, emotion, style, kind))

    # [2] 감정 매핑 표 (폴백 종류 구분)
    print("=" * 72)
    print(f"[2] 대사 감정 매핑  (정확 {kc['정확매칭']} / 근사 {kc['근사폴백']} / neutral최후 {kc['neutral최후']})")
    print(f"    {'장면':>4} | {'화자':<8} | {'역할':<5} | {'감정':<6} | {'style':<10} | 종류")
    _mark = {"정확매칭": "✅ 정확", "근사폴백": "≈ 근사(계열유지)", "neutral최후": "⚠ neutral최후"}
    for seq, sp, role, emo, st, kind in rows:
        print(f"    {seq:>4} | {sp:<8} | {role:<5} | {emo:<6} | {st:<10} | {_mark[kind]}")

    # [3] 폴백 감정 카운트(정확매칭 제외)
    print("=" * 72)
    print("[3] 폴백 감정어 카운트 (근사+최후)")
    if fb_emotion:
        for emo, c in sorted(fb_emotion.items(), key=lambda x: -x[1]):
            print(f"    {emo} : {c}회")
    else:
        print("    (없음 — 전부 정확매칭)")

    # neutral 최후폴백 장면 목록 (밋밋하게 나갈 대사)
    print("-" * 72)
    print(f"[⚠ neutral 최후폴백 장면] {len(neutral_scenes)}건 — 밋밋하게 나갈 대사")
    print("    " + (", ".join(neutral_scenes) if neutral_scenes else "(없음)"))

    # [4] character_cast에 없는 화자
    print("=" * 72)
    print(f"[4] character_cast.json에 없는 화자 (기본 역할 '{DEFAULT_DIALOGUE_ROLE}')")
    if uncast:
        for sp, c in sorted(uncast.items(), key=lambda x: -x[1]):
            note = "  ← [감정]만 있고 화자 생략된 줄" if sp == "대사" else ""
            print(f"    {sp} : {c}회{note}")
    else:
        print("    (없음 — 모든 화자가 cast에 존재)")
    print("=" * 72)
    print("드라이런 완료: 실제 오디오/ API 호출 없음. 생성하려면 'tts' 명령을 사용하세요.")

# ── 오디오 매니페스트: seq → '그 mp3가 만들어진 텍스트'. 텍스트 바뀐 장면만 재생성. ──
_MANIFEST = ".audio_texts.json"

def _load_manifest():
    p = os.path.join(OUT_AUDIO, _MANIFEST)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_manifest(m):
    os.makedirs(OUT_AUDIO, exist_ok=True)
    json.dump(m, open(os.path.join(OUT_AUDIO, _MANIFEST), "w", encoding="utf-8"), ensure_ascii=False)

def _part_sig(kind, speaker, emotion, text):
    """한 조각의 오디오 결정 서명(텍스트+엔진+역할/성우+감정+속도). 단일 장면과 동일 포맷."""
    if kind == "narration":
        cfg = VOICE_MAP.get("나레이션", {}); st = cfg.get("settings", {})
        # 기존 형식 유지(재생성 방지) + 발음교정이 실제로 걸린 장면만 꼬리표가 붙는다
        return (f"N|{text}|{cfg.get('voice_id')}|{st.get('speed')}"
                + _pron_tag(text, "narration"))
    role = role_of(speaker)
    cfg = VOICE_MAP.get(role, {})
    if DIALOGUE_ENGINE == ENGINE_GM:
        import gemini_engine as gm
        return (f"D|{text}|GM|{role}|{gm.voice_for(role, VOICE_MAP)}|{emotion}"
                + _pron_tag(text, "dialogue"))
    if DIALOGUE_ENGINE == ENGINE_TC:      # 엔진이 다르면 서명도 달라짐 → 그 장면만 재생성
        import typecast_engine as tc
        return f"D|{text}|TC|{role}|{tc.voice_for(role, VOICE_MAP)}"   # 감정은 smart(문맥)라 미포함
    if DIALOGUE_ENGINE == ENGINE_EL:
        return (f"D|{text}|EL|{role}|{el_voice_for(role)}|{emotion}"
                + _pron_tag(text, "dialogue"))
    speed = (cfg.get("settings") or {}).get("speed")
    return f"D|{text}|{role}|{cfg.get('voice_id')}|{emotion}|{speed}"

def scene_sig(seg):
    """오디오를 결정하는 요소의 서명 = 텍스트+역할(성우)+감정+속도.
       이게 바뀌면(역할/감정/속도 변경 포함) 재생성 대상. (스타일 캐시 비의존 → 결정적)
       다중 화자 장면은 모든 파트의 서명을 이어붙임(파트 하나만 바뀌어도 재생성)."""
    parts = seg.get("parts")
    if parts:
        return "M|" + "||".join(_part_sig(p["kind"], p.get("speaker"), p.get("emotion"), p["text"])
                                for p in parts)
    return _part_sig(seg["kind"], seg.get("speaker"), seg.get("emotion"), seg["text"])

def _needs_gen(seg, manifest):
    """mp3가 없거나(비었거나) 서명(텍스트·역할·감정·속도)이 바뀌면 True(재생성 필요)."""
    seq = seg["seq"]
    p = os.path.join(OUT_AUDIO, f"{seq}.mp3")
    return not (os.path.exists(p) and os.path.getsize(p) > 0 and manifest.get(seq) == scene_sig(seg))

def tts_plan(script_path):
    """run_tts가 실제로 할 일을 생성 없이 미리 계산(UI 표시용).
       → {total, existing(건너뜀), to_generate(생성/재생성), resume}."""
    segs = load_segments(script_path)
    manifest = _load_manifest()
    to_gen = sum(1 for s in segs if _needs_gen(s, manifest))
    return {"total": len(segs), "existing": len(segs) - to_gen,
            "to_generate": to_gen, "resume": to_gen < len(segs)}

# ─── 4. TTS 전체 실행 ───────────────────────────────
def run_tts(script_path, resume=True):
    """resume=True면 이미 만들어진 mp3(비어있지 않은)는 건너뛴다 → 크레딧 절약·이어하기.
       (429 등으로 중단됐을 때 재실행하면 남은 것만 생성)"""
    segs = load_segments(script_path)
    n_narr = sum(1 for s in segs if s["kind"] == "narration")
    n_dial = len(segs) - n_narr
    os.makedirs(OUT_AUDIO, exist_ok=True)
    manifest = _load_manifest()          # seq → 그 mp3가 만들어진 텍스트
    build_scene_context(segs)            # 타입캐스트 smart 감정용 앞뒤 문맥
    cur_seqs = {s["seq"] for s in segs}
    # 현재 대본 범위 밖 오디오(다른 화 잔여물) 정리 → 파일 안 섞임
    stale = [f for f in os.listdir(OUT_AUDIO) if f.endswith(".mp3") and f[:-4] not in cur_seqs]
    for f in stale:
        os.remove(os.path.join(OUT_AUDIO, f)); manifest.pop(f[:-4], None)
    if stale:
        print(f"  [범위밖 오디오 {len(stale)}개 정리]")
    print(f"세그먼트 {len(segs)}개 (나레이션 {n_narr} / 대사 {n_dial}) · 이어하기={resume}")
    # 사용될 역할의 실제 지원 style 1회 조회·캐시(안전 체크 → 400/오폴백 방지)
    used_roles = {role_of(s["speaker"])
                  for s in segs if s["kind"] == "dialogue"}
    preload_voice_styles(used_roles)
    n_skip = 0
    for s in segs:
        # mp3가 있고 서명(텍스트·역할·감정·속도)이 같으면 건너뜀(바뀐 장면만 재생성)
        if resume and not _needs_gen(s, manifest):
            n_skip += 1
            print(f"  {s['seq']}.mp3  (변경 없음 — 건너뜀)")
            continue
        p = synth(s)
        normalize(p)
        manifest[s["seq"]] = scene_sig(s)   # 이 서명으로 만들었다고 기록
        _save_manifest(manifest)         # 매 장면 저장(429 등 중단돼도 진행 보존)
        tag = s["speaker"] if s["kind"] == "dialogue" else "나레이션"
        print(f"  {s['seq']}.mp3  [{tag}] {s['text'][:24]}…")
    _save_manifest(manifest)
    if n_skip:
        print(f"  ({n_skip}개 건너뜀 — 변경 없음)")
    # 화자 목록 요약(성우 매핑 누락 점검용)
    speakers = sorted({s["speaker"] for s in segs if s["kind"] == "dialogue"})
    print("등장 화자:", ", ".join(speakers))
    print("완료 →", OUT_AUDIO)

# ─── 5. 영상 매칭 + 자막 ─────────────────────────────
def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", p],
        capture_output=True, text=True)
    return float(r.stdout.strip())

# ── 길이맞춤: 오디오(TTS) 길이 A에 영상을 맞춘다 ───────
#   영상이 짧으면 → 전체를 균일하게 느리게(setpts, k=A/V). freeze/pad 안 씀(중간 정지 없음).
#   영상이 길면   → 뒷부분 trim(A초에서 자름).
#   거의 같으면   → 그대로.
_EQ_TOL = 0.05   # |V-A| 가 이 이내면 그대로(copy)

def _vfps(path, default=24.0):
    """소스 영상의 프레임레이트."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate", "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        n, d = r.stdout.strip().split("/")
        f = float(n) / float(d)
        return f if f > 0 else default
    except Exception:
        return default

def fit_video_to_audio(video, audio, out):
    """오디오 길이에 영상을 맞춘다. 짧으면 slow(전체 속도조절), 길면 trim.
       ★ 이어붙일 때 밀리지 않도록 클립 길이를 '프레임 격자'(A′)로 올려
         영상·오디오를 정확히 같은 길이로 만든다(오디오 끝 수십 ms 무음 apad)."""
    A, V = dur(audio), dur(video)
    if A <= 0 or V <= 0:                       # 무음/빈 영상 등 예외 → crash 없이 로그
        print(f"  [경고] 길이 0 감지(A={A:.3f}, V={V:.3f}) → 단순 mux")
        subprocess.run(["ffmpeg", "-y", "-i", video, "-i", audio, "-map", "0:v", "-map", "1:a",
                        "-c:v", "libx264", "-c:a", "aac", "-shortest", out],
                       check=True, capture_output=True)
        return {"A": A, "V": V, "mode": "mux", "factor": None, "out_dur": dur(out)}

    fps = _vfps(video)
    A2 = math.ceil(A * fps) / fps              # 프레임 격자로 올림(음성 안 잘림, +1프레임 이하)
    if V < A - _EQ_TOL:                        # 영상이 짧음 → 전체 느리게(중간 정지 없음)
        mode, k = "slow", A / V
        vf = f"[0:v]setpts={k:.6f}*PTS,tpad=stop_mode=clone:stop_duration=1[v]"
    elif V > A + _EQ_TOL:                       # 영상이 김 → 뒷부분 trim
        mode, k = "trim", None
        vf = "[0:v]tpad=stop_mode=clone:stop_duration=1[v]"
    else:                                       # 거의 같음
        mode, k = "copy", 1.0
        vf = "[0:v]tpad=stop_mode=clone:stop_duration=1[v]"
    subprocess.run(["ffmpeg", "-y", "-i", video, "-i", audio,
                    "-filter_complex", vf, "-map", "[v]", "-map", "1:a",
                    "-c:v", "libx264", "-c:a", "aac",
                    "-af", "apad",                        # 오디오 끝 무음 패딩 → A′까지
                    "-fps_mode", "cfr", "-r", f"{fps}",   # 영상 CFR
                    "-t", f"{A2:.6f}", out],              # 둘 다 정확히 A′에서 컷
                   check=True, capture_output=True)
    return {"A": A, "V": V, "mode": mode, "factor": k, "out_dur": dur(out)}

# ── 자막(.srt) 생성 ─────────────────────────────────
SRT_SPLIT     = True    # 긴 장면을 여러 cue로 분할
SRT_MAX_CHARS = 28      # 한 줄 최대(이 이내면 CapCut/유튜브에서 1줄로 나옴)
SRT_MIN_TAIL  = 8       # 꼬리 조각이 이보다 짧으면 재분배(“2자만 남기기” 방지)

def _fmt_ts(sec):
    if sec < 0:
        sec = 0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _split_sentences(text):
    # 문장부호(. ? ! …)와 닫는 따옴표까지 한 문장으로 유지
    parts = re.findall(r'[^.?!…]+[.?!…]*[”"]?', text)
    return [p.strip() for p in parts if p.strip()]

_BREAK_CHARS = (",", "·", "、", ";", ":")   # 쉼표류 우선 절단

def _best_cut(s, maxlen):
    """maxlen 이내 최적 절단 위치 반환(그 위치까지 앞 조각).
       우선순위: 쉼표류 > 공백 > 강제. 너무 앞(절반 미만)이면 무시."""
    window = s[:maxlen]
    floor = maxlen // 2
    best = -1
    for ch in _BREAK_CHARS:                  # 쉼표가 있으면 거기서 끊음
        i = window.rfind(ch)
        if i > best:
            best = i
    if best >= floor:
        return best + 1                      # 쉼표 포함해서 자름
    i = window.rfind(" ")
    if i >= floor:
        return i + 1
    return maxlen                            # 공백 없는 긴 덩어리 → 강제

def _chunk_by_len(s, maxlen):
    """문장을 maxlen 이내 조각들로. 쉼표 우선, 짧은 꼬리 방지."""
    s = s.strip()
    if len(s) <= maxlen:
        return [s]
    out = []
    while len(s) > maxlen:
        c = _best_cut(s, maxlen)
        out.append(s[:c].strip())
        s = s[c:].strip()
    if s:
        out.append(s)
    # 꼬리가 너무 짧으면(예: 2자) 앞 조각과 합쳐 균등 재분배
    if len(out) >= 2 and len(out[-1]) < SRT_MIN_TAIL:
        merged = (out[-2] + " " + out[-1]).strip()
        if len(merged) <= maxlen:
            out[-2:] = [merged]
        else:
            c = _best_cut(merged, min(maxlen, len(merged) // 2 + 6))
            out[-2:] = [merged[:c].strip(), merged[c:].strip()]
    return [p for p in out if p]

# ── 자막 싱크 정렬 ──
SRT_ALIGN       = True      # True면 실제 발화 시각에 자막을 맞춤
SRT_ASR         = True      # True면 Whisper 단어 타임스탬프로 정확 정렬(최우선). 실패 시 무음 폴백
SIL_NOISE       = "-35dB"   # (폴백) 이 이하를 무음으로 간주
SIL_MIN         = 0.25      # (폴백) 이 이상 이어지면 '쉼'으로 인정(초)

# ── ASR 강제정렬: 대본 텍스트는 이미 알고 있으므로 '단어별 시각'만 활용 ──
WHISPER_MODEL   = "small"   # base(빠름/덜정확) < small < medium(느림/정확)
_ASR_MODEL      = None
_ASR_CACHE      = {}        # audio_path → [(start,end,word), ...] (같은 파일 재인식 방지)

def _get_asr():
    global _ASR_MODEL
    if _ASR_MODEL is None:
        from faster_whisper import WhisperModel
        _ASR_MODEL = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _ASR_MODEL

def asr_words(audio):
    """오디오 → [(start,end,word)]. 미설치/실패 시 []. 파일별 1회만 인식(캐시)."""
    if audio in _ASR_CACHE:
        return _ASR_CACHE[audio]
    words = []
    try:
        segs, _ = _get_asr().transcribe(audio, language="ko", word_timestamps=True)
        for s in segs:
            for w in (s.words or []):
                words.append((float(w.start), float(w.end), (w.word or "").strip()))
    except Exception as ex:
        print(f"  [ASR경고] {os.path.basename(audio)} 인식 실패 → 무음 기반 정렬로 폴백: {type(ex).__name__}: {ex}")
    _ASR_CACHE[audio] = words
    return words

def _align_pieces_asr(pieces, words, length):
    """조각별 '발화 시작 시각'을 인식 단어 타임스탬프에 글자수 비례로 스냅. 실패 시 None.
       (대본을 알고 있으니 인식 '텍스트'는 무시하고 '시각'만 쓴다 → 인식 오탈자에 강함)"""
    if not words:
        return None
    # ── ASR 신뢰성 체크: 인식이 튀면(앞부분 놓침·단어 태부족) 폴백(무음 기반) ──
    #    예) 첫 단어가 클립 중반 이후에 잡히면 앞 발화를 통째로 놓친 것 → 자막이 0초로 깨짐.
    span = words[-1][1] - words[0][0]
    if words[0][0] > max(1.5, length * 0.4):      # 첫 단어가 너무 늦게 시작
        return None
    if span < length * 0.35 and length > 2.0:     # 인식 단어가 클립 일부에만 몰림
        return None
    if len(words) < max(2, len(pieces)):          # 조각 수보다 인식 단어가 적음(대부분 놓침)
        return None
    wchars = [max(1, len(w[2])) for w in words]
    totw = sum(wchars)
    bounds = [max(0.0, words[0][0])]          # 첫 조각 = 첫 단어 시작(리드인 존중)
    tot = sum(len(p) for p in pieces) or 1
    acc = 0
    for p in pieces[:-1]:
        acc += len(p)
        target = (acc / tot) * totw           # 이 경계의 누적 글자 위치(인식 글자 기준)
        # 경계 '이후' 첫 단어의 시작을 집는다(단어 끝에 걸린 경계의 off-by-one 방지).
        c, idx = 0, len(words) - 1
        for k, wc in enumerate(wchars):
            if c >= target - 1e-6:            # 이 단어가 경계 시점/이후에 시작
                idx = k; break
            c += wc
        b = max(bounds[-1], words[idx][0])     # 단조 증가 보장
        bounds.append(min(b, length))
    return bounds

def detect_speech_chunks(audio, total):
    """오디오에서 실제 발화 덩어리 [(시작,끝), ...] 반환. 실패 시 [] """
    try:
        r = subprocess.run(["ffmpeg", "-i", audio, "-af",
                            f"silencedetect=noise={SIL_NOISE}:d={SIL_MIN}", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", r.stderr)]
        ends   = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", r.stderr)]
    except Exception:
        return []
    # 무음 구간을 뺀 나머지가 발화 구간
    gaps = []
    for i, s in enumerate(starts):
        e_ = ends[i] if i < len(ends) else total
        gaps.append((max(0.0, s), min(total, e_)))
    chunks, cur = [], 0.0
    for s, e_ in gaps:
        if s - cur > 0.05:
            chunks.append((cur, s))
        cur = e_
    if total - cur > 0.05:
        chunks.append((cur, total))
    return chunks

def _punct_pieces(text):
    """문장부호(쉼표·마침표 등) 단위 조각 — TTS가 실제로 쉬는 지점과 일치."""
    parts = re.findall(r'[^,.?!…·;:]+[,.?!…·;:]*[”"]?', text)
    return [p.strip() for p in parts if p.strip()]

def _merge_chunks(chunks, target):
    """발화 덩어리 수를 target까지 축소: 사이 쉼이 가장 짧은 곳부터 합침(진짜 경계가 아닐 가능성)."""
    ch = list(chunks)
    while len(ch) > target and len(ch) > 1:
        gaps = [(ch[i + 1][0] - ch[i][1], i) for i in range(len(ch) - 1)]
        _, i = min(gaps)
        ch[i:i + 2] = [(ch[i][0], ch[i + 1][1])]
    return ch

def _align_pieces_to_chunks(pieces, chunks, length):
    """조각(pieces) 각각의 '발화 시작 시각'을 반환. len(chunks) >= len(pieces) 전제.
       감정 대사는 문장부호와 무관한 극적 쉼이 많아 덩어리 수가 더 많다.
       → 글자수 비례로 각 경계의 '대략 위치'를 예측하고, 가장 가까운 실제 무음 경계(쉼)로 스냅.
         (글자수=텍스트 구조로 '어디쯤'인지, 실제 쉼으로 '정확한 시각'을 잡는 강제정렬 lite)"""
    starts = [c[0] for c in chunks]
    ends   = [c[1] for c in chunks]
    sp0, sp1 = starts[0], ends[-1]
    span = max(1e-6, sp1 - sp0)
    tot = sum(len(p) for p in pieces) or 1
    # 후보 경계 = 덩어리 사이 '쉼' 지점(다음 덩어리 시작 시각). 총 len(chunks)-1개.
    cand = list(range(1, len(chunks)))          # cand[k] → starts[cand] 가 경계 시각
    bounds = [sp0]                              # 첫 조각은 첫 발화에서 시작
    used = 0
    acc = 0
    for p in pieces[:-1]:
        acc += len(p)
        pred = sp0 + span * (acc / tot)         # 이 경계의 글자수 비례 예상 시각
        # 아직 안 쓴 후보 중 예상 시각에 가장 가까운 쉼으로 스냅(단조 증가 보장)
        pool = cand[used:]
        if not pool:                            # 남은 후보 없음 → 예상 시각 그대로
            bounds.append(pred); continue
        j = min(range(len(pool)), key=lambda k: abs(starts[pool[k]] - pred))
        used += j + 1
        bounds.append(starts[pool[j]])
    return bounds

def _merge_pieces(pieces, target):
    """자막 조각 수를 target까지 축소: 합쳐도 가장 짧은 인접 쌍부터 병합."""
    ps = list(pieces)
    while len(ps) > target and len(ps) > 1:
        lens = [(len(ps[i]) + len(ps[i + 1]), i) for i in range(len(ps) - 1)]
        _, i = min(lens)
        ps[i:i + 2] = [(ps[i] + " " + ps[i + 1]).strip()]
    return ps

def make_cues(text, start, length, audio=None):
    """장면 텍스트 → cue 리스트.
       audio가 주어지고 발화 덩어리 수와 문장부호 조각 수가 맞으면 '실제 발화 시각'에 정렬,
       아니면 글자수 비례로 배분."""
    if not SRT_SPLIT:
        return [(start, start + length, text)]

    # ① 실제 발화에 정렬
    if SRT_ALIGN and audio and os.path.exists(audio):
        pieces = _punct_pieces(text)
        # ①-a ASR 강제정렬(최우선): '최종 표시 단위' 전체를 단어 시각에 스냅
        if SRT_ASR and pieces:
            units = []                           # 28자 초과 조각까지 미리 쪼갠 최종 자막 단위
            for p in pieces:
                units += [p] if len(p) <= SRT_MAX_CHARS else _chunk_by_len(p, SRT_MAX_CHARS)
            bnds = _align_pieces_asr(units, asr_words(audio), length)
            if bnds:
                out = []                          # (cs, ce, text) 로컬 시각
                for i, u in enumerate(units):
                    cs = bnds[i]
                    ce = bnds[i + 1] if i + 1 < len(bnds) else length
                    # ASR이 인접 단위를 같은 시각으로 잡으면(영길이/겹침) 앞 자막에 병합
                    if out and (ce - cs < 0.04 or cs < out[-1][1] - 1e-6):
                        out[-1][1] = max(out[-1][1], ce)
                        out[-1][2] = (out[-1][2] + " " + u).strip()
                    else:
                        out.append([cs, max(cs, ce), u])
                return [(start + cs, start + ce, u) for cs, ce, u in out]
        # ①-b 폴백: 무음(쉼) 기반 스냅
        chunks = detect_speech_chunks(audio, length)
        if pieces and chunks:
            if len(pieces) > len(chunks):        # 쉼표에서 안 쉼 → 조각을 덩어리 수로 합침
                pieces = _merge_pieces(pieces, len(chunks))
            bnds = _align_pieces_to_chunks(pieces, chunks, length)
            out = []
            for i, p in enumerate(pieces):
                cs = bnds[i]
                seg_end = bnds[i + 1] if i + 1 < len(bnds) else length
                subs = [p] if len(p) <= SRT_MAX_CHARS else _chunk_by_len(p, SRT_MAX_CHARS)
                span = max(0.05, seg_end - cs)
                tot = sum(len(s) for s in subs) or 1
                t = cs
                for j, s in enumerate(subs):     # 한 조각이 28자 초과면 글자수 비례로 재분할
                    e_ = seg_end if j == len(subs) - 1 else t + span * (len(s) / tot)
                    out.append((start + t, start + e_, s))
                    t = e_
            return out

    # ② 폴백: 글자수 비례
    units = []
    for sent in _split_sentences(text):
        units += ([sent] if len(sent) <= SRT_MAX_CHARS
                  else _chunk_by_len(sent, SRT_MAX_CHARS))
    if not units:
        units = [text]
    total = sum(len(u) for u in units) or 1
    cues, t = [], start
    for i, u in enumerate(units):
        end = (start + length) if i == len(units) - 1 else t + length * (len(u) / total)
        cues.append((t, end, u))
        t = end
    return cues

def _read_srt(path):
    """SRT → [(start초, end초, text), ...]. 실패 시 []. (매칭 재사용 시 자막 복원용)"""
    try:
        blocks = open(path, encoding="utf-8").read().strip().split("\n\n")
    except Exception:
        return []
    out = []
    for blk in blocks:
        lines = [ln for ln in blk.splitlines() if ln.strip()]
        ti = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ti is None:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)", lines[ti])
        if not m:
            continue
        g = list(map(int, m.groups()))
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        txt = " ".join(lines[ti + 1:]).strip()
        if txt:
            out.append((s, e, txt))
    return out

def write_srt(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, (s, e, txt) in enumerate(cues, 1):
            f.write(f"{i}\n{_fmt_ts(s)} --> {_fmt_ts(e)}\n{txt}\n\n")

# ── 메인: 장면=대본줄, 1:1 매칭 + 자막 동시 생성 ────
def run_match(script_path, make_unified=True, make_per_scene=True):
    segs = load_segments(script_path)
    t = 0.0
    all_cues, report, total_audio, total_clip = [], [], 0.0, 0.0
    image_scenes = set()        # 모션을 구워 넣은 이미지 장면 → CapCut 프리셋 애니 제외
    _match_manifest = _load_manifest()   # seq → 그 mp3를 만든 서명(다른 화 오디오 혼입 차단용)
    stale_audio = []            # 지금 대본과 서명이 다른(예전 화) 오디오 장면
    for s in segs:
        N = s["seq"]
        audio = os.path.join(OUT_AUDIO, f"{N}.mp3")
        if not os.path.exists(audio):
            report.append((N, "SKIP(오디오없음)", None, None, None, None))
            continue
        # ★ 안전장치: 그 mp3가 '지금 대본'으로 만든 게 맞는지 확인(다른 화 오디오 혼입 방지).
        #    매니페스트에 기록이 있는데 서명이 다르면 = 예전 대본의 음성 → 쓰지 않는다.
        _rec = _match_manifest.get(N)
        if _rec is not None and _rec != scene_sig(s):
            stale_audio.append(N)
            report.append((N, "SKIP(다른 대본 오디오)", None, None, None, None))
            continue
        A = dur(audio); total_audio += A
        clip_len = A                       # 자막 타임라인 기준 = 실제 클립 길이
        video = find_scene_media(N)            # 영상 또는 이미지(둘 다 허용)
        out = os.path.join(OUT_MATCH, f"{N}_matched.mp4")
        srt = os.path.join(OUT_SRT, f"{N}.srt")
        # ── 이미 이 오디오로 매칭·자막이 끝난 장면은 재사용(느린 ffmpeg·ASR 재실행 방지) ──
        #    matched·srt가 오디오(그리고 원본영상)보다 새것이면 그대로 씀.
        if (os.path.exists(out) and os.path.exists(srt)
                and os.path.getmtime(out) >= os.path.getmtime(audio)
                and (not video or os.path.getmtime(out) >= os.path.getmtime(video))):
            rel = _read_srt(srt)
            if rel:
                clip_len = dur(out)
                all_cues += [(t + cs, t + ce, tx) for (cs, ce, tx) in rel]
                report.append((N, "재사용", A, None, clip_len, True))
                total_clip += clip_len
                t += clip_len
                continue                   # 이미 완료된 장면 → 건너뜀
        if video and video.lower().endswith(SCENE_IMG_EXTS):
            # 이미지 장면: 카메라 모션(켄번즈)을 구워 넣은 뒤 오디오 입힘(영상과 동일 산출물)
            mv = pick_move(N)
            tmp = os.path.join(OUT_MATCH, f"{N}_img.mp4")
            image_to_clip(video, A, tmp, move=mv)
            r = fit_video_to_audio(tmp, audio, out)
            clip_len = r["out_dur"]
            try: os.remove(tmp)
            except OSError: pass
            ok = abs(clip_len - A) < 0.15
            image_scenes.add(N)                    # CapCut 프리셋 애니 제외(이중 적용 방지)
            report.append((N, f"이미지→모션({mv})", A, A, clip_len, ok))
        elif video:
            r = fit_video_to_audio(video, audio, out)
            clip_len = r["out_dur"]        # 프레임 격자 정렬된 실제 길이(A′)
            ok = abs(clip_len - A) < 0.15
            report.append((N, r["mode"], A, r["V"], clip_len, ok))
        else:
            report.append((N, "SKIP(소재없음)", A, None, None, None))
        total_clip += clip_len
        # 자막: 태그 없는 seg['text'] · 실제 오디오의 발화 시각에 정렬(audio 전달)
        all_cues += make_cues(s["text"], t, clip_len, audio=audio)
        if make_per_scene:
            write_srt(make_cues(s["text"], 0.0, clip_len, audio=audio), srt)
        t += clip_len
    if make_unified:
        write_srt(all_cues, os.path.join(OUT_SRT, "전체.srt"))

    # ── 리포트 ──
    print("=" * 74)
    print(f"{'장면':>4} | {'mode':<14} | {'오디오':>7} | {'영상':>7} | {'결과':>7} | 판정")
    print("-" * 74)
    for N, mode, A, V, od, ok in report:
        As = f"{A:.3f}" if A is not None else "  -  "
        Vs = f"{V:.3f}" if V is not None else "  -  "
        Os = f"{od:.3f}" if od is not None else "  -  "
        verdict = "" if ok is None else ("PASS" if ok else "FAIL")
        print(f"{N:>4} | {mode:<14} | {As:>7} | {Vs:>7} | {Os:>7} | {verdict}")
    print("=" * 74)
    end_srt = all_cues[-1][1] if all_cues else 0.0
    print(f"전체 클립 합계 = {total_clip:.3f}s  (TTS 원본 합 {total_audio:.3f}s "
          f"+ 프레임정렬 무음 {total_clip - total_audio:+.3f}s)")
    print(f"전체.srt 마지막 cue 종료 = {end_srt:.3f}s | 일치: {abs(end_srt - total_clip) < 0.05}")
    if stale_audio:
        raise RuntimeError(
            f"⚠️ 예전 대본으로 만든 오디오가 {len(stale_audio)}개 남아 있습니다 "
            f"(장면 {', '.join(stale_audio[:15])}{' 외' if len(stale_audio) > 15 else ''}). "
            f"다른 화 음성이 섞이는 것을 막기 위해 중단했습니다 — ② 실제 제작을 먼저 끝내세요.")
    print("완료 → 매칭:", OUT_MATCH, "| 자막:", OUT_SRT)

# ── (legacy) scene_map.json으로 여러 오디오를 한 장면으로 묶던 옛 방식 ──
#    1:1 방식(run_match)으로 대체됨. 예외 상황 대비해 남겨둠.
def concat_audio(parts, out):
    lst = out + ".txt"
    with open(lst, "w", encoding="utf-8") as f:
        for m in parts:
            f.write(f"file '{os.path.abspath(os.path.join(OUT_AUDIO, m + '.mp3'))}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", out], check=True, capture_output=True)
    os.remove(lst)

# ── 장면 소재: 영상(mp4 등) 또는 이미지(jpg/png 등) 아무거나 ─────────
SCENE_VID_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi")
SCENE_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

def is_image_scene(seq):
    """그 장면의 소재가 이미지인가(=모션을 이미 구워 넣음 → CapCut 프리셋 애니 제외 대상)."""
    p = find_scene_media(seq)
    return bool(p and p.lower().endswith(SCENE_IMG_EXTS))

def find_scene_media(seq):
    """영상클립/ 에서 그 장면의 소재를 찾는다(영상 우선, 없으면 이미지). 없으면 None."""
    for ext in SCENE_VID_EXTS + SCENE_IMG_EXTS:
        p = os.path.join(VIDEO_DIR, f"{seq}{ext}")
        if os.path.exists(p):
            return p
    return None

# ── 정지 이미지 카메라 모션(켄번즈) ─────────────────────────────
#   이미지 장면이 정지 사진처럼 보이지 않게 ffmpeg zoompan으로 모션을 '구워' 넣는다.
#   ★ 속도는 장면 길이(seconds)로 나눠 정함 → 3초든 12초든 이동량이 비슷(긴 장면에서 얼굴 잘림 방지).
#   ★ 무브는 장면번호로 결정적 선택(랜덤 X → 다시 돌려도 같은 결과).
MOVES = ["push", "pull", "left", "right", "up", "down"]

def pick_move(scene_no, moves=None):
    """장면번호 → 무브 종류(결정적). 연속 장면이 서로 다른 움직임이 되게 순환."""
    pool = moves or MOVES
    try:
        i = int(re.sub(r"\D", "", str(scene_no)) or 0)
    except Exception:
        i = 0
    return pool[i % len(pool)]

def amp_for(seconds):
    """짧은 장면은 세게, 긴 장면은 약하게 — 총 이동량을 비슷하게."""
    if seconds <= 3:  return 0.16
    if seconds <= 6:  return 0.12
    if seconds <= 10: return 0.09
    return 0.07

def _move_expr(kind, n, amp):
    """(z, x, y) zoompan 표현식."""
    if kind == "push":
        return f"1.0+{amp}*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if kind == "pull":
        return f"{1+amp}-{amp}*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    base = f"{1+amp*0.6}"                       # 패닝은 살짝 확대한 상태에서 이동
    if kind == "left":
        return base, f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"
    if kind == "right":
        return base, f"(iw-iw/zoom)*(on/{n})", "ih/2-(ih/zoom/2)"
    if kind == "up":
        return base, "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(1-on/{n})"
    return base, "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(on/{n})"   # down

def image_to_clip(img, seconds, out, fps=24, size="1920x1080", move="push", amp=None):
    """정지 이미지 → 오디오 길이만큼의 무음 영상. move=None이면 모션 없이(기존 동작).
       모션은 여기서 구우므로 CapCut 프리셋 애니는 이 장면에 걸지 않는다(이중 적용 방지)."""
    seconds = max(seconds, 0.1)
    W, H = (int(v) for v in size.split("x"))
    if not move:                                  # 모션 없음(하위호환)
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", img, "-t", f"{seconds:.3f}",
                        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                               f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,fps={fps},format=yuv420p",
                        "-c:v", "libx264", "-preset", "veryfast", "-r", str(fps), out],
                       check=True, capture_output=True)
        return out
    n = max(1, int(round(seconds * fps)))          # 총 프레임 수 = zoompan d (끝까지 움직이게)
    a = amp_for(seconds) if amp is None else amp
    z, x, y = _move_expr(move, n, a)
    # 입력을 2배로 키운 뒤 잘라야 확대해도 안 뭉개지고 가장자리 검은 띠도 없음
    up = f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2}"
    vf = (f"{up},zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={fps},format=yuv420p")
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", img, "-t", f"{seconds:.3f}",
                    "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-r", str(fps), out],
                   check=True, capture_output=True)
    return out

def match_scene_legacy(scene_no, audio_parts):
    scene_audio = os.path.join(OUT_SCENE, f"{scene_no}.mp3")
    concat_audio(audio_parts, scene_audio)
    video = os.path.join(VIDEO_DIR, f"{scene_no}.mp4")
    if not os.path.exists(video):
        print(f"[SKIP] 장면{scene_no}: 영상클립 없음"); return
    out = os.path.join(OUT_MATCH, f"{scene_no}_matched.mp4")
    factor = dur(scene_audio) / dur(video)   # (버그수정) k=A/V
    subprocess.run(["ffmpeg", "-y", "-i", video, "-i", scene_audio,
        "-filter_complex", f"[0:v]setpts={factor}*PTS[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", out],
        check=True, capture_output=True)
    d = abs(dur(out) - dur(scene_audio))
    print(f"[{'PASS' if d < 0.15 else 'FAIL'}] 장면{scene_no}  오디오 {dur(scene_audio):.2f}s  diff {d:.3f}s")

def match_legacy(scene_map_path):
    with open(scene_map_path, encoding="utf-8") as f:
        scene_map = json.load(f)   # {"001":["001","002"], ...}
    for scene_no in sorted(scene_map):
        match_scene_legacy(scene_no, scene_map[scene_no])
    print("완료 →", OUT_MATCH)

# ─── 6. 슈퍼톤 성우 목록 (세팅 보조) ────────────────
def list_voices():
    res = ST.voices.list_voices(page_size=50)
    for v in (res.items or []):
        print(v.voice_id, "|", v.name, "|", v.language, "| styles:", v.styles)

# ─── 진입점 ─────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tts"
    if cmd == "tts":
        run_tts(sys.argv[2] if len(sys.argv) > 2 else "대본.txt")
    elif cmd == "dryrun":                       # API 호출 없이 리포트만
        dry_run(sys.argv[2] if len(sys.argv) > 2 else "대본.txt")
    elif cmd == "match":                       # 1:1 매칭 + 자막 (신규)
        run_match(sys.argv[2] if len(sys.argv) > 2 else "대본.txt")
    elif cmd == "match_legacy":                 # scene_map.json 묶음 방식 (구)
        match_legacy(sys.argv[2] if len(sys.argv) > 2 else "scene_map.json")
    elif cmd == "list":
        list_voices()
    else:
        print("사용: python tts_pipeline.py [tts|dryrun|match|match_legacy|list] [파일]")
