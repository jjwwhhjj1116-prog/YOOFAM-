# -*- coding: utf-8 -*-
"""일본 경제학 파이프라인 — 무협/야담과 완전 격리.
   후킹=영상, 나머지=이미지. 일본어 나레이션(ElevenLabs 또는 edge-tts JA).
   CapCut: CTA(후킹 뒤) + 경제학로고(우상, 무협과 동일 위치) + 후킹/본론 음악(-25dB)
           + 이미지 100% 애니(80줌1/20기울기), 후킹영상엔 애니 없음.
   ★ tts_pipeline/capcut_export/yadam_pipeline 유틸만 재사용, 수정 안 함.
"""
import os, re, json, asyncio, subprocess
import tts_pipeline as e
import capcut_export as ce
import yadam_pipeline as yp        # image_to_clip 재사용

KEIZAI_AUDIO = "경제학오디오"
KEIZAI_CLIP  = "경제학클립"
BRAND_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "경제학 필수")
BRAND_TPL    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "필수", "brand_template.json")
_MANIFEST    = ".keizai_texts.json"
US = 1_000_000
IMG_EXTS = yp.IMG_EXTS
VID_EXTS = yp.VID_EXTS

# 나레이션 TTS 방식 (2택)
TTS_ELEVEN = "① ElevenLabs 일본어 (유료·고품질)"
TTS_EDGE   = "② edge-tts 일본어 (무료)"
EDGE_VOICES_JA = {"여자 · Nanami": "ja-JP-NanamiNeural", "남자 · Keita": "ja-JP-KeitaNeural"}

def _num(path):
    m = re.match(r'\s*0*(\d+)', os.path.basename(path))
    return f"{int(m.group(1)):03d}" if m else None

# ── 매니페스트 (나레이션 텍스트 기반 — 화자/역할 없음) ──
def _mpath(): return os.path.join(KEIZAI_AUDIO, _MANIFEST)
def _load_manifest():
    p = _mpath()
    try: return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    except Exception: return {}
def _save_manifest(m):
    os.makedirs(KEIZAI_AUDIO, exist_ok=True)
    json.dump(m, open(_mpath(), "w", encoding="utf-8"), ensure_ascii=False)

def _sig(seg, source, voice):
    return f"{source}|{voice}|{seg['text']}"

def audio_plan(scenes, source, voice):
    man = _load_manifest()
    to_gen = sum(1 for s in scenes
                 if not (os.path.exists(os.path.join(KEIZAI_AUDIO, f"{s[0]}.mp3"))
                         and os.path.getsize(os.path.join(KEIZAI_AUDIO, f"{s[0]}.mp3")) > 0
                         and man.get(s[0]) == _sig({"text": s[5]}, source, voice)))
    n = len(scenes)
    return {"total": n, "existing": n - to_gen, "to_generate": to_gen}

# ── 나레이션 생성 ──
def _edge_ja(text, voice, out, rate="-4%"):
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(out))
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise RuntimeError(f"edge-tts 생성 실패: {os.path.basename(out)}")
    return out

def _eleven_ja(text, out):
    """ElevenLabs 멀티링궐(eleven_flash_v2_5) + language_code=ja. 나레이션 voice 재사용."""
    from elevenlabs.types.voice_settings import VoiceSettings
    cfg = e.VOICE_MAP.get("나레이션", {}); st = cfg.get("settings", {})
    audio = e.EL.text_to_speech.convert(
        text=text, voice_id=cfg["voice_id"], model_id="eleven_flash_v2_5",
        output_format="mp3_44100_128", language_code="ja",
        voice_settings=VoiceSettings(
            stability=st.get("stability"), similarity_boost=st.get("similarity_boost"),
            style=st.get("style"), use_speaker_boost=st.get("use_speaker_boost"), speed=st.get("speed")))
    with open(out, "wb") as f:
        for c in audio:
            f.write(c)
    if os.path.getsize(out) == 0:
        raise RuntimeError(f"ElevenLabs 생성 실패: {os.path.basename(out)}")
    return out

def run_narration(scenes, source, voice, progress=None):
    """scenes:(seq,kind,sp,emo,eng,text). source=TTS_ELEVEN|TTS_EDGE. → (생성수, 건너뜀수)."""
    os.makedirs(KEIZAI_AUDIO, exist_ok=True)
    man = _load_manifest()
    cur = {s[0] for s in scenes}
    for f in os.listdir(KEIZAI_AUDIO):
        if f.endswith(".mp3") and f[:-4] not in cur:
            os.remove(os.path.join(KEIZAI_AUDIO, f)); man.pop(f[:-4], None)
    n_gen = n_skip = 0
    for i, (seq, kind, sp, emo, eng, text) in enumerate(scenes):
        out = os.path.join(KEIZAI_AUDIO, f"{seq}.mp3")
        sig = _sig({"text": text}, source, voice)
        if os.path.exists(out) and os.path.getsize(out) > 0 and man.get(seq) == sig:
            n_skip += 1; continue
        if source == TTS_EDGE:
            _edge_ja(text, EDGE_VOICES_JA.get(voice, "ja-JP-NanamiNeural"), out)
        else:
            _eleven_ja(text, out)
        man[seq] = sig; _save_manifest(man); n_gen += 1
        if progress: progress((i + 1) / len(scenes), desc=f"나레이션 {i+1}/{len(scenes)}")
    _save_manifest(man)
    return n_gen, n_skip

