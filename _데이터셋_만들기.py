# -*- coding: utf-8 -*-
"""내 목소리(ElevenLabs 나레이션) 학습용 데이터셋 생성.

로컬 TTS 재현(XTTS / GPT-SoVITS / F5-TTS / Piper 등)에 바로 넣을 수 있도록
LJSpeech 형식(오디오 + metadata.csv)으로 만든다.

★ 순수 나레이션 장면만 쓴다. 대사가 섞인 장면(M|)은 Gemini/타입캐스트 목소리가
  들어가 있어서 학습에 넣으면 모델이 오염된다.
"""
import io, os, sys, json, shutil, subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

SRC_AUDIO = "오디오"
MANIFEST = os.path.join(SRC_AUDIO, ".audio_texts.json")
OUT = "voice_dataset"
WAVS = os.path.join(OUT, "wavs")


def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def main():
    if not os.path.exists(MANIFEST):
        print("매니페스트가 없습니다:", MANIFEST)
        return 1
    m = json.load(io.open(MANIFEST, encoding="utf-8"))

    os.makedirs(WAVS, exist_ok=True)
    rows, total_sec, skipped = [], 0.0, {"혼합": 0, "대사": 0, "파일없음": 0}

    for seq in sorted(m):
        sig = m[seq]
        if sig.startswith("M|"):
            skipped["혼합"] += 1
            continue
        if sig.startswith("D|"):
            skipped["대사"] += 1
            continue
        if not sig.startswith("N|"):
            continue
        text = sig.split("|")[1].strip()
        src = os.path.join(SRC_AUDIO, seq + ".mp3")
        if not (os.path.exists(src) and os.path.getsize(src) > 0):
            skipped["파일없음"] += 1
            continue

        # 22.05kHz 모노 wav — 대부분의 로컬 TTS 학습 파이프라인 기본값
        name = "narr_%s" % seq
        dst = os.path.join(WAVS, name + ".wav")
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-ac", "1", "-ar", "22050", dst],
            capture_output=True, text=True)
        if r.returncode != 0:
            print("  변환 실패 %s: %s" % (seq, (r.stderr or "")[:80]))
            continue
        d = dur(dst)
        total_sec += d
        rows.append((name, text, d))

    # LJSpeech 형식: 파일명|원문|정규화문  (구분자 파이프)
    with io.open(os.path.join(OUT, "metadata.csv"), "w", encoding="utf-8") as f:
        for name, text, _ in rows:
            f.write("%s|%s|%s\n" % (name, text, text))

    # 사람이 보기 쉬운 목록도 같이
    with io.open(os.path.join(OUT, "transcript.txt"), "w", encoding="utf-8") as f:
        for name, text, d in rows:
            f.write("[%s] (%.1fs) %s\n" % (name, d, text))

    chars = sum(len(t) for _, t, _ in rows)
    mn, sec = divmod(int(total_sec), 60)
    print("=" * 70)
    print("  데이터셋 생성 완료 → %s/" % OUT)
    print("=" * 70)
    print("  클립      : %d개" % len(rows))
    print("  총 길이   : %d분 %d초" % (mn, sec))
    print("  총 글자   : %d자" % chars)
    if rows:
        ds = sorted(d for _, _, d in rows)
        print("  클립 길이 : 최소 %.1fs / 중앙 %.1fs / 최대 %.1fs"
              % (ds[0], ds[len(ds) // 2], ds[-1]))
    print("  형식      : 22.05kHz 모노 wav + metadata.csv (LJSpeech)")
    print()
    print("  제외: 혼합장면 %d · 순수대사 %d · 파일없음 %d"
          % (skipped["혼합"], skipped["대사"], skipped["파일없음"]))
    sz = sum(os.path.getsize(os.path.join(WAVS, f)) for f in os.listdir(WAVS))
    print("  용량      : %.1f MB" % (sz / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
