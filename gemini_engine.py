# -*- coding: utf-8 -*-
"""Gemini TTS 대사 엔진 — 타입캐스트/수퍼톤 대체 후보.
   ★ 무협/야담/경제학 기존 코드를 건드리지 않고, 대사 생성만 담당하는 별도 모듈
     (typecast_engine.py 와 같은 패턴).

   특징:
     - 감정을 '한국어 연기 지시문'으로 넘긴다 → 같은 보이스가 실제로 톤을 바꾼다
       실측: Orus 평온 129Hz → 분노 229Hz → 냉혹 87Hz (감정반응 9.9세미톤)
     - 역할별 voice_id 는 voice_map.json 의 gm_voice_id 로 지정

   ★★ 반드시 Part.speech_metadata 로 넘겨야 한다 ★★
     스타일을 프롬프트 접두어("분노한 목소리로: 대사")로 넣으면 모델이 그 지시문을
     그대로 소리내어 읽어버린다(실측 확인). SpeechConfig 에는 스타일 필드가 없고,
     v1beta REST 의 speechConfig.speechMetadata 는 400 Unknown name 이 난다.
     google-genai 2.29.0 이상에서만 Part.speech_metadata 가 존재한다.
"""
import os
import time
import wave
import shutil
import tempfile
import subprocess

MODEL = "gemini-3.8-flash-tts"
SAMPLE_RATE = 24000          # 응답은 헤더 없는 24kHz 16bit mono PCM

# 우리 감정 style → Gemini 연기 지시문(한국어).
# 무협 대사 톤에 맞춰 작성. 문장으로 줘야 모델이 잘 따른다.
EMO_STYLE = {
    "angry":       "크게 분노하여 격앙되게, 호통치듯",
    "annoyed":     "짜증이 묻어나게, 날카롭고 퉁명스럽게",
    "disgusted":   "역겨움이 묻어나게, 내뱉듯이",
    "evil":        "사악하고 음흉하게, 낮게 웃음기를 머금고",
    "teasing":     "비웃듯 빈정거리며, 여유롭게",
    "suspicious":  "의심에 찬 목소리로, 낮게 떠보듯",
    "dominating":  "위압적으로 내려누르듯, 단호하게",
    "command":     "명령하듯 단호하고 위엄 있게",
    "firm":        "흔들림 없이 단호하고 결연하게",
    "courageous":  "담대하고 기개 있게",
    "confident":   "자신감 있게, 여유를 띠고",
    "happy":       "밝고 기쁘게",
    "excited":     "들뜨고 흥분하여 빠르게",
    "admiring":    "감탄하며 우러르듯",
    "friendly":    "따뜻하고 다정하게",
    "relieved":    "안도하며 숨을 내쉬듯",
    "serene":      "평온하고 담담하게, 감정을 억누르며",
    "sad":         "슬픔에 잠겨 가라앉은 목소리로",
    "scared":      "두려움에 떨리는 목소리로",
    "anxious":     "불안하고 초조하게",
    "embarrassed": "당황하여 말끝을 흐리며",
    "surprised":   "놀라서 숨을 들이켜듯",
    "neutral":     "평온하고 담담하게",
    # 무협 전용으로 자주 쓰는 결
    "cold":        "차갑고 서늘하게, 낮게 깔아서",
}

