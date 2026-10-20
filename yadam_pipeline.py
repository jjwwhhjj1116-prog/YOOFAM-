# -*- coding: utf-8 -*-
"""야담 & 민담 파이프라인 — 무협과 완전 격리.
   TTS 2가지 옵션(선택): ① 무료 edge-tts(자동 생성)  ② Vrew TTS 오디오 첨부(업로드).
   그 뒤 이미지/영상을 장면에 맞춰 CapCut 프로젝트로 (줌·자막·전환 자동).
   ★ 무협 코드(tts_pipeline)는 유틸만 재사용하고 절대 수정하지 않음.
"""
import os, re, json, asyncio, subprocess
import tts_pipeline as e          # dur, make_cues, write_srt, fit_video_to_audio 재사용(읽기 전용)
import capcut_export as ce        # CapCutDraft, prepare_brand, make_cover 재사용

YADAM_AUDIO = "야담오디오"        # 장면별 TTS mp3 (001.mp3 …)
YADAM_MEDIA = "야담소재"          # 업로드된 이미지/영상 (001.*, 002.* …)
YADAM_CLIP  = "야담클립"          # 이미지→영상 변환/영상 정렬 결과 (001_clip.mp4 …)
_MANIFEST   = ".yadam_texts.json"
US = 1_000_000

# edge-tts 한국어 음성 (무료)
EDGE_VOICES = {
    "여자 · SunHi (나레이터 추천)": "ko-KR-SunHiNeural",
    "남자 · InJoon":               "ko-KR-InJoonNeural",
    "남자 · Hyunsu":               "ko-KR-HyunsuMultilingualNeural",
}
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
VID_EXTS = (".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v")
AUD_EXTS = (".mp3", ".wav", ".m4a", ".mpga", ".aac", ".flac", ".ogg")

def _num(path):
    m = re.match(r'\s*0*(\d+)', os.path.basename(path))
    return f"{int(m.group(1)):03d}" if m else None

# ── edge-tts (무료) ─────────────────────────────────
def edge_synth(text, voice, out, rate="-8%"):
    """edge-tts로 mp3 생성. rate 음수=느리게(옛날이야기 톤). 실패 시 예외."""
    import edge_tts
    async def _go():
        await edge_tts.Communicate(text, voice, rate=rate).save(out)
    asyncio.run(_go())
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise RuntimeError(f"edge-tts 생성 실패: {os.path.basename(out)}")
    return out

# ── 매니페스트(장면별 텍스트) — 바뀐 장면만 재생성 ──
def _mpath(): return os.path.join(YADAM_AUDIO, _MANIFEST)
def _load_manifest():
    p = _mpath()
    try: return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    except Exception: return {}
def _save_manifest(m):
    os.makedirs(YADAM_AUDIO, exist_ok=True)
    json.dump(m, open(_mpath(), "w", encoding="utf-8"), ensure_ascii=False)

def audio_plan(scenes, source):
    """생성/건너뜀 미리 계산. source='edge'|'vrew'. → dict."""
    man = _load_manifest()
    to_gen = 0
    for (seq, kind, sp, emo, eng, text) in scenes:
        p = os.path.join(YADAM_AUDIO, f"{seq}.mp3")
        need = not (os.path.exists(p) and os.path.getsize(p) > 0 and man.get(seq) == text)
        if need: to_gen += 1
    n = len(scenes)
    return {"total": n, "existing": n - to_gen, "to_generate": to_gen}

# ── ① edge-tts로 장면별 오디오 생성 (무료) ──────────
def run_edge_tts(scenes, voice, rate="-8%", progress=None):
    os.makedirs(YADAM_AUDIO, exist_ok=True)
    man = _load_manifest()
    cur = {s[0] for s in scenes}
    for f in os.listdir(YADAM_AUDIO):                 # 범위 밖 정리
        if f.endswith(".mp3") and f[:-4] not in cur:
            os.remove(os.path.join(YADAM_AUDIO, f)); man.pop(f[:-4], None)
    n_gen = n_skip = 0
    for i, (seq, kind, sp, emo, eng, text) in enumerate(scenes):
        out = os.path.join(YADAM_AUDIO, f"{seq}.mp3")
        if os.path.exists(out) and os.path.getsize(out) > 0 and man.get(seq) == text:
            n_skip += 1; continue
        edge_synth(text, voice, out, rate=rate)
        man[seq] = text; _save_manifest(man); n_gen += 1
        if progress: progress((i + 1) / len(scenes), desc=f"edge-tts {i+1}/{len(scenes)}")
    _save_manifest(man)
    return n_gen, n_skip

