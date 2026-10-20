# -*- coding: utf-8 -*-
r"""Gemini TTS 대사 성우 오디션 — '감정표현 잘 되는 보이스'를 실측으로 고른다.

핵심 지표는 피치폭이 아니라 **감정 반응도**다.
  같은 보이스에 평온/분노/냉혹을 지시했을 때 F0가 실제로 얼마나 움직이는가.
  움직이지 않으면 라벨이 뭐든 '연기를 안 하는' 보이스다.
  (기존 실측 샘플: Charon 이 평온 105Hz → 악역 147Hz = 5.7세미톤 이동. 이게 좋은 값이다.)

같이 재는 것:
  피치폭(세미톤)  — 한 클립 안에서의 억양 폭. 참고: 주인님 나레이션 10.4 / 무협도서관 10.9
  ASR 일치도      — 한국어를 안 씹고 제대로 읽는가(자모 단위)

준비:
  .env 에 GEMINI_API_KEY=... 한 줄 추가 (aistudio.google.com/apikey 에서 발급)
  google-genai 는 이미 설치돼 있음

사용법:
  .venv\Scripts\python.exe 제미나이보이스.py              # 드라이런 — 무엇을 돌릴지만 표시
  .venv\Scripts\python.exe 제미나이보이스.py --생성        # 실제 생성 + 순위표
  .venv\Scripts\python.exe 제미나이보이스.py --생성 --역할 악역
  .venv\Scripts\python.exe 제미나이보이스.py --채점        # 이미 만든 것만 다시 채점(무료)
"""
import os
import sys
import glob
import difflib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

OUT = "_제미나이오디션"
MODEL = "gemini-3.8-flash-tts"

# 공식 라벨 기준 후보. 성별·연령은 라벨에 없으므로 '추정'이고, 그래서 이 스크립트로 확인한다.
# ★ 순서 주의: 무료 쿼터가 중간에 끊기므로 '아직 안 재본 역할'을 앞에 둔다.
#   남주(Orus)·여전사(Kore)·중년1(Charon)·악역4(Fenrir)는 이미 실측 완료라 맨 뒤.
CANDIDATES = {
    "악역": ["Algenib", "Enceladus", "Pulcherrima", "Umbriel", "Algieba"],
    "노인": ["Gacrux", "Sadaltager", "Schedar", "Rasalgethi"],
    "여주": ["Sulafat", "Vindemiatrix", "Achernar", "Despina", "Erinome"],
    "청년": ["Iapetus", "Algieba", "Achird", "Zubenelgenubi", "Leda"],
    "중년": ["Rasalgethi", "Schedar", "Alnilam", "Charon"],
    "기측정": ["Orus", "Kore", "Fenrir"],
}

# 같은 대사를 세 감정으로 — F0가 얼마나 움직이는지 보려면 대사가 같아야 한다.
LINE = "내 이놈. 감히 내 앞에서 검을 뽑겠다는 것이냐."
# Part.speech_metadata.style 로 전달한다. 프롬프트 접두어로 넣으면 모델이 지시문을
# 그대로 소리내어 읽어버린다(실측 확인). SDK 2.29.0 이상 필요.
STYLES = [
    ("평온", "평온하고 담담하게, 감정을 억누르며"),
    ("분노", "크게 분노하여 격앙되게, 호통치듯"),
    ("냉혹", "차갑고 서늘하게, 낮게 깔아서"),
]


# ─── 측정 ──────────────────────────────────────────
def _pitch(path):
    """(중앙 F0 Hz, 피치폭 세미톤). 실패하면 (None, None)."""
    import subprocess
    import tempfile
    import wave

    import numpy as np

    w = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", "16000", w],
            check=True,
        )
        wv = wave.open(w, "rb")
        sr = wv.getframerate()
        x = np.frombuffer(wv.readframes(wv.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        wv.close()
        fl, hop = int(0.040 * sr), int(0.020 * sr)
        f0 = []
        for i in range(0, len(x) - fl, hop):
            fr = x[i : i + fl]
            if np.sqrt(np.mean(fr ** 2)) < 0.02:
                continue
            fr = fr - fr.mean()
            ac = np.correlate(fr, fr, "full")[fl - 1 :]
            if ac[0] <= 0:
                continue
            lo, hi = int(sr / 350), int(sr / 65)
            k = int(np.argmax(ac[lo:hi])) + lo
            if 2 * k < hi and ac[2 * k] > 0.90 * ac[k]:
                k = 2 * k
            if ac[k] / ac[0] < 0.4:
                continue
            f0.append(sr / k)
        if len(f0) < 20:
            return None, None
        f0 = np.array(f0)
        p5, p95 = np.percentile(f0, [5, 95])
        return float(np.median(f0)), float(12 * np.log2(p95 / p5))
    except Exception:
        return None, None
    finally:
        try:
            os.remove(w)
        except OSError:
            pass


def _accuracy(path, want):
    """대본 대비 자모 일치도. ASR 자체 오탈자가 섞이니 순위 힌트로만."""
    import tts_pipeline as e
    import 발음검사 as C

    heard = "".join(w[2] for w in e.asr_words(path))
    r = difflib.SequenceMatcher(None, C.jamo(want), C.jamo(heard), autojunk=False).ratio()
    return r, heard


# ─── 생성 ──────────────────────────────────────────
def _client():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        for line in open(".env", encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith(("GEMINI_API_KEY=", "GOOGLE_API_KEY=")):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY 가 없습니다. aistudio.google.com/apikey 에서 발급받아 "
            ".env 에 'GEMINI_API_KEY=...' 한 줄 추가하세요."
        )
    from google import genai

    return genai.Client(api_key=key)


def _synth(client, voice, style_prompt, text, out):
    """한 클립 생성. 반환된 PCM(24kHz L16)을 wav로 저장."""
    from google.genai import types

    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Content(parts=[types.Part(
            text=text,
            speech_metadata=types.SpeechMetadata(style=style_prompt),
        )])],
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )
    data = resp.candidates[0].content.parts[0].inline_data.data
    import wave

    with wave.open(out, "wb") as w:          # 응답은 헤더 없는 24kHz 16bit mono PCM
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(data)
    return out