# 대본의 한국어 감정어 → 연기 지시문 (EMOTION_CHAINS 41종 + 자주 쓰는 변형).
# Gemini 는 성우별 지원 style 제약이 없으므로 원래 감정어를 그대로 살린다.
KO_EMO_STYLE = {
    # 분노 계열
    "분노": "크게 분노하여 격앙되게, 호통치듯",
    "격노": "극도로 격노하여 터뜨리듯",
    "분개": "분개하여 떨리는 목소리로",
    "격분": "격분하여 쏘아붙이듯",
    "발악": "악에 받쳐 발악하듯",
    "광기": "광기에 차 들뜬 목소리로",
    "다급": "다급하고 숨 가쁘게",
    # 비웃음 계열
    "냉소": "차갑게 비웃으며, 코웃음치듯",
    "조롱": "조롱하듯 빈정거리며",
    "조소": "비웃음을 머금고 낮게",
    "비웃음": "비웃듯 빈정거리며",
    # 차가움 계열
    "냉혹": "차갑고 서늘하게, 낮게 깔아서",
    "냉정": "차갑고 침착하게, 감정을 눌러",
    "서늘함": "서늘하게 가라앉은 목소리로",
    "위협": "위협하듯 낮고 묵직하게",
    "혐오": "혐오가 묻어나게, 내뱉듯이",
    # 결의 계열
    "단호": "흔들림 없이 단호하게",
    "결의": "결연한 각오를 담아",
    "결사": "목숨을 건 결의로, 비장하게",
    "확신": "확신에 차 또렷하게",
    "오만": "오만하게 내려다보듯",
    "비장": "비장하게, 무겁게",
    # 슬픔 계열
    "슬픔": "슬픔에 잠겨 가라앉은 목소리로",
    "절망": "절망에 무너진 목소리로",
    "오열": "오열하듯 흐느끼며",
    "체념": "체념한 듯 힘없이",
    "애원": "애원하듯 간절하게",
    "절규": "절규하듯 목이 터져라",
    # 밝음 계열
    "기쁨": "기쁘고 밝게",
    "희열": "희열에 차 들뜨게",
    "안도": "안도하며 숨을 내쉬듯",
    "감탄": "감탄하며 우러르듯",
    "다정": "다정하고 부드럽게",
    # 놀람/당황 계열
    "놀람": "놀라서 숨을 들이켜듯",
    "경악": "경악하여 말을 잇지 못하듯",
    "당황": "당황하여 말끝을 흐리며",
    "두려움": "두려움에 떨리는 목소리로",
    "수줍": "수줍게 머뭇거리며",
    # 차분 계열
    "담담": "평온하고 담담하게, 감정을 억누르며",
    "차분": "차분하고 가라앉은 목소리로",
    "침착": "침착하고 흔들림 없이",
    "무심함": "무심하게, 감정을 싣지 않고",
    "무심": "무심하게, 감정을 싣지 않고",
    "평온": "평온하고 담담하게",
}


def style_for_emotion(emotion, fallback_style=None):
    """대본 감정어 → 연기 지시문.
       1) 한국어 감정어 직결   2) 부분일치   3) resolve_style 결과로 폴백   4) 중립."""
    key = (emotion or "").strip()
    if key in KO_EMO_STYLE:
        return KO_EMO_STYLE[key]
    if key:
        for k, v in KO_EMO_STYLE.items():     # '냉정함', '분노함' 같은 변형 흡수
            if k in key:
                return v
    if fallback_style:
        return style_for(fallback_style)
    return EMO_STYLE["neutral"]


_client = None


def _key():
    k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not k:
        try:
            for line in open(".env", encoding="utf-8", errors="ignore"):
                line = line.strip()
                if line.startswith(("GEMINI_API_KEY=", "GOOGLE_API_KEY=")):
                    k = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except OSError:
            pass
    if not k:
        raise RuntimeError(
            "GEMINI_API_KEY 가 .env 에 없습니다. aistudio.google.com/apikey 에서 발급하세요."
        )
    return k


def client():
    """google.genai 클라이언트(1회 생성 후 재사용)."""
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=_key())
    return _client


def style_for(style):
    """우리 감정 style → 연기 지시문. 모르는 style 이면 중립."""
    return EMO_STYLE.get(style) or EMO_STYLE["neutral"]


def voice_for(role, voice_map):
    """역할 → Gemini voice 이름 (voice_map.json 의 gm_voice_id)."""
    cfg = voice_map.get(role) or {}
    return cfg.get("gm_voice_id")


def _pcm_to_mp3(pcm, out_path):
    """헤더 없는 24kHz PCM → mp3. 다른 엔진과 산출물 형식을 맞춘다."""
    tmp_wav = tempfile.mktemp(suffix=".wav")
    try:
        with wave.open(tmp_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        if out_path.lower().endswith(".wav"):
            shutil.copy(tmp_wav, out_path)
            return out_path
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_wav,
             "-codec:a", "libmp3lame", "-q:a", "2", out_path],
            capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError("ffmpeg mp3 변환 실패: " + (r.stderr or "")[:200])
        return out_path
    finally:
        try:
            os.remove(tmp_wav)
        except OSError:
            pass