# ── ② Vrew(또는 외부) 오디오 첨부 → 장면별로 배치 ──
def ingest_audio_files(scenes, files):
    """업로드된 오디오(파일명 앞 숫자로 매칭)를 야담오디오/NNN.mp3 로 변환 복사."""
    os.makedirs(YADAM_AUDIO, exist_ok=True)
    cur = {s[0] for s in scenes}
    seq2 = {}
    for p in (files or []):
        if not p.lower().endswith(AUD_EXTS): continue
        k = _num(p)
        if k in cur: seq2[k] = p
    done, miss = [], []
    for (seq, *_rest) in scenes:
        src = seq2.get(seq)
        out = os.path.join(YADAM_AUDIO, f"{seq}.mp3")
        if not src:
            miss.append(seq); continue
        subprocess.run(["ffmpeg", "-y", "-i", src, "-c:a", "libmp3lame", "-q:a", "2", out],
                       check=True, capture_output=True)
        done.append(seq)
    # 매니페스트는 텍스트 기반 재생성용인데 vrew는 외부오디오라 무효화(항상 최신으로 둠)
    man = {s[0]: s[5] for s in scenes if s[0] in done}
    _save_manifest(man)
    return done, miss

# ── 장면 소재(이미지/영상) → 오디오 길이의 클립 ─────
def _media_by_seq(media_files, scenes):
    cur = {s[0] for s in scenes}; m = {}
    for p in (media_files or []):
        k = _num(p)
        if k in cur and p.lower().endswith(IMG_EXTS + VID_EXTS):
            m[k] = p
    return m

# 이미지에 ffmpeg 카메라 모션(켄번즈)을 구울지. False면 기존대로 CapCut 줌 애니 사용.
#   ★ 주인님이 야담/경제학은 "CapCut 이미지 100% 애니(80%줌1/20%기울기)"로 지정 → 기본 False 유지.
#     True로 바꾸면 무협처럼 ffmpeg 모션이 구워지고, CapCut 프리셋은 자동 제외(이중 방지).
BAKE_MOTION = False

def image_to_clip(img, dur_s, out, w=1920, h=1080, fps=24, move=None):
    """정지 이미지 → dur_s초 무음 영상. move가 있으면 카메라 모션을 구움(무협과 동일 엔진).
       move=None(기본)이면 레터박스 정지영상 → CapCut에서 줌 애니 얹힘(기존 동작)."""
    if move:
        return e.image_to_clip(img, dur_s, out, fps=fps, size=f"{w}x{h}", move=move)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
    subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", img, "-t", f"{dur_s:.3f}",
                    "-r", str(fps), "-vf", vf, "-pix_fmt", "yuv420p", "-an", out],
                   check=True, capture_output=True)
    return out

def prepare_scenes(scenes, media_files, progress=None):
    """장면별로 (무음영상 + wav오디오 + cues) 준비. 이미지=정지영상화, 영상=오디오길이에 맞춤.
       → CapCutDraft.build 에 넣을 리스트. (오디오는 야담오디오/NNN.mp3 존재 전제)"""
    os.makedirs(YADAM_CLIP, exist_ok=True)
    mbys = _media_by_seq(media_files, scenes)
    built, missing = [], []
    for i, (seq, kind, sp, emo, eng, text) in enumerate(scenes):
        amp3 = os.path.join(YADAM_AUDIO, f"{seq}.mp3")
        media = mbys.get(seq)
        if not os.path.exists(amp3) or not media:
            missing.append(seq); continue
        A = e.dur(amp3)
        dv = os.path.abspath(os.path.join(YADAM_CLIP, f"{seq}_video.mp4"))
        da = os.path.abspath(os.path.join(YADAM_CLIP, f"{seq}_audio.wav"))
        if media.lower().endswith(VID_EXTS):          # 후킹 영상 → 오디오 길이에 정렬
            r = e.fit_video_to_audio(media, amp3, dv)
            clip_len = r["out_dur"]
            subprocess.run(["ffmpeg", "-y", "-i", dv, "-vn", "-c:a", "pcm_s16le", da],
                           check=True, capture_output=True)
        else:                                         # 이미지 → 오디오 길이만큼 정지영상(+선택 모션)
            image_to_clip(media, A, dv, move=(e.pick_move(seq) if BAKE_MOTION else None))
            clip_len = e.dur(dv)
            subprocess.run(["ffmpeg", "-y", "-i", amp3, "-c:a", "pcm_s16le", da],
                           check=True, capture_output=True)
        cues = e.make_cues(text, 0.0, clip_len, audio=amp3)
        item = {"video": dv, "audio": da, "dur_us": int(round(clip_len * 1e6)), "cues": cues}
        if BAKE_MOTION and not media.lower().endswith(VID_EXTS):
            item["no_anim"] = True                    # 모션을 구웠으면 CapCut 프리셋 제외
        built.append(item)
        if progress: progress((i + 1) / len(scenes), desc=f"소재 준비 {i+1}/{len(scenes)}")
    return built, missing

def build_capcut(out_dir, name, built, fps=24.0):
    draft = ce.CapCutDraft()
    info = draft.build(out_dir, name, built, fps=fps)
    if built:
        ce.make_cover(out_dir, built[0]["video"])
    return info