def _path(role, voice, tag):
    return os.path.join(OUT, "%s_%s_%s.wav" % (role, voice, tag))


# ─── 실행 ──────────────────────────────────────────
def dry(roles):
    print("=" * 76)
    print("  Gemini TTS 대사 오디션 — 드라이런 (생성 안 함)")
    print("=" * 76)
    n = 0
    for role in roles:
        print("\n  [%s]  %s" % (role, ", ".join(CANDIDATES[role])))
        n += len(CANDIDATES[role]) * len(STYLES)
    print("\n  대사: %s" % LINE)
    print("  감정 %d종: %s" % (len(STYLES), ", ".join(t for t, _ in STYLES)))
    print("\n  총 %d클립 (모델 %s, 24kHz wav → %s 폴더)" % (n, MODEL, OUT))
    print("\n  측정 지표")
    print("    감정반응도 = 평온↔분노 F0 이동(세미톤). 클수록 연기를 한다. 기준: Charon 5.7st")
    print("    피치폭     = 클립 내 억양 폭. 참고: 주인님 나레이션 10.4 / 무협도서관 10.9")
    print("    일치도     = 한국어를 안 씹고 읽는가")
    print("\n  준비: .env 에 GEMINI_API_KEY=... 추가 (aistudio.google.com/apikey)")
    print("  실행: 제미나이보이스.py --생성")


def run(roles, generate=True):
    os.makedirs(OUT, exist_ok=True)
    client = _client() if generate else None
    rows = []
    for role in roles:
        for voice in CANDIDATES[role]:
            per_style = {}
            ok = True
            for tag, prompt in STYLES:
                p = _path(role, voice, tag)
                if generate and not os.path.exists(p):
                    try:
                        print("  생성 %s/%s/%s ..." % (role, voice, tag), flush=True)
                        _synth(client, voice, prompt, LINE, p)
                    except Exception as ex:
                        print("     실패 %s: %s" % (type(ex).__name__, str(ex)[:110]))
                        ok = False
                        break
                if not os.path.exists(p):
                    ok = False
                    break
                f0, span = _pitch(p)
                acc, heard = _accuracy(p, LINE)
                per_style[tag] = (f0, span, acc, heard)
            if not ok or len(per_style) < len(STYLES):
                continue
            calm = per_style["평온"][0]
            rage = per_style["분노"][0]
            import math

            react = abs(12 * math.log2(rage / calm)) if (calm and rage) else None
            spans = [v[1] for v in per_style.values() if v[1]]
            accs = [v[2] for v in per_style.values() if v[2]]
            rows.append((
                react or 0.0, role, voice,
                sum(spans) / len(spans) if spans else 0.0,
                sum(accs) / len(accs) if accs else 0.0,
                per_style,
            ))

    if not rows:
        print("\n  측정할 클립이 없습니다.")
        return
    rows.sort(key=lambda r: (-r[0], -r[3]))
    print("\n" + "=" * 76)
    print("  순위 — 감정반응도(평온↔분노 F0 이동) 우선")
    print("=" * 76)
    print("  %-6s %-14s %9s %8s %8s   %s" % ("역할", "보이스", "감정반응", "피치폭", "일치도", "평온→분노 F0"))
    for react, role, voice, span, acc, ps in rows:
        print("  %-6s %-14s %7.1fst %6.1fst %7.1f%%   %.0f → %.0f Hz"
              % (role, voice, react, span, acc * 100, ps["평온"][0] or 0, ps["분노"][0] or 0))
    print("\n  감정반응 3st 미만 = 스타일 지시를 거의 안 따르는 보이스 (연기 못 함)")
    print("  최종 판정은 귀로 — %s 폴더의 분노/냉혹 클립을 들어보세요." % OUT)


if __name__ == "__main__":
    argv = sys.argv[1:]
    roles = list(CANDIDATES)
    for i, a in enumerate(argv):
        if a == "--역할" and i + 1 < len(argv) and argv[i + 1] in CANDIDATES:
            roles = [argv[i + 1]]
    if "--채점" in argv:
        run(roles, generate=False)
    elif "--생성" in argv or "--go" in argv:
        run(roles, generate=True)
    else:
        dry(roles)