def _quota_message(ex, msg):
    """429 를 사람이 읽을 수 있게 — 어느 쿼터가 얼마나 차서 언제 풀리는지.
       하루 한도는 자정까지 막히는 벽이 아니라 롤링 윈도우다. retryDelay 를 반드시 보여준다.
       (안 보여주면 '내일까지 기다려야 하나' 하고 하루를 버린다 — 실제로 그랬다.)"""
    import re
    지표 = 한도 = 모델 = 대기 = None
    d = getattr(ex, "details", None)
    if isinstance(d, dict):
        for det in (d.get("error") or {}).get("details") or []:
            t = det.get("@type", "")
            if t.endswith("QuotaFailure"):
                v = (det.get("violations") or [{}])[0]
                지표 = v.get("quotaMetric", "").split("/")[-1] or None
                한도 = v.get("quotaValue")
                모델 = (v.get("quotaDimensions") or {}).get("model")
            elif t.endswith("RetryInfo"):
                대기 = det.get("retryDelay")
    if 대기 is None:                                   # 본문의 "retry in 6m59.3s" 폴백
        m = re.search(r"retry in ([\dhms.]+)", msg, re.I)
        대기 = m.group(1) if m else None
    초 = None
    if 대기:
        m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s?)?", str(대기).strip())
        if m:
            h, mi, se = (float(x) if x else 0 for x in m.groups())
            초 = int(h * 3600 + mi * 60 + se)
    부분 = ["💳 Gemini 쿼터 소진(429)"]
    if 지표:
        부분.append("한도: %s = %s%s" % (지표, 한도 or "?",
                                      (" · 모델 %s" % 모델) if 모델 else ""))
    if 초 is not None:
        부분.append("⏱ %s 뒤에 다시 됩니다 (%d분 %d초). 하루 한도는 자정까지 막히는 벽이 "
                    "아니라 롤링 윈도우라, 24시간 전 호출이 빠지면서 자리가 조금씩 돌아옵니다."
                    % (대기, 초 // 60, 초 % 60))
    else:
        부분.append("재시도 시각을 응답에서 찾지 못했습니다.")
    부분.append("②를 다시 누르면 남은 장면부터 이어서 생성됩니다. "
                "한 번에 끝내려면 Tier 를 올리거나(결제 연동), "
                "voice_map.json 에서 일부 역할의 engine 을 typecast 로 옮기세요.")
    return "  ".join(부분) + "  (원본: " + msg[:200] + ")"


def synth(out_path, text, voice_id, style=None, retries=5, raw_style=False):
    """대사 1건 생성.
       raw_style=False → style 은 감정 style 키(angry/evil/firm…)
       raw_style=True  → style 이 이미 완성된 한국어 연기 지시문"""
    from google.genai import types

    instruction = style if raw_style else style_for(style)
    delay = 5
    for attempt in range(retries):
        try:
            resp = client().models.generate_content(
                model=MODEL,
                contents=[types.Content(parts=[types.Part(
                    text=text[:2000],
                    speech_metadata=types.SpeechMetadata(style=instruction),
                )])],
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice_id)
                        )
                    ),
                ),
            )
            pcm = resp.candidates[0].content.parts[0].inline_data.data
            if not pcm:
                raise RuntimeError("Gemini 가 빈 오디오를 반환했습니다.")
            return _pcm_to_mp3(pcm, out_path)
        except Exception as ex:
            msg = str(ex)
            low = msg.lower()
            # 쿼터 소진은 재시도해도 소용없다 — 지표·한도·재시도 시각을 그대로 알리고 중단
            if "resource_exhausted" in low or "429" in msg or "quota" in low:
                raise RuntimeError(_quota_message(ex, msg))
            if any(t in low for t in ("permission", "api key", "unauthenticated", "401", "403")):
                raise RuntimeError("Gemini 인증 실패 — GEMINI_API_KEY 확인 필요. " + msg[:150])
            transient = any(t in low for t in
                            ("timeout", "timed out", "connection", "500", "502", "503", "504",
                             "unavailable", "빈 오디오"))
            if transient and attempt < retries - 1:
                print(f"    [Gemini 일시오류] {delay}s 후 재시도 ({attempt+1}/{retries}): {msg[:70]}",
                      flush=True)
                time.sleep(delay)
                delay = min(int(delay * 1.8), 60)
                continue
            raise


def list_voices():
    """공식 프리빌트 보이스 30종(라벨). 성별·연령은 공식 라벨에 없다 — 오디션으로 확인할 것."""
    return {
        "Zephyr": "Bright", "Puck": "Upbeat", "Charon": "Informative", "Kore": "Firm",
        "Fenrir": "Excitable", "Leda": "Youthful", "Orus": "Firm", "Aoede": "Breezy",
        "Callirrhoe": "Easy-going", "Autonoe": "Bright", "Enceladus": "Breathy",
        "Iapetus": "Clear", "Umbriel": "Easy-going", "Algieba": "Smooth",
        "Despina": "Smooth", "Erinome": "Clear", "Algenib": "Gravelly",
        "Rasalgethi": "Informative", "Laomedeia": "Upbeat", "Achernar": "Soft",
        "Alnilam": "Firm", "Schedar": "Even", "Gacrux": "Mature",
        "Pulcherrima": "Forward", "Achird": "Friendly", "Zubenelgenubi": "Casual",
        "Vindemiatrix": "Gentle", "Sadachbia": "Lively", "Sadaltager": "Knowledgeable",
        "Sulafat": "Warm",
    }
