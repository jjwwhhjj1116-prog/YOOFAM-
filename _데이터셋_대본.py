# -*- coding: utf-8 -*-
"""데이터셋에 '장면 단위 나레이션 원문'을 함께 올린다.

metadata.csv 는 클립(3~15초) 단위 라벨이다. 학습기가 문장 경계를 다시 잡거나
사람이 검수할 때는 장면 통째 원문이 필요하다. 그걸 TSV 로 따로 뽑는다.

  · 현재 화  : 오디오/.audio_texts.json 의 N| 서명에서 대본 원문을 그대로 꺼낸다(정확)
  · 지난 화  : 오디오/ 가 덮어써져 원본이 없으므로 metadata.csv 의 클립 텍스트를
               장면별로 순서대로 이어 복원한다(복원본이라고 표시)

사용법:  python _데이터셋_대본.py ep02
"""
import io, os, sys, json, collections, subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(os.path.dirname(os.path.abspath(__file__)))

OUT = "voice_dataset"
SCENES = os.path.join(OUT, "scenes")
META = os.path.join(OUT, "metadata.csv")
MAN = os.path.join("오디오", ".audio_texts.json")


def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return "%.1f" % float(r.stdout.strip())
    except ValueError:
        return ""


def 클립모음():
    """metadata.csv → {화접두어: {장면번호: [(조각번호, 텍스트), …]}}"""
    d = collections.defaultdict(lambda: collections.defaultdict(list))
    if not os.path.exists(META):
        return d
    for line in io.open(META, encoding="utf-8"):
        if "|" not in line:
            continue
        name, text = line.split("|")[0], line.split("|")[1]
        parts = name.split("_")
        # ep02_narr_001_00  /  narr_001_00(접두어 없음 = 첫 화)
        if parts[0] == "narr":
            ep, seq, k = "ep01", parts[1], parts[2]
        else:
            ep, seq, k = parts[0], parts[2], parts[3]
        d[ep][seq].append((int(k), text.strip()))
    return d


def 쓰기(path, 머리, rows):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(머리)
        f.write("장면\t초\t글자\t나레이션 원문\n")
        for seq, sec, text in rows:
            t = text.replace("\t", " ").replace("\n", " ")
            f.write("%s\t%s\t%d\t%s\n" % (seq, sec, len(t), t))
    return len(rows)


def main():
    현재화 = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    os.makedirs(SCENES, exist_ok=True)
    모음 = 클립모음()
    만든것 = []

    # ── 현재 화: 매니페스트에서 원문 그대로 ───────────────────
    if 현재화 and os.path.exists(MAN):
        m = json.load(io.open(MAN, encoding="utf-8"))
        rows = []
        for seq in sorted(m):
            if not m[seq].startswith("N|"):
                continue
            mp3 = os.path.join("오디오", seq + ".mp3")
            if not (os.path.exists(mp3) and os.path.getsize(mp3) > 0):
                continue
            rows.append((seq, dur(mp3), m[seq].split("|")[1].strip()))
        p = os.path.join(SCENES, "%s_narration.tsv" % 현재화)
        n = 쓰기(p, "# %s 나레이션 장면 원문 — 대본에서 그대로 꺼낸 정본.\n"
                   "# 오디오: voice_dataset/wavs/%s_narr_<장면>_<조각>.wav\n"
                   "# 클립 단위 라벨은 metadata.csv 를 쓸 것.\n" % (현재화, 현재화), rows)
        만든것.append((p, n, "원문(정본)"))

    # ── 지난 화: metadata.csv 에서 복원 ──────────────────────
    for ep in sorted(모음):
        if ep == 현재화:
            continue
        rows = []
        for seq in sorted(모음[ep]):
            조각 = sorted(모음[ep][seq])
            rows.append((seq, "", " ".join(t for _, t in 조각)))
        p = os.path.join(SCENES, "%s_narration.tsv" % ep)
        n = 쓰기(p, "# %s 나레이션 장면 텍스트 — 클립 텍스트를 순서대로 이어 복원한 것.\n"
                   "# 원본 오디오가 덮어써져 대본 정본을 다시 꺼낼 수 없었다.\n"
                   "# 클립 경계에서 공백이 하나 더 들어갈 수 있다(내용은 동일).\n" % ep, rows)
        만든것.append((p, n, "복원본"))

    print("=" * 62)
    print("  장면 단위 나레이션 대본 생성")
    print("=" * 62)
    for p, n, 종류 in 만든것:
        print("  %-42s %3d장면  %s" % (p.replace("\\", "/"), n, 종류))
    return 0


if __name__ == "__main__":
    sys.exit(main())
