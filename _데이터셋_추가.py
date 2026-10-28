# -*- coding: utf-8 -*-
"""내 목소리 학습 데이터셋에 '한 화'를 덧붙인다 (기존 데이터 보존).

_데이터셋_분할.py 는 voice_dataset/ 를 처음부터 다시 만든다. 그런데 오디오/ 폴더는
화가 바뀔 때 같은 번호(001.mp3 …)로 덮어써지므로, 이전 화의 클립은 voice_dataset/ 에
있는 것이 유일한 사본이다. 그래서 다시 만들면 이전 화가 날아간다.

이 스크립트는 **덧붙이기만** 한다:
  · 파일명에 화 접두어를 붙여 번호 충돌을 막는다 (ep02_narr_001_00.wav)
  · metadata.csv / transcript.txt 는 기존 줄을 보존하고 뒤에 추가한다
  · 이미 있는 이름은 건너뛴다 (다시 돌려도 안전)

★ 텍스트는 ASR 결과가 아니라 '대본 원문' — 오탈자 없는 정답 라벨.
★ 순수 나레이션 장면(N|)만. 대사 섞인 장면은 다른 엔진 목소리가 들어가 학습을 오염시킨다.

사용법:  python _데이터셋_추가.py ep02
"""
import io, os, sys, json, subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
import tts_pipeline as e

SRC = "오디오"
OUT = "voice_dataset"
WAVS = os.path.join(OUT, "wavs")
META = os.path.join(OUT, "metadata.csv")
TRANS = os.path.join(OUT, "transcript.txt")
MIN_S, MAX_S = 3.0, 15.0


def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def merge(cues):
    """cue 들을 MIN_S~MAX_S 덩어리로 묶는다. (_데이터셋_분할.py 와 동일 규칙)"""
    out, cur_s, cur_e, cur_t = [], None, None, []
    for cs, ce, txt in cues:
        if cur_s is None:
            cur_s, cur_e, cur_t = cs, ce, [txt]
            continue
        if (ce - cur_s) <= MAX_S:
            cur_e = ce
            cur_t.append(txt)
        else:
            out.append((cur_s, cur_e, " ".join(cur_t)))
            cur_s, cur_e, cur_t = cs, ce, [txt]
    if cur_s is not None:
        out.append((cur_s, cur_e, " ".join(cur_t)))
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
    prefix = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not prefix:
        print("화 접두어가 필요합니다.  예:  python _데이터셋_추가.py ep02")
        return 2

    man_path = os.path.join(SRC, ".audio_texts.json")
    if not os.path.exists(man_path):
        print("매니페스트가 없습니다:", man_path)
        return 1
    m = json.load(io.open(man_path, encoding="utf-8"))
    os.makedirs(WAVS, exist_ok=True)

    기존 = set()
    if os.path.exists(META):
        for line in io.open(META, encoding="utf-8"):
            if "|" in line:
                기존.add(line.split("|", 1)[0])
    print("  기존 클립 %d개 — 보존합니다." % len(기존))

    seqs = [s for s in sorted(m) if m[s].startswith("N|")]
    print("  순수 나레이션 장면 %d개 정렬 시작 (ASR — 몇 분 걸립니다)" % len(seqs))

    rows, n_scene, 건너뜀 = [], 0, 0
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
            name = "%s_narr_%s_%02d" % (prefix, seq, k)
            if name in 기존:
                건너뜀 += 1
                continue
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
    print("\r" + " " * 56 + "\r", end="")

    # 기존 줄 보존 + 뒤에 추가
    with io.open(META, "a", encoding="utf-8") as f:
        for name, txt, _ in rows:
            f.write("%s|%s|%s\n" % (name, txt, txt))
    with io.open(TRANS, "a", encoding="utf-8") as f:
        for name, txt, d in rows:
            f.write("[%s] (%.1fs) %s\n" % (name, d, txt))

    tot = sum(d for _, _, d in rows)
    ds = sorted(d for _, _, d in rows)
    mn, sec = divmod(int(tot), 60)
    print("=" * 66)
    print("  '%s' 추가 완료 → %s/" % (prefix, OUT))
    print("=" * 66)
    print("  원본 장면   : %d개 (순수 나레이션)" % n_scene)
    print("  새 클립     : %d개   (중복 건너뜀 %d)" % (len(rows), 건너뜀))
    print("  새 길이     : %d분 %d초" % (mn, sec))
    print("  새 글자     : %d자" % sum(len(t) for _, t, _ in rows))
    if ds:
        print("  클립 길이   : 최소 %.1fs / 중앙 %.1fs / 최대 %.1fs"
              % (ds[0], ds[len(ds) // 2], ds[-1]))
        print("  3~15초 비율 : %.0f%%" % (100 * sum(1 for d in ds if 3 <= d <= 15) / len(ds)))
    전체 = len([x for x in os.listdir(WAVS) if x.endswith(".wav")])
    sz = sum(os.path.getsize(os.path.join(WAVS, f)) for f in os.listdir(WAVS))
    print("-" * 66)
    print("  데이터셋 전체: 클립 %d개 · %.1f MB" % (전체, sz / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
