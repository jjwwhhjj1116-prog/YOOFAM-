# -*- coding: utf-8 -*-
r"""씹힘 해결책 찾기 — 같은 문장을 여러 설정으로 만들어 자동 채점한다.

왜 필요한가:
  ElevenLabs 한국어에 "이렇게 읽어라"를 넣는 공식 방법은 사실상 하나뿐이다(별칭 재표기).
  IPA/phoneme 규칙은 영어 전용 모델에서만 동작하므로 한국어에는 못 쓴다.
  그래서 남은 레버는 '모델·설정·텍스트 표기'뿐이고, 어느 게 먹히는지는 실측만이 답이다.

  기본은 드라이런(크레딧 0) — 무엇을 보낼지만 보여준다.
  실제 생성은 --생성 을 붙일 때만.

사용법
  # 대본 모드 (권장) — 실제 대본의 '에게' 문장들로 flash / 조사분리 / v2 / v3 비교
  .venv\Scripts\python.exe 발음AB.py --대본 _작업대본.txt
  .venv\Scripts\python.exe 발음AB.py --대본 _작업대본.txt --생성

  # 한 문장 모드 — 특정 문장 하나를 전략 10개로
  .venv\Scripts\python.exe 발음AB.py "태강에게 물어볼 일이 남았다고 했다."
  .venv\Scripts\python.exe 발음AB.py "태강에게 물어볼 일이 남았다고 했다." --생성
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

OUT = "_발음AB"
PARTICLES = ("에게서", "에게")

FLASH = "eleven_flash_v2_5"
MULTI = "eleven_multilingual_v2"
V3 = "eleven_v3"


# ─── 표현력 지표 ────────────────────────────────────
def _expressiveness(path):
    """피치 폭(세미톤)과 중앙 F0(Hz). '더 재미나게 읽나'를 숫자로 본다."""
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

        fl = int(0.040 * sr)
        hop = int(0.020 * sr)
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


def _voice_settings(st):
    from elevenlabs import VoiceSettings

    return VoiceSettings(
        stability=st.get("stability"),
        similarity_boost=st.get("similarity_boost"),
        style=st.get("style"),
        use_speaker_boost=st.get("use_speaker_boost"),
        speed=st.get("speed"),
    )


def _generate(e, cfg, st, text, out):
    """한 조각 생성. 실패하면 예외를 그대로 올린다."""
    kw = dict(
        text=text,
        voice_id=cfg["voice_id"],
        model_id=st.get("model_id", FLASH),
        output_format="mp3_44100_128",
        voice_settings=_voice_settings(st),
    )
    if st.get("language_code"):
        kw["language_code"] = st["language_code"]
    e._el_convert(kw, out)
    return out


def _billed(chars, model_id):
    """multilingual_v2 는 flash 대비 크레딧 2배로 환산."""
    return chars * (2 if model_id == MULTI else 1)


# ═══ 대본 모드 — 실제 대본의 '에게' 문장들로 비교 ══════════════
#   한 문장 테스트는 표본이 1개라 운에 좌우된다. 대본에서 '에게'가 든 절만
#   뽑아 같은 묶음을 4가지 설정으로 만들고, '에'가 살아남았는지 직접 센다.


def clauses_from_script(script_path, limit=8):
    """대본에서 '에게'가 든 절만 추출. 절 단위로 잘라 전송 글자수를 줄인다."""
    import tts_pipeline as e

    segs = e.load_segments(script_path)
    out = []
    for seg in segs:
        for clause in re.split(r"(?<=[.!?…])\s+", seg["text"]):
            clause = clause.strip()
            if not clause or not any(p in clause for p in PARTICLES):
                continue
            if len(clause) > 80:
                i = max(clause.find(p) for p in PARTICLES if p in clause)
                clause = clause[max(0, i - 35) : i + 45].strip()
            out.append((seg["seq"], clause))
            break
    return out[:limit]


def script_configs():
    """형님 질문 그대로: 지금 / 조사분리 / v2 / v3 — 4개만 비교."""
    return [
        ("A_현재_flash", False, {}, "지금 그대로 — 기준선"),
        ("B_flash_조사분리", True, {}, "flash + '에게' 떼기 (발음교정.json 방식)"),
        ("C_multi_v2", False, {"model_id": MULTI}, "multilingual_v2 · 텍스트 원본 (크레딧 2배)"),
        ("D_v3", False, {"model_id": V3}, "v3 · 텍스트 원본 (대사에서 검증된 모델)"),
    ]


def _count_particles(txt):
    """조사 개수를 중복 없이 센다.
       '에게서'는 '에게'를 포함하므로 긴 것부터 지워가며 세야 이중계수가 안 생긴다."""
    n = 0
    for p in PARTICLES:            # PARTICLES 는 긴 것부터 정렬되어 있다
        n += txt.count(p)
        txt = txt.replace(p, " ")
    return n


def _survived(want, heard):
    """의도한 조사 개수 중 실제로 들린 개수. 씹히면 '게'만 남아 카운트가 준다."""
    exp = _count_particles(want)
    got = _count_particles(heard)
    return min(got, exp), exp


def script_dry(script_path):
    clauses = clauses_from_script(script_path)
    cfgs = script_configs()
    print("=" * 78)
    print("  대본 모드 드라이런 — 크레딧 0.  대본: %s" % script_path)
    print("=" * 78)
    if not clauses:
        print("  '에게'가 든 문장을 못 찾았습니다.")
        return
    print()
    print("  테스트 문장 %d개:" % len(clauses))
    for seq, c in clauses:
        print("    [%s] %s" % (seq, c))
    n = sum(len(c) for _, c in clauses)
    total = 0
    print()
    print("  설정 %d개:" % len(cfgs))
    for name, _sep, ov, desc in cfgs:
        total += _billed(n, ov.get("model_id", FLASH))
        print("    %-18s %s" % (name, desc))
    print()
    print("  전송 %d자 x %d설정 · 과금환산 ~%d자 (월 197,000자 대비 %.2f%%)"
          % (n, len(cfgs), total, total / 197000 * 100))
    print("  mp3 %d개가 %s 폴더에 생깁니다." % (len(clauses) * len(cfgs), OUT))
    print()
    print("  실제 생성:  발음AB.py --대본 %s --생성" % script_path)


def script_run(script_path):
    import difflib

    import tts_pipeline as e
    import 발음검사 as C
    import 발음교정 as P

    clauses = clauses_from_script(script_path)
    if not clauses:
        print("  '에게'가 든 문장을 못 찾았습니다.")
        return
    cfg = e.VOICE_MAP.get("나레이션")
    base = dict(cfg.get("settings") or {})
    os.makedirs(OUT, exist_ok=True)
    table = []

    for name, use_sep, ov, desc in script_configs():
        st = dict(base)
        st.update(ov)
        surv = exp_tot = 0
        ratios, spans = [], []
        print()
        print("  -- %s (%s)" % (name, desc))
        for seq, want in clauses:
            send = P.fix(want)[0] if use_sep else want
            out = os.path.join(OUT, "%s_%s.mp3" % (name, seq))
            try:
                _generate(e, cfg, st, send, out)
            except Exception as ex:
                print("     [%s] 생성실패 %s: %s" % (seq, type(ex).__name__, str(ex)[:70]))
                continue
            heard = "".join(w[2] for w in e.asr_words(out))
            got, exp = _survived(want, heard)
            surv += got
            exp_tot += exp
            ratio = difflib.SequenceMatcher(
                None, C.jamo(want), C.jamo(heard), autojunk=False
            ).ratio()
            ratios.append(ratio)
            _f0, span = _expressiveness(out)
            if span:
                spans.append(span)
            mark = "OK  " if got == exp else "씹힘"
            print("     [%s] %s 에게 %d/%d · 일치 %.0f%% · %s"
                  % (seq, mark, got, exp, ratio * 100, heard[:44]))
        table.append((
            surv / exp_tot if exp_tot else 0.0,
            surv, exp_tot, name, desc,
            sum(ratios) / len(ratios) if ratios else 0.0,
            sum(spans) / len(spans) if spans else None,
        ))

    table.sort(key=lambda r: (-r[0], -r[5]))
    print()
    print("=" * 78)
    print("  결과 — '에게' 생존율이 1순위 지표입니다")
    print("=" * 78)
    print("   에게생존   평균일치   피치폭  설정")
    for rate, surv, tot, name, desc, ratio, span in table:
        span_s = "%6.1fst" % span if span else "      -"
        print("   %3d/%-3d %3.0f%%  %7.1f%% %s  %-18s %s"
              % (surv, tot, rate * 100, ratio * 100, span_s, name, desc))
    print()
    print("  경고: 1등을 반드시 귀로 들어 '내 목소리인지' 확인하세요.")
    print("        모델(C/D)을 바꾸면 클론 음색 자체가 이동할 수 있습니다.")
    print("  mp3: %s 폴더" % OUT)


# ═══ 한 문장 모드 — 전략 10개 ═════════════════════════════


def strategies(text):
    """전략 목록: (이름, 보낼 텍스트, 덮어쓸 설정, 설명)"""
    import 발음교정 as P

    fixed, applied = P.fix(text)
    sep_space = fixed if applied else text

    comma = text
    brk = text
    for p in PARTICLES:
        comma = comma.replace(p, "," + p)
        brk = brk.replace(p, '<break time="0.12s" />' + p)

    return [
        ("01_원본_현재설정", text, {}, "지금 그대로 — 기준선(정말 씹히는지 확인)"),
        ("02_조사분리_공백", sep_space, {}, "조사를 공백으로 떼기 — 발음교정.json 기본 전략"),
        ("03_조사분리_쉼표", comma, {}, "조사 앞 쉼표 — 확실하나 쉼이 생겨 흐름이 끊길 수 있음"),
        ("04_multi_v2", text, {"model_id": MULTI}, "모델만 교체 · 텍스트 원본 (크레딧 2배)"),
        ("05_multi_v2_style0", text, {"model_id": MULTI, "style": 0.0},
         "위 + style 0 — style>0은 음절 탈락을 늘린다"),
        ("06_v3", text, {"model_id": V3}, "v3 — 대사 경로에서 이미 한국어 검증된 모델"),
        ("07_v3_break", brk, {"model_id": V3}, "v3 + <break> — break는 v3/multi_v2에서만 확실"),
        ("08_speed_1.0", text, {"speed": 1.0}, "speed 되돌리기 — speed<1.0이 뭉갬 원인일 수 있음"),
        ("09_stability_0.35", text, {"stability": 0.35}, "표현력↑ 테스트 — 씹힘이 늘 수 있음(상충 확인)"),
        ("10_stability_0.85", text, {"stability": 0.85}, "안정성↑ 테스트 — 씹힘↓ 대신 톤이 납작해질 수 있음"),
    ]


def dry(text):
    st = strategies(text)
    print("=" * 78)
    print("  드라이런 — 크레딧 0. 실제로 만들려면 --생성 을 붙이세요.")
    print("=" * 78)
    for name, send, ov, desc in st:
        print()
        print("  [%s]  %s" % (name, desc))
        print("     보낼 텍스트: %s" % send)
        if ov:
            print("     설정 덮어쓰기: %s" % ov)
    n = sum(len(x[1]) for x in st)
    total = sum(_billed(len(x[1]), x[2].get("model_id", FLASH)) for x in st)
    print()
    print("  전략 %d개 · 전송 %d자 · 과금환산 ~%d자 (월 197,000자 대비 %.2f%%)"
          % (len(st), n, total, total / 197000 * 100))


def run(text):
    import difflib

    import tts_pipeline as e
    import 발음검사 as C

    cfg = e.VOICE_MAP.get("나레이션")
    base = dict(cfg.get("settings") or {})
    os.makedirs(OUT, exist_ok=True)
    results = []

    for name, send, ov, desc in strategies(text):
        st = dict(base)
        st.update(ov)
        out = os.path.join(OUT, "%s.mp3" % name)
        print("  생성 %s ... " % name, end="", flush=True)
        try:
            _generate(e, cfg, st, send, out)
            print("ok")
        except Exception as ex:
            print("실패: %s: %s" % (type(ex).__name__, str(ex)[:90]))
            continue
        heard = "".join(w[2] for w in e.asr_words(out))
        # 채점은 '보낸 텍스트'가 아니라 '의도한 원본'과 비교한다
        ratio = difflib.SequenceMatcher(
            None, C.jamo(text), C.jamo(heard), autojunk=False
        ).ratio()
        got, exp = _survived(text, heard)
        f0, span = _expressiveness(out)
        results.append((ratio, name, heard, desc, f0, span, got, exp))

    results.sort(key=lambda x: -x[0])
    print()
    print("=" * 78)
    print("  자동 채점 — 두 축을 따로 보세요")
    print("    정확도 = 대본 대비 자모 일치도(높을수록 안 씹힘)")
    print("    피치폭 = 표현력 지표(세미톤). 참고: 무협도서관 10.9 / 형님 현재 10.4")
    print("=" * 78)
    print("   정확도   에게     F0    피치폭  전략")
    for ratio, name, heard, desc, f0, span, got, exp in results:
        f0_s = "%6.1f" % f0 if f0 else "     -"
        span_s = "%6.1fst" % span if span else "      -"
        eg_s = "%d/%d" % (got, exp) if exp else "  -"
        print("  %6.1f%%  %4s %s %s  %-18s %s"
              % (ratio * 100, eg_s, f0_s, span_s, name, desc))
        print("          들림: %s" % heard[:76])
    print()
    print("  경고: 정확도와 피치폭은 자주 상충합니다(표현력↑ = 씹힘↑).")
    print("        정확도를 먼저 확보하고, 그 다음 피치폭이 큰 쪽을 고르세요.")
    print("  경고: 반드시 '내 목소리로 들리는지'를 귀로 확인하세요 —")
    print("        모델을 바꾸면 클론 음색 자체가 달라질 수 있습니다.")
    print("  mp3: %s 폴더" % OUT)


if __name__ == "__main__":
    argv = sys.argv[1:]
    go = ("--생성" in argv) or ("--go" in argv)
    script = None
    for i, a in enumerate(argv):
        if a == "--대본" and i + 1 < len(argv):
            script = argv[i + 1]
    free = [a for a in argv if not a.startswith("--") and a != script]

    if script:
        (script_run if go else script_dry)(script)
    elif free:
        (run if go else dry)(free[0])
    else:
        print(__doc__)
        sys.exit(2)
