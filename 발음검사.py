# -*- coding: utf-8 -*-
r"""씹힘 자동 검출 — 이미 만들어진 오디오/*.mp3 를 대본과 맞춰보고 '삼킨 곳'만 뽑아준다.

크레딧 0원. 파이프라인이 이미 쓰는 faster-whisper(small)를 그대로 재사용한다.
귀로 29분 다 듣지 말고, 여기서 점수 낮은 장면만 골라 들으면 된다.

    .venv\Scripts\python.exe 발음검사.py 대본.txt
    .venv\Scripts\python.exe 발음검사.py 대본.txt --임계 0.90   (더 깐깐하게)

원리:
  대본 텍스트는 이미 알고 있다. 그래서 ASR '텍스트'를 정답으로 보지 않고,
  대본 대비 '빠진 자모'만 본다. ASR 오탈자는 대개 '치환'으로 나타나고
  씹힘은 '삭제'로 나타나므로, 삭제만 세면 오탈자에 강해진다.
"""
import os, re, sys, json, difflib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import tts_pipeline as e

CHO  = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
JUNG = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")
JONG = [""] + list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")

def jamo(s):
    """한글 음절 → 자모 문자열(초/중/종). 한글 아닌 글자는 버린다."""
    out = []
    for ch in s:
        c = ord(ch)
        if 0xAC00 <= c <= 0xD7A3:
            i = c - 0xAC00
            out += [CHO[i // 588], JUNG[(i % 588) // 28], JONG[i % 28]]
        elif ch.isdigit():
            out.append(ch)
    return "".join(x for x in out if x)

def syls(s):
    return [ch for ch in s if 0xAC00 <= ord(ch) <= 0xD7A3 or ch.isdigit()]

def check(script_path, thresh=0.93, model=None):
    if model:
        e.WHISPER_MODEL = model
    segs = e.load_segments(script_path)
    rows = []
    print(f"  대본 {len(segs)}장면 · 오디오 폴더 {e.OUT_AUDIO} · ASR={e.WHISPER_MODEL}\n")
    for i, s in enumerate(segs, 1):
        seq = s["seq"]
        mp3 = os.path.join(e.OUT_AUDIO, f"{seq}.mp3")
        if not (os.path.exists(mp3) and os.path.getsize(mp3) > 0):
            continue
        words = e.asr_words(mp3)
        if not words:
            rows.append((0.0, seq, "ASR실패", s["text"][:40], []))
            continue
        heard = "".join(w[2] for w in words)
        want_j, got_j = jamo(s["text"]), jamo(heard)
        if not want_j:
            continue
        sm = difflib.SequenceMatcher(None, want_j, got_j, autojunk=False)
        ratio = sm.ratio()
        # 삭제 구간만 수집(= 삼킨 곳). 치환/삽입은 ASR 오탈자일 가능성이 커서 제외.
        missing = []
        for tag, a1, a2, b1, b2 in sm.get_opcodes():
            if tag == "delete" and (a2 - a1) >= 2:
                # 자모 위치 → 음절 위치로 환산해 앞뒤 문맥까지 보여준다
                sy = syls(s["text"])
                lo, hi = a1 // 3, max(a1 // 3 + 1, (a2 + 2) // 3)
                ctx = "".join(sy[max(0, lo - 4):hi + 4])
                gone = "".join(sy[lo:hi])
                if gone.strip():
                    missing.append((gone, ctx))
        rows.append((ratio, seq, heard, s["text"], missing))
        sys.stdout.write(f"\r  검사 {i}/{len(segs)} …"); sys.stdout.flush()
    print("\r" + " " * 40 + "\r", end="")

    rows.sort(key=lambda r: r[0])
    bad = [r for r in rows if r[0] < thresh or r[4]]
    print("=" * 72)
    print(f"  검사 {len(rows)}장면 · 의심 {len(bad)}장면 (임계 {thresh:.2f})")
    print("=" * 72)
    for ratio, seq, heard, want, missing in bad:
        print(f"\n  [{seq}] 일치도 {ratio*100:.1f}%")
        print(f"     대본: {want[:90]}")
        print(f"     들림: {heard[:90]}")
        for gone, ctx in missing[:4]:
            print(f"     ⚠ 빠진 듯: '{gone}'   (…{ctx}…)")
    if not bad:
        print("\n  🎉 삼킨 곳으로 의심되는 장면 없음.")
    else:
        print(f"\n  → 위 장면만 들어보세요. 재현되면 발음교정.json 의 '치환'에 추가하시면 됩니다.")
        print(f"     규칙 파일: {os.path.basename('발음교정.json')}")
    return rows

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    th = 0.93; mdl = None
    for i, a in enumerate(sys.argv):
        if a == "--임계" and i + 1 < len(sys.argv): th = float(sys.argv[i + 1])
        if a == "--모델" and i + 1 < len(sys.argv): mdl = sys.argv[i + 1]
    if not args:
        print("사용법: python 발음검사.py <대본.txt> [--임계 0.93] [--모델 medium]")
        sys.exit(2)
    check(args[0], th, mdl)
