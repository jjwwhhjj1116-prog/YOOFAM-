# -*- coding: utf-8 -*-
"""타입캐스트(Typecast) 대사 엔진 — Supertone 종료(2026-08-31) 대체.
   ★ 무협/야담/경제학 기존 코드를 건드리지 않고, 대사 생성만 담당하는 별도 모듈.

   특징:
     - ssfm-v30 + smart 감정: 앞뒤 장면 문맥을 넘기면 감정을 AI가 자동 판단
       → 우리 EMOTION_CHAINS 수동 매핑 없이도 감정이 실림
     - 대본에 감정 태그가 있으면 preset 모드로 강제 지정(선택)
     - 역할별 voice_id는 voice_map.json의 tc_voice_id 로 지정
"""
import os, json, time, urllib.request, urllib.error

API_URL   = "https://api.typecast.ai/v1/text-to-speech"
VOICES_URL= "https://api.typecast.ai/v2/voices"
MODEL     = "ssfm-v30"
LANG      = "kor"

# 우리 감정 style → 타입캐스트 preset (smart 모드를 못 쓸 때 대비)
EMO_PRESET = {
    "angry":"angry", "annoyed":"angry", "disgusted":"angry", "evil":"tonedown",
    "teasing":"tonedown", "suspicious":"tonedown", "dominating":"angry",
    "command":"angry", "firm":"normal", "courageous":"normal", "confident":"normal",
    "happy":"happy", "excited":"happy", "admiring":"happy", "friendly":"happy",
    "relieved":"happy", "serene":"normal", "sad":"sad", "scared":"sad",
    "anxious":"sad", "embarrassed":"sad", "surprised":"toneup", "neutral":"normal",
}

def _key():
    k = os.environ.get("TYPECAST_API_KEY")
    if not k:
        raise RuntimeError("TYPECAST_API_KEY 가 .env 에 없습니다.")
    return k

def _post(body, retries=5):
    """429/일시오류 자동 재시도."""
    delay = 4
    for attempt in range(retries):
        req = urllib.request.Request(API_URL, data=json.dumps(body).encode(),
            headers={"X-API-KEY": _key(), "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as ex:
            msg = ex.read().decode("utf-8", "ignore")[:200]
            if ex.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                print(f"    [타입캐스트 {ex.code}] {delay}s 후 재시도 ({attempt+1}/{retries})", flush=True)
                time.sleep(delay); delay = min(int(delay * 1.8), 60); continue
            if ex.code in (401, 403):
                raise RuntimeError(f"타입캐스트 인증 실패 — API 키 확인 필요. {msg}")
            if ex.code == 402 or "quota" in msg.lower() or "credit" in msg.lower():
                raise RuntimeError(f"💳 타입캐스트 크레딧 소진. 충전 후 ②를 다시 누르면 이어서 생성됩니다. {msg}")
            raise RuntimeError(f"타입캐스트 오류 {ex.code}: {msg}")
        except Exception as ex:
            if attempt < retries - 1:
                time.sleep(delay); delay = min(int(delay * 1.8), 60); continue
            raise

def list_voices():
    req = urllib.request.Request(VOICES_URL, headers={"X-API-KEY": _key()})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def voice_for(role, voice_map):
    """역할 → 타입캐스트 voice_id (voice_map.json의 tc_voice_id)."""
    cfg = voice_map.get(role) or {}
    return cfg.get("tc_voice_id")

def synth(out_path, text, voice_id, prev_text=None, next_text=None,
          style=None, intensity=None, pitch=0, tempo=1.0, use_smart=True):
    """대사 1건 생성. use_smart=True면 앞뒤 문맥으로 감정 자동, 아니면 style→preset."""
    if use_smart:
        prompt = {"emotion_type": "smart",
                  "previous_text": (prev_text or "")[:2000],
                  "next_text": (next_text or "")[:2000]}
    else:
        prompt = {"emotion_type": "preset",
                  "emotion_preset": EMO_PRESET.get(style, "normal"),
                  "emotion_intensity": float(intensity if intensity is not None else 1.0)}
    body = {"voice_id": voice_id, "text": text[:2000], "model": MODEL, "language": LANG,
            "prompt": prompt,
            "output": {"audio_pitch": int(pitch), "audio_tempo": float(tempo),
                       "audio_format": "mp3"}}
    data = _post(body)
    if not data:
        raise RuntimeError("타입캐스트가 빈 오디오를 반환했습니다.")
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path
