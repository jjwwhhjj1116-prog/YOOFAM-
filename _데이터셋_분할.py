# -*- coding: utf-8 -*-
"""내 목소리 학습 데이터셋 — 문장 단위 분할본 생성.

장면 통째(중앙값 23초, 최대 60초)는 로컬 TTS 학습기에 너무 길다.
파이프라인의 make_cues()가 대본 텍스트를 실제 발화 시각에 정렬해 주므로,
그 cue 들을 3~15초 덩어리로 묶어서 잘라낸다.

★ 텍스트는 ASR 결과가 아니라 '대본 원문'을 쓴다 → 오탈자 없는 정답 라벨.
★ 순수 나레이션 장면만. 대사 섞인 장면은 제외.
"""
import io, os, sys, json, subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import tts_pipeline as e

SRC = "오디오"
OUT = "voice_dataset"
WAVS = os.path.join(OUT, "wavs")
MIN_S, MAX_S = 3.0, 15.0


def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def merge(cues):
    """cue 들을 MIN_S~MAX_S 덩어리로 묶는다."""
    out, cur_s, cur_e, cur_t = [], None, None, []
    for cs, ce, txt in cues:
        if cur_s is None:
            cur_s, cur_e, cur_t = cs, ce, [txt]
            continue
        if (ce - cur_s) <= MAX_S:
            cur_e, _ = ce, cur_t.append(txt)
        else:
            out.append((cur_s, cur_e, " ".join(cur_t)))
            cur_s, cur_e, cur_t = cs, ce, [txt]
    if cur_s is not None:
        out.append((cur_s, cur_e, " ".join(cur_t)))
    # 너무 짧은 꼬리는 앞 덩어리에 붙인다
    merged = []
    for seg in out:
        if merged and (seg[1] - seg[0]) < MIN_S:
            ps, pe, pt = merged[-1]
            if seg[1] - ps <= MAX_S + 3:
                merged[-1] = (ps, seg[1], pt + " " + seg[2])
                continue
        merged.append(seg)
    return merged


def main():
    m = json.load(io.open(os.path.join(SRC, ".audio_texts.json"), encoding="utf-8"))
    os.makedirs(WAVS, exist_ok=True)
    rows, n_scene = [], 0

    seqs = [s for s in sorted(m) if m[s].startswith("N|")]
    for i, seq in enumerate(seqs, 1):
        src = os.path.join(SRC, seq + ".mp3")
        if not (os.path.exists(src) and os.path.getsize(src) > 0):
            continue
        text = m[seq].split("|")[1].strip()
        length = dur(src)
        if length <= 0:
            continue
        sys.stdout.write("\r  정렬 %d/%d (%s) …" % (i, len(seqs), seq))
        sys.stdout.flush()
        try:
            cues = e.make_cues(text, 0.0, length, audio=src)
        except Exception as ex:
            print("\n  cue 실패 %s: %s" % (seq, type(ex).__name__))
            continue
        if not cues:
            continue
        for k, (cs, ce, txt) in enumerate(merge(cues)):
            txt = (txt or "").strip()
            if not txt or (ce - cs) < 1.0:
                continue
            name = "narr_%s_%02d" % (seq, k)
            dst = os.path.join(WAVS, name + ".wav")
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", "%.3f" % cs,
                 "-t", "%.3f" % (ce - cs), "-i", src,
                 "-ac", "1", "-ar", "22050", dst],
                capture_output=True, text=True)
            if r.returncode != 0 or not os.path.exists(dst):
                continue
            rows.append((name, txt, dur(dst)))
        n_scene += 1
    print("\r" + " " * 50 + "\r", end="")

    with io.open(os.path.join(OUT, "metadata.csv"), "w", encoding="utf-8") as f:
        for name, txt, _ in rows:
            f.write("%s|%s|%s\n" % (name, txt, txt))
    with io.open(os.path.join(OUT, "transcript.txt"), "w", encoding="utf-8") as f:
        for name, txt, d in rows:
            f.write("[%s] (%.1fs) %s\n" % (name, d, txt))

    tot = sum(d for _, _, d in rows)
    ds = sorted(d for _, _, d in rows)
    mn, sec = divmod(int(tot), 60)
    print("=" * 70)
    print("  문장 단위 분할 완료 → %s/" % OUT)
    print("=" * 70)
    print("  원본 장면 : %d개" % n_scene)
    print("  클립      : %d개" % len(rows))
    print("  총 길이   : %d분 %d초" % (mn, sec))
    print("  총 글자   : %d자" % sum(len(t) for _, t, _ in rows))
    if ds:
        print("  클립 길이 : 최소 %.1fs / 중앙 %.1fs / 최대 %.1fs" % (ds[0], ds[len(ds)//2], ds[-1]))
        print("  3~15초 비율: %.0f%%" % (100 * sum(1 for d in ds if 3 <= d <= 15) / len(ds)))
    sz = sum(os.path.getsize(os.path.join(WAVS, f)) for f in os.listdir(WAVS))
    print("  용량      : %.1f MB" % (sz / 1e6))


if __name__ == "__main__":
    main()
