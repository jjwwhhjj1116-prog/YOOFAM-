# -*- coding: utf-8 -*-
"""데이터셋에 '자막(SRT)'을 한 화씩 담는다 — 실제 나레이션 오디오에서 직접 생성.

★ 왜 자막/ 폴더를 복사하지 않는가
  자막/ 은 '현재 작업 중인 대본' 기준으로 다시 생성된다. 오디오/ 에 들어 있는
  음성이 만들어진 뒤 대본을 고치면, 자막/ 의 내용은 그 음성과 짝이 맞지 않는다.
  실제로 남궁회귀에서 199장면 중 123장면이 전혀 다른 문장이었다.
  그걸 학습 라벨로 올리면 틀린 짝이 된다.

  그래서 이 스크립트는 오디오/<장면>.mp3 와 매니페스트의 대본 원문을 가지고
  make_cues() 로 다시 정렬해 SRT 를 만든다. 음성과 글자가 반드시 일치한다.

순수 나레이션 장면(N|)만 담는다 — 대사 섞인 장면은 다른 엔진 목소리가 들어 있다.

사용법:  python _데이터셋_자막.py ng01
"""
import io, os, sys, json, subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
import tts_pipeline as e

SRC = "오디오"
OUT = "voice_dataset"
MAN = os.path.join(SRC, ".audio_texts.json")


def dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def ts(sec):
    """SRT 타임코드 00:00:00,000"""
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        ms = 999
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def _매니페스트_지문(path):
    """매니페스트 내용의 해시. 작업 중에 바뀌면(= TTS 가 돌아 오디오가 교체되면)
       글자와 음성이 다른 버전으로 짝지어진다. 실제로 그렇게 178/198 장면이 어긋났다."""
    import hashlib
    try:
        return hashlib.sha1(io.open(path, "rb").read()).hexdigest()
    except Exception:
        return None


def _지문_검사(path, before, 무엇):
    """작업 전후 지문을 비교. 달라졌으면 결과를 믿을 수 없으니 알리고 실패시킨다."""
    after = _매니페스트_지문(path)
    if before != after:
        print()
        print("=" * 64)
        print("  [중단] 작업 중에 오디오가 다시 생성됐습니다.")
        print("  매니페스트가 바뀌어서 %s의 글자와 음성이 어긋납니다." % 무엇)
        print("  앱에서 TTS 제작(②)이 끝난 뒤에 다시 돌리세요.")
        print("=" * 64)
        return False
    return True


def main():
    화 = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not 화:
        print("화 접두어가 필요합니다.  예:  python _데이터셋_자막.py ng01")
        return 2
    if not os.path.exists(MAN):
        print("매니페스트가 없습니다:", MAN)
        return 1

    지문0 = _매니페스트_지문(MAN)             # 작업 중 오디오 교체 감지용
    m = json.load(io.open(MAN, encoding="utf-8"))
    dst = os.path.join(OUT, "subtitles", 화)
    os.makedirs(dst, exist_ok=True)

    seqs = [s for s in sorted(m) if m[s].startswith("N|")]
    print("  순수 나레이션 %d장면 — 오디오에서 자막 생성 (ASR 정렬, 몇 분 걸립니다)"
          % len(seqs))

    만듦, 블록, 실패 = 0, 0, []
    목록 = []
    for i, seq in enumerate(seqs, 1):
        src = os.path.join(SRC, seq + ".mp3")
        if not (os.path.exists(src) and os.path.getsize(src) > 0):
            실패.append(seq + "(오디오없음)")
            continue
        text = m[seq].split("|")[1].strip()
        length = dur(src)
        if length <= 0:
            실패.append(seq + "(길이0)")
            continue
        sys.stdout.write("\r  정렬 %d/%d (%s) …" % (i, len(seqs), seq))
        sys.stdout.flush()
        try:
            cues = e.make_cues(text, 0.0, length, audio=src)
        except Exception as ex:
            실패.append("%s(%s)" % (seq, type(ex).__name__))
            continue
        if not cues:
            실패.append(seq + "(cue없음)")
            continue
        with io.open(os.path.join(dst, seq + ".srt"), "w", encoding="utf-8") as f:
            for k, (cs, ce, txt) in enumerate(cues, 1):
                txt = (txt or "").strip()
                if not txt:
                    continue
                f.write("%d\n%s --> %s\n%s\n\n" % (k, ts(cs), ts(ce), txt))
        블록 += len(cues)
        만듦 += 1
        목록.append((seq, len(cues), "%.1f" % length, text))
    print("\r" + " " * 56 + "\r", end="")

    if not _지문_검사(MAN, 지문0, "자막"):
        import shutil as _sh
        _sh.rmtree(dst, ignore_errors=True)
        print("  → 이번에 만든 자막 폴더를 지웠습니다.")
        return 3

    idx = os.path.join(dst, "_목록.tsv")
    with io.open(idx, "w", encoding="utf-8") as f:
        f.write("# %s 나레이션 자막 목록 — 오디오에서 직접 정렬해 만든 것(음성과 일치 보장).\n" % 화)
        f.write("# 자막 : voice_dataset/subtitles/%s/<장면>.srt\n" % 화)
        f.write("# 오디오 클립 : voice_dataset/wavs/%s_narr_<장면>_<조각>.wav\n" % 화)
        f.write("장면\t자막줄\t초\t나레이션 원문\n")
        for seq, n, sec, t in 목록:
            f.write("%s\t%d\t%s\t%s\n" % (seq, n, sec, t.replace("\t", " ")))

    sz = sum(os.path.getsize(os.path.join(dst, f)) for f in os.listdir(dst))
    print("=" * 64)
    print("  '%s' 자막 생성 완료 → %s/" % (화, dst.replace("\\", "/")))
    print("=" * 64)
    print("  SRT 파일   : %d개" % 만듦)
    print("  자막 블록  : %d줄" % 블록)
    print("  제외(혼합) : %d장면  ← 대사가 섞여 나레이션 라벨로 못 씀"
          % (len(m) - len(seqs)))
    print("  실패       : %d  %s" % (len(실패), ", ".join(실패[:10])))
    print("  목록표     : %s" % idx.replace("\\", "/"))
    print("  용량       : %.1f KB" % (sz / 1e3))
    return 0


if __name__ == "__main__":
    sys.exit(main())
