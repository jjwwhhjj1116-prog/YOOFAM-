# -*- coding: utf-8 -*-
"""TTS 발음 교정 레이어 — 엔진에 보내기 직전 대본 텍스트만 손본다.

왜 필요한가:
  ElevenLabs 한국어(특히 eleven_flash_v2_5)는 조사 앞 음절을 흘려버리는 일이 있다.
  예) "태강에게" → "태강게"  ("에"가 앞 음절에 먹힘)
  보이스가 '내 목소리 클론'이라 성우를 바꿀 수 없으므로, 모델이 삼키지 못하도록
  조사를 별개 운율 단위로 떼어 보낸다("태강 에게"). 자막·서명용 원본 텍스트는 그대로다.

설계 원칙:
  - 규칙은 코드가 아니라 발음교정.json 에 있다(주인님이 직접 추가/삭제).
  - 규칙이 텍스트를 실제로 바꾼 장면만 서명이 달라진다 → 그 장면만 재생성(크레딧 최소).
  - 규칙이 안 걸린 장면은 서명이 종전과 100% 동일 → 재생성 0건.
  - 파일이 없거나 깨져도 절대 예외를 올리지 않는다(무협 파이프라인 불변).
"""
import os, re, json, hashlib

_HERE  = os.path.dirname(os.path.abspath(__file__))
_RULES_PATH = os.path.join(_HERE, "발음교정.json")

# 규칙 파일이 없을 때 쓰는 내장 기본값(주인님이 보고한 '에게' 계열만 보수적으로)
_DEFAULT = {
    "적용": True,
    "조사분리": {
        "적용": True,
        "구분자": " ",
        "대상": ["에게"],   # 실제 보고된 패턴만. '에서'는 대상 장면이 6배로 늘어 기본 제외
    },
    "치환": {},
    "정규식": [],
}

_cache = None          # (mtime, rules) — 파일 고치면 자동 반영
_HANGUL = "가-힣"


def _load():
    """발음교정.json 로드(파일 변경 시 자동 재로드). 실패하면 내장 기본값."""
    global _cache
    try:
        mt = os.path.getmtime(_RULES_PATH)
    except OSError:
        _cache = (None, dict(_DEFAULT))
        return _cache[1]
    if _cache and _cache[0] == mt:
        return _cache[1]
    try:
        with open(_RULES_PATH, encoding="utf-8") as f:
            r = json.load(f)
        if not isinstance(r, dict):
            raise ValueError("최상위가 객체가 아님")
    except Exception as ex:
        print(f"  [발음교정] {os.path.basename(_RULES_PATH)} 읽기 실패 → 기본 규칙 사용: "
              f"{type(ex).__name__}: {ex}")
        r = dict(_DEFAULT)
    _cache = (mt, r)
    return r


def _particle_re(p, sep):
    """조사 p 를 '앞은 한글 음절, 뒤는 어절 경계'일 때만 분리.
       → 낱말 속(예: '에서'가 명사 일부)에는 걸리지 않는다."""
    return (re.compile(r"(?<=[" + _HANGUL + r"])" + re.escape(p) + r"(?=[\s,.!?…·\"'’”\)\]}]|$)"),
            sep + p)


def fix(text, kind="narration"):
    """대본 텍스트 → (교정된 텍스트, 적용된 규칙 설명 리스트).
       규칙이 하나도 안 걸리면 원본 그대로 + 빈 리스트를 돌려준다."""
    if not text:
        return text, []
    r = _load()
    if not r.get("적용", True):
        return text, []
    out, applied = text, []

    # 1) 낱말 치환(긴 것부터) — 고유명사·숫자·한자어 개별 교정용
    rep = r.get("치환") or {}
    for k in sorted(rep, key=len, reverse=True):
        if k and k in out:
            n = out.count(k)
            out = out.replace(k, str(rep[k]))
            applied.append(f"치환 {k}→{rep[k]}×{n}")

    # 2) 정규식 규칙 — 주인님이 직접 추가하는 고급 규칙
    for item in (r.get("정규식") or []):
        try:
            pat, to = item.get("찾기"), item.get("바꾸기", "")
            if not pat:
                continue
            new, n = re.subn(pat, to, out)
            if n:
                out = new
                applied.append(f"정규식 {item.get('설명') or pat}×{n}")
        except re.error as ex:
            print(f"  [발음교정] 정규식 무시({ex}): {item.get('찾기')}")

    # 3) 조사 분리 — 씹힘의 주 원인. 긴 조사부터 처리해 중복 분리를 막는다
    js = r.get("조사분리") or {}
    if js.get("적용", True):
        sep = js.get("구분자", " ")
        for p in sorted(js.get("대상") or [], key=len, reverse=True):
            if not p or p not in out:
                continue
            rx, to = _particle_re(p, sep)
            new, n = rx.subn(to, out)
            if n:
                out = new
                applied.append(f"조사분리 {p}×{n}")

    if out == text:
        return text, []
    return out, applied


def tag(text, kind="narration"):
    """오디오 서명에 덧붙일 꼬리표. 규칙이 텍스트를 바꿨을 때만 '|P:xxxxxxxx'.
       바꾸지 않았으면 빈 문자열 → 기존 서명과 완전히 동일(재생성 0건)."""
    fixed, applied = fix(text, kind)
    if not applied:
        return ""
    return "|P:" + hashlib.sha1(fixed.encode("utf-8")).hexdigest()[:8]


def rules_path():
    return _RULES_PATH


def preview(text, kind="narration"):
    """UI/검사용: 사람이 읽을 한 줄 요약."""
    fixed, applied = fix(text, kind)
    if not applied:
        return None
    return {"원본": text, "교정": fixed, "규칙": applied}