# ── 장면 소재(후킹=영상, 나머지=이미지) → 클립 ──
def prepare_scenes(scenes, media_files, progress=None):
    os.makedirs(KEIZAI_CLIP, exist_ok=True)
    mbys = {}
    for p in (media_files or []):
        k = _num(p)
        if k and p.lower().endswith(IMG_EXTS + VID_EXTS):
            mbys[k] = p
    built, missing = [], []
    for i, (seq, kind, sp, emo, eng, text) in enumerate(scenes):
        amp3 = os.path.join(KEIZAI_AUDIO, f"{seq}.mp3"); media = mbys.get(seq)
        if not os.path.exists(amp3) or not media:
            missing.append(seq); continue
        A = e.dur(amp3)
        dv = os.path.abspath(os.path.join(KEIZAI_CLIP, f"{seq}_video.mp4"))
        da = os.path.abspath(os.path.join(KEIZAI_CLIP, f"{seq}_audio.wav"))
        is_video = media.lower().endswith(VID_EXTS)
        if is_video:                                   # 후킹 영상 → 오디오 길이에 정렬(줌 없음)
            r = e.fit_video_to_audio(media, amp3, dv); clip_len = r["out_dur"]
            subprocess.run(["ffmpeg", "-y", "-i", dv, "-vn", "-c:a", "pcm_s16le", da],
                           check=True, capture_output=True)
        else:                                          # 이미지 → 정지영상(줌 애니 대상)
            # BAKE_MOTION=True면 ffmpeg 카메라 모션을 굽고 CapCut 프리셋은 제외(이중 방지).
            yp.image_to_clip(media, A, dv,
                             move=(e.pick_move(seq) if yp.BAKE_MOTION else None))
            clip_len = e.dur(dv)
            subprocess.run(["ffmpeg", "-y", "-i", amp3, "-c:a", "pcm_s16le", da],
                           check=True, capture_output=True)
        cues = e.make_cues(text, 0.0, clip_len, audio=amp3)
        built.append({"video": dv, "audio": da, "dur_us": int(round(clip_len * 1e6)),
                      # 후킹영상=애니 제외. 이미지도 모션을 구웠으면(BAKE_MOTION) 제외.
                      "cues": cues, "no_anim": is_video or yp.BAKE_MOTION})
        if progress: progress((i + 1) / len(scenes), desc=f"소재 준비 {i+1}/{len(scenes)}")
    return built, missing

# ── 경제학 브랜드(CTA·로고·음악, 인트로/아웃트로/창작고지 없음) ──
def load_brand(cta_after):
    cta  = os.path.join(BRAND_DIR, "CTA(일본경제학).mp4")
    logo = os.path.join(BRAND_DIR, "경제학로고.png")
    hook = os.path.join(BRAND_DIR, "후킹 음악.wav")
    body = os.path.join(BRAND_DIR, "본론 음악.wav")
    need = {"CTA(일본경제학).mp4": cta, "경제학로고.png": logo,
            "후킹 음악.wav": hook, "본론 음악.wav": body, "brand_template.json": BRAND_TPL}
    missing = [k for k, p in need.items() if not os.path.exists(p)]
    if missing:
        return None, missing
    proto = json.load(open(BRAND_TPL, encoding="utf-8"))
    dur_us = lambda p: int(round(e.dur(p) * 1e6))
    brand = {"proto": proto, "intro": None, "outro": None, "disclaimer": None,
             "cta_after": cta_after, "logo": {"path": logo},
             "bgm": {"후킹": hook, "본편": [body], "반전": None,
                     "durs": {hook: dur_us(hook), body: dur_us(body)}},
             "_cta_src": cta}
    return brand, []

def build_capcut(out_dir, name, built, brand, fps=24.0):
    # CTA를 무음영상+wav로 추출(무협 place_brand 방식)
    if brand.get("_cta_src"):
        os.makedirs(os.path.join(out_dir, "assets"), exist_ok=True)
        b = ce.prepare_brand(os.path.join(out_dir, "assets"), {"cta": brand.pop("_cta_src")})
        brand["cta"] = b["cta"]
    draft = ce.CapCutDraft()
    info = draft.build(out_dir, name, built, fps=fps, brand=brand, anim_ratio=1.0)  # 이미지 100% 애니
    if built:
        ce.make_cover(out_dir, built[0]["video"])
    return info
